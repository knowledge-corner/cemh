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

from .models import DaySignOff, InvalidTransition, Visit, VisitStatus

logger = logging.getLogger(__name__)

#: Before this hour, "yesterday" is still tonight's work. A clinic running an
#: evening list finishes after midnight, and a sweep at 00:05 would cancel
#: patients who are sitting in the waiting room.
SWEEP_AFTER_HOUR = 5

#: What the sweep does with each state it finds left open.
#:
#: A booking nobody confirmed lapsed; one the patient confirmed and then missed
#: is a no-show, and the difference matters when the clinic later asks how often
#: people fail to turn up. CONSULTED is absent on purpose — see the module note.
SWEEP_TARGETS = {
    VisitStatus.BOOKED: VisitStatus.CANCELLED,
    VisitStatus.CONFIRMED: VisitStatus.NO_SHOW,
    VisitStatus.ARRIVED: VisitStatus.CANCELLED,
    VisitStatus.IN_CABIN: VisitStatus.CONSULTED,
}

SHEETS = ("billed", "cancelled", "no_show")

COLUMNS = [
    "time", "patient_id", "patient", "doctor", "status",
    "charged", "collected", "outstanding", "receipt",
]


def recipients():
    """Who the report goes to. Empty means nobody has configured it yet."""
    configured = getattr(settings.CLINIC, "SIGN_OFF_EMAILS", "") or ""
    found = [address.strip() for address in configured.split(",") if address.strip()]
    if found:
        return found
    fallback = getattr(settings.CLINIC, "CLINIC_EMAIL", "") or ""
    return [fallback] if fallback else []


def is_due(day=None, now=None):
    """
    Is there a previous clinic day still waiting to be signed off?

    Answered from :class:`DaySignOff` rather than from the visits, because a day
    on which nothing needed billing and a day nobody closed look identical in
    the visit table — and only one of them is a problem.
    """
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


def unbilled(day):
    """Consultations from ``day`` that were never paid for — KAN-48's whole subject."""
    return list(
        Visit.objects.filter(scheduled_start__date=day, status=VisitStatus.CONSULTED)
        .with_related()
        .select_related("charge")
        .order_by("scheduled_start")
    )


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
def sign_off(day, by_user=None, send=True):
    """
    Sweep the day, email the report, and record that it was done.

    Idempotent: a day already signed off returns its existing record rather than
    sending a second report. Two receptionists both pressing the button at the
    end of a shift is not an error, and the accountant receiving the day twice
    is worse than either of them being told "already done".

    The sign-off is recorded even when the email fails. Refusing to close a
    clinic day because a mail server is down would block the next morning's work
    over something nobody at the desk can fix — the failure is stored on the
    record and shown, so it can be chased without holding up the clinic.
    """
    existing = DaySignOff.objects.filter(date=day).first()
    if existing is not None:
        return existing, False

    sweep(day, by_user=by_user)
    report = build_report(day)

    to = recipients()
    error = ""
    if send and to:
        try:
            _send(report, to)
        except Exception as exc:                     # noqa: BLE001 — reported, not swallowed
            error = f"{type(exc).__name__}: {exc}"[:300]
            logger.exception("Sign-off report for %s could not be sent", day)
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
