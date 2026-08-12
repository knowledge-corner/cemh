"""
Closing a clinic day, and the report that goes with it (KAN-48, KAN-49).

Two things happen at the end of a day and they are deliberately one operation
here:

* the day's leftovers are dealt with — bookings nobody arrived for become
  no-shows or lapse, and anything the doctor saw but nobody billed is left
  alone, because money still owed is a task rather than an untidy row;
* a report goes out with three sheets — billed, cancelled, no-show.

Making them one operation is what lets KAN-49 ask a single question the next
morning: *was yesterday signed off?* Two separate actions would give two answers
and no way to tell which one the receptionist forgot.

**The sweep never touches a consultation that has not been billed.** Sweeping
those away would destroy the only record that money is owed, which is precisely
what KAN-48 exists to recover. They stay on the board until somebody bills them,
and the sign-off says how many are still outstanding rather than pretending the
day is clean.
"""

import csv
import io
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from django.db.models import Max

from .models import DaySignOff, DoctorSchedule, InvalidTransition, Visit, VisitStatus

logger = logging.getLogger(__name__)

#: Before this hour, "yesterday" is still tonight's work. A clinic running an
#: evening list finishes after midnight, and a sweep at 00:05 would cancel
#: patients who are sitting in the waiting room.
SWEEP_AFTER_HOUR = 5

#: What the sweep does with each state it finds left open.
#:
#: A booking nobody confirmed lapsed; one the patient confirmed and then missed
#: is a no-show, and the difference matters when the clinic later asks how often
#: people fail to turn up.
#:
#: BILLED closes as COMPLETED. The patient paid and went home yesterday; the
#: only thing missing is somebody pressing the last button. Without this the
#: visit sat on the "still open" strip for ever and each press of Close them off
#: reported it as "a consultation that has not been billed" — which it plainly
#: was not. A board that can never be cleared, giving an untrue reason.
#:
#: CONSULTED is absent on purpose — see the module note. That one really is
#: money owed, and it is the one thing here that must survive the sweep.
SWEEP_TARGETS = {
    VisitStatus.BOOKED: VisitStatus.CANCELLED,
    VisitStatus.CONFIRMED: VisitStatus.NO_SHOW,
    VisitStatus.ARRIVED: VisitStatus.CANCELLED,
    VisitStatus.IN_CABIN: VisitStatus.CONSULTED,
    VisitStatus.BILLED: VisitStatus.COMPLETED,
}

SHEETS = ("billed", "cancelled", "no_show")

COLUMNS = [
    "time", "patient_id", "patient", "doctor", "type", "status",
    "charged", "collected", "outstanding", "receipt",
]


#: Mail backends that accept a message and deliver it nowhere.
#:
#: The clinic's own docker-compose.yml runs the development settings, whose
#: backend prints to the container log. Django reports that as a successful
#: send, so without this check the receptionist is told "report sent to
#: contact@cemhcare.com" while nothing left the building — and nobody finds out
#: until the day somebody asks the accountant why no day sheets ever arrived.
#:
#: locmem is deliberately absent: it is the test backend, standing in for real
#: delivery so the suite can assert what was sent.
UNDELIVERED_BACKENDS = ("console", "dummy", "filebased")


def is_really_delivered():
    """False when the configured backend accepts mail and discards it."""
    backend = getattr(settings, "EMAIL_BACKEND", "") or ""
    return not any(name in backend for name in UNDELIVERED_BACKENDS)


def email_enabled():
    """
    Whether the sign-off should try to send the day sheet.

    Separate from :func:`is_enabled`. Closing the day and reporting on it are
    two different things, and the clinic wants the first without the second
    until there is a mail server — switching the whole feature off to silence
    one warning would take the sweep and the lock with it.
    """
    return bool(getattr(settings.CLINIC, "SIGN_OFF_EMAIL_ENABLED", False))


def recipients():
    """Who the report goes to. Empty means nobody has configured it yet."""
    configured = getattr(settings.CLINIC, "SIGN_OFF_EMAILS", "") or ""
    found = [address.strip() for address in configured.split(",") if address.strip()]
    if found:
        return found
    fallback = getattr(settings.CLINIC, "CLINIC_EMAIL", "") or ""
    return [fallback] if fallback else []


def is_enabled():
    """
    Whether the clinic is being asked to sign its days off at all.

    Off at the clinic's request. Everything KAN-48 and KAN-49 built stays here
    and stays tested — this is a switch, not a deletion, because the feature is
    wanted once there is a mail server for the report to go to.

    Checked in one place so it cannot be honoured by the alert and forgotten by
    the rule underneath it, which is how a "disabled" feature still blocks
    somebody's morning.
    """
    return bool(getattr(settings.CLINIC, "DAY_SIGN_OFF_ENABLED", False))


def is_due(day=None, now=None):
    """
    Is there a previous clinic day still waiting to be signed off?

    Answered from :class:`DaySignOff` rather than from the visits, because a day
    on which nothing needed billing and a day nobody closed look identical in
    the visit table — and only one of them is a problem.

    Always ``None`` while the feature is switched off. Both the alert and the
    hold on arrivals ask this one question, so one answer turns off both.
    """
    if not is_enabled():
        return None

    now = now or timezone.localtime()
    today = day or now.date()

    # Before five in the morning the previous day is still tonight, and nagging
    # about it would be nagging somebody who is still working.
    if now.hour < SWEEP_AFTER_HOUR:
        return None

    yesterday = today - timedelta(days=1)
    if DaySignOff.objects.filter(date=yesterday).exists():
        return None

    # Nothing happened that day, so there is nothing to sign off. A clinic that
    # is shut on Sundays should not be told every Monday that it failed to
    # close Sunday.
    if not Visit.objects.filter(scheduled_start__date=yesterday).exists():
        return None

    return yesterday


def auto_cancel_stale(now=None):
    """
    Cancel anything from a previous day that was never marked arrived.

    The clinic's rule: a patient who did not turn up needs nothing pressed. So
    after five in the morning the previous days' unarrived bookings close
    themselves, and the receptionist starts the day with a board showing only
    today.

    Not a scheduled job, because there is no scheduler here and adding one to a
    clinic laptop is adding something that can be quietly not running —
    discovered the month nothing had been cancelled. It runs on the first
    reception page load after five instead, which is the same moment anybody
    would have noticed, and is cheap when there is nothing to do.

    A consultation is never touched, arrived or not. Money owed is a task, and
    this is the function whose whole job is closing things — exactly where that
    rule is easiest to lose.

    Returns how many were closed.
    """
    if not is_enabled():
        return 0

    now = now or timezone.localtime()
    if now.hour < SWEEP_AFTER_HOUR:
        # Still tonight. An evening list running past midnight would otherwise
        # have its waiting patients cancelled out from under it.
        return 0

    stale = Visit.objects.filter(
        scheduled_start__date__lt=now.date(),
        status__in=(VisitStatus.BOOKED, VisitStatus.CONFIRMED),
    )
    closed = 0
    for visit in stale:
        # Booked-but-never-confirmed lapsed; confirmed-and-missed is a no-show.
        # Both are "not arrived", and the day sheet reports them separately
        # because the clinic asked for a no-show sheet — one bucket would leave
        # that sheet permanently empty.
        target = SWEEP_TARGETS[visit.status]
        try:
            visit.transition_to(
                target, note="Not marked arrived — closed automatically",
            )
        except InvalidTransition:
            logger.warning("Could not auto-close visit %s", visit.pk)
        else:
            closed += 1
    if closed:
        logger.info("Auto-closed %s unarrived visit(s) from previous days", closed)
    return closed


def unclosed(day=None):
    """
    Every appointment that has reached a consultation, on a day not yet signed
    off — billed or not.

    **Broadened from "still owed money"** (KAN-48's original definition, which
    dropped a row the moment it was billed) to the whole day's clinical
    worklist. A billed row staying on the list, unchanged, until the day itself
    is signed off is the point: the list is what the receptionist is checking
    against the calendar, not only what she still has to collect, and it must
    not quietly shrink under her while she works down it. Generate bill is
    still offered only on the rows that still need it.

    **Today included**, up to and including ``day``. It counted only previous
    days at first, which made it a next-morning tidy-up rather than what it is:
    the receptionist's worklist for the clinic she is running. A patient the
    doctor finished with an hour ago was not on it and the tab did not appear
    at all, so she was shown work already hers only the following day.

    Every earlier unsigned day too, not just yesterday. A day missed on Friday
    is still owed on Monday, and a list that showed one day would let it
    scroll quietly out of sight. A day drops off this list the moment — and
    only the moment — it is signed off, whatever is billed on it by then.

    A patient still in a cabin is not on it. There is no consultation to
    review until the doctor has finished with them.
    """
    day = day or timezone.localdate()
    signed_off_dates = DaySignOff.objects.values_list("date", flat=True)
    return list(
        Visit.objects.filter(
            scheduled_start__date__lte=day,
            status__in=(VisitStatus.CONSULTED, VisitStatus.BILLED, VisitStatus.COMPLETED),
        )
        .exclude(scheduled_start__date__in=signed_off_dates)
        .with_related()
        .select_related("charge")
        .order_by("scheduled_start")
    )


def unbilled(day):
    """Consultations from ``day`` that were never paid for — KAN-48's whole subject."""
    return list(
        Visit.objects.filter(scheduled_start__date=day, status=VisitStatus.CONSULTED)
        .with_related()
        .select_related("charge")
        .order_by("scheduled_start")
    )


def _clinic_finished_for(day, now=None):
    """
    Has every doctor's own scheduled sitting for ``day`` actually ended?

    Read from the calendar, not from the board happening to look empty — a
    quiet Stage 4 an hour before the evening sitting starts is a lull, not the
    end of the day, and signing off in that gap would tell tomorrow morning
    the day was finished before the clinic had even opened its second sitting.

    ``True`` when nobody was scheduled on ``day`` at all: there is nothing on
    the calendar to wait for, so this does not block anything by itself.
    """
    last_end = DoctorSchedule.objects.filter(date=day).aggregate(
        Max("end_time")
    )["end_time__max"]
    if last_end is None:
        return True

    now = now or timezone.localtime()
    if now.date() > day:
        return True
    if now.date() < day:
        return False
    return now.time() >= last_end


def can_close(day=None, now=None):
    """
    Is today's clinic finished enough to sign off?

    Three conditions:

    * nobody is in a cabin — Stage 3 is empty, so no doctor is still with a
      patient whose fee is not set yet;
    * nothing in Stage 4 is still owed — every consultation has had its receipt
      generated, which is what locks it;
    * every doctor's own scheduled sitting for the day has actually ended
      (``_clinic_finished_for``) — the first two can both be true in the gap
      between a morning and an evening sitting, and that gap is not the end of
      the day.

    Stages 1 and 2 are deliberately not checked. A patient who never turned up
    and one still in the waiting room at closing time are both ordinary ends to
    a day, and the sweep deals with them. Demanding they be cleared first would
    make the button unreachable on any day somebody went home early.

    The button is hidden rather than shown-and-refused when this is False: the
    receptionist would otherwise be told off for pressing the only control on
    the screen, having no way to know the doctor had not finished.
    """
    day = day or timezone.localdate()
    today = Visit.objects.filter(scheduled_start__date=day)
    if today.filter(status=VisitStatus.IN_CABIN).exists():
        return False
    if today.filter(status=VisitStatus.CONSULTED).exists():
        return False
    return _clinic_finished_for(day, now=now)


def day_to_close(now=None):
    """
    The day the receptionist can sign off right now, or ``None``.

    ``is_due`` only ever answers about a *previous* day, because it exists to
    raise an alert about a day left open overnight — and it answers as soon as
    the day is unsigned, before anything on it has been billed, because the
    alert has to fire the moment there is money at risk of being forgotten,
    not once the receptionist has already cleared it. That left a gap: a
    receptionist who bills every patient before leaving has no previous day
    outstanding and no unclosed visits either, so the tab — and the sign-off
    button living on it — never appeared, and today itself could never be
    closed.

    This tries the previous-day answer first, and only when that is silent
    does it check whether *today* is itself finished enough (``can_close``)
    to be signed off early. Either way, the day is only ever handed back once
    ``can_close`` agrees it is actually finished — being due is not the same
    as being closeable, and conflating them here would offer the sign-off
    button on a previous day that still has an unbilled consultation on it.
    A day already signed off, or a day with no visits at all, is never
    offered.
    """
    if not is_enabled():
        return None

    pending = is_due(now=now)
    if pending is not None:
        return pending if can_close(pending, now=now) else None

    today = (now or timezone.localtime()).date()
    if DaySignOff.objects.filter(date=today).exists():
        return None
    if not Visit.objects.filter(scheduled_start__date=today).exists():
        return None
    return today if can_close(today, now=now) else None


def _rows(day):
    """The day's visits, split into the three sheets the ticket names."""
    visits = (
        Visit.objects.filter(scheduled_start__date=day)
        .with_related()
        .select_related("charge")
        .order_by("scheduled_start")
    )

    sheets = {name: [] for name in SHEETS}
    collected = Decimal("0.00")

    for visit in visits:
        charge = getattr(visit, "charge", None)
        paid = charge.amount_paid if charge else Decimal("0.00")

        row = {
            "time": timezone.localtime(visit.scheduled_start).strftime("%H:%M"),
            "patient_id": visit.patient.patient_id,
            "patient": visit.patient.full_name,
            "doctor": visit.doctor.display_name,
            "type": visit.visit_type_label,
            "status": visit.get_status_display(),
            "charged": charge.total if charge else "",
            "collected": paid if charge else "",
            "outstanding": charge.balance if charge else "",
            "receipt": ", ".join(
                p.receipt.receipt_number
                for p in (charge.payments.all() if charge else [])
                if getattr(p, "receipt", None)
            ),
        }

        if visit.status in (VisitStatus.BILLED, VisitStatus.COMPLETED):
            sheets["billed"].append(row)
            collected += paid
        elif visit.status == VisitStatus.CANCELLED:
            sheets["cancelled"].append(row)
        elif visit.status == VisitStatus.NO_SHOW:
            sheets["no_show"].append(row)
        elif visit.status == VisitStatus.CONSULTED:
            # Seen but not paid for. On the billed sheet because that is where
            # somebody reconciling the day will look for it, and marked
            # outstanding so it cannot be mistaken for money taken.
            sheets["billed"].append(row)

    return sheets, collected


def _csv(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def sweep(day, by_user=None):
    """
    Close what is still open from ``day``, and say what could not be closed.

    Returns ``(closed, left_open)`` where ``left_open`` is the consultations
    still owed money — the sweep deliberately will not touch those.
    """
    closed = 0
    for visit in Visit.objects.filter(scheduled_start__date=day).active():
        # A direct consultation the doctor never closed is not reception's to
        # sweep — nobody at the desk knows it exists, and there is no fee on
        # file for her to bill. It stays IN_CABIN, blocking that doctor's
        # cabin, until the doctor finishes it themselves; see the red banner
        # on the calendar for the same rule.
        if visit.status == VisitStatus.IN_CABIN and visit.is_direct:
            continue
        target = SWEEP_TARGETS.get(visit.status)
        if target is None:
            continue
        try:
            visit.transition_to(
                target, by_user=by_user, note="Closed by the end-of-day sign-off",
            )
        except InvalidTransition:
            logger.warning("Sign-off could not close visit %s", visit.pk)
        else:
            closed += 1
    return closed, unbilled(day)


def build_report(day):
    """The three sheets and the day's totals, without sending anything."""
    sheets, collected = _rows(day)
    return {
        "day": day,
        "sheets": sheets,
        "collected": collected,
        "billed_count": len(sheets["billed"]),
        "cancelled_count": len(sheets["cancelled"]),
        "no_show_count": len(sheets["no_show"]),
        "outstanding": unbilled(day),
    }


@transaction.atomic
def sign_off(day, by_user=None, send=True, sweep_first=True):
    """
    Sweep the day, email the report, and record that it was done.

    Idempotent: a day already signed off returns its existing record rather than
    sending a second report. Two receptionists both pressing the button at the
    end of a shift is not an error, and the accountant receiving the day twice
    is worse than either of them being told "already done".

    ``sweep_first`` is False when signing off the day that is still today. The
    sweep cancels bookings nobody arrived for, which is the right thing to do to
    yesterday and quite wrong to do at four in the afternoon — this evening's
    patients have not failed to turn up yet. Today's stragglers are swept the
    next morning, on their own date, like every other day's.

    The sign-off is recorded even when the email fails. Refusing to close a
    clinic day because a mail server is down would block the next morning's work
    over something nobody at the desk can fix — the failure is stored on the
    record and shown, so it can be chased without holding up the clinic.
    """
    existing = DaySignOff.objects.filter(date=day).first()
    if existing is not None:
        return existing, False

    if sweep_first:
        sweep(day, by_user=by_user)
    report = build_report(day)

    to = recipients()
    error = ""

    # Temporarily not sending. There is no mail server yet, so every sign-off
    # ended with a warning the receptionist could do nothing about — on the one
    # action she is asked to perform every day.
    #
    # The report is still BUILT above and simply not handed to the mail layer,
    # rather than the whole block being skipped: a reporting path that has not
    # run for a month is a reporting path that no longer works, and the day this
    # is switched back on is the worst moment to discover that.
    if send and not email_enabled():
        error = (
            "The day sheet was not emailed — sending is switched off until the "
            "clinic's mail server is set up. Nothing else about the sign-off "
            "changes."
        )
    elif send and to:
        try:
            _send(report, to)
        except Exception as exc:                     # noqa: BLE001 — reported, not swallowed
            error = f"{type(exc).__name__}: {exc}"[:300]
            logger.exception("Sign-off report for %s could not be sent", day)
        else:
            if not is_really_delivered():
                # Django reported success and the message went to the container
                # log. Saying "sent" here would be the most useful-sounding lie
                # the system could tell.
                error = (
                    "The report was written to the server log rather than "
                    "emailed — this installation is running the development "
                    "mail settings. Nothing reached "
                    + ", ".join(to) + "."
                )
    elif send and not to:
        error = (
            "No sign-off address is configured, so the report was not sent. "
            "Set SIGN_OFF_EMAILS."
        )

    record = DaySignOff.objects.create(
        date=day,
        sent_by=by_user,
        sent_to=", ".join(to),
        billed_count=report["billed_count"],
        cancelled_count=report["cancelled_count"],
        no_show_count=report["no_show_count"],
        collected=report["collected"],
        delivery_error=error,
    )
    return record, True


def _send(report, to):
    day = report["day"]
    clinic = settings.CLINIC.CLINIC_NAME
    outstanding = report["outstanding"]

    lines = [
        f"{clinic} — clinic day {day:%d %B %Y}",
        "",
        f"Billed        {report['billed_count']}",
        f"Cancelled     {report['cancelled_count']}",
        f"No-show       {report['no_show_count']}",
        f"Collected     {settings.CLINIC.CURRENCY_SYMBOL}{report['collected']}",
    ]

    if outstanding:
        # Named, not counted. A number tells the clinic owner something is
        # wrong; the names tell the receptionist who to ring in the morning.
        lines += [
            "",
            f"{len(outstanding)} consultation"
            f"{'' if len(outstanding) == 1 else 's'} still to be billed:",
        ]
        lines += [
            f"  {timezone.localtime(v.scheduled_start):%H:%M}  "
            f"{v.patient.patient_id}  {v.patient.full_name}  "
            f"({v.doctor.display_name})"
            for v in outstanding
        ]

    message = EmailMessage(
        subject=f"{clinic} — day sheet for {day:%d %b %Y}",
        body="\n".join(lines) + "\n",
        to=to,
    )
    for name in SHEETS:
        message.attach(
            f"{day:%Y-%m-%d}-{name}.csv", _csv(report["sheets"][name]), "text/csv",
        )
    message.send(fail_silently=False)
