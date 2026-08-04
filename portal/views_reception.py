"""
The receptionist's screens.

Three jobs, in the order they happen through a clinic day:

  1. **Queue** — call each patient, mark them arrived, send them into the
     cabin. This is the screen that stays open all day.
  2. **Bookings** — take a booking over the phone, or confirm one a patient
     made online.
  3. **Billing** — once the doctor has finished, collect the fee, issue a
     receipt and print the prescription.
"""

import csv
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from accounts.permissions import role_required
from appointments import scheduling
from appointments.models import (
    ClinicHoliday, DoctorLeave, DoctorSchedule, InvalidTransition,
    ScheduleOverride, Visit, VisitStatus,
)
from audit.models import AuditAction
from audit.services import record, record_patient_view
from billing.models import Charge, Payment, Receipt
from patients.models import Patient

from . import forms as clinic_forms


def _safe_next(request, fallback="reception_home"):
    """
    Where to return to after a save or a cancel (KAN-10 FR-2, FR-3, FR-5).

    Only ever an internal path. A ``next`` carrying a full URL would be an open
    redirect — a login page that bounces to somebody else's site is a real
    phishing route, and this is a clinical system.
    """
    target = request.GET.get("next") or request.POST.get("next") or ""
    if target.startswith("/") and not target.startswith("//"):
        return target
    return reverse(fallback)


# ── Queue ─────────────────────────────────────────────────────────────────────

#: Columns of the queue board, in the order a patient moves through them.
#:
#: Four stages, not six. "To confirm" and "Confirmed" were separate while the
#: receptionist rang every patient on the morning of their appointment; that
#: call is not made any more, so the two columns held the same thing and one of
#: them was always empty. They are now one Appointments column.
#:
#: "Ready to bill" and "Settled" are likewise one column: with a single
#: receptionist, a patient waiting to pay and a patient who has paid are the
#: same row of the desk's work, and splitting them pushed the rest of the board
#: off the side of the screen.
QUEUE_COLUMNS = [
    ("appointments", "Stage 1 · Appointments", (VisitStatus.BOOKED, VisitStatus.CONFIRMED)),
    ("waiting", "Stage 2 · In waiting room", (VisitStatus.ARRIVED,)),
    ("cabin", "Stage 3 · Cabin", (VisitStatus.IN_CABIN,)),
    ("billing", "Stage 4 · Ready to bill / Settled",
     (VisitStatus.CONSULTED, VisitStatus.BILLED, VisitStatus.COMPLETED)),
]

#: Which stage each status is drawn in. Derived from the columns above rather
#: than written out again, so the two cannot disagree.
STAGE_OF_STATUS = {
    status: key for key, _label, statuses in QUEUE_COLUMNS for status in statuses
}


def _queue_context(request, day=None):
    """
    Today's board.

    The date picker was removed deliberately: this screen is the live state of
    the clinic right now, and a receptionist who had paged back to Tuesday and
    left it there would be sending patients in from the wrong day. Past days are
    read on the Bookings screen, which is built for looking things up.
    """
    day = day or timezone.localdate()

    visits = (
        Visit.objects.filter(scheduled_start__date=day)
        .with_related()
        .select_related("charge", "prescription")
        .prefetch_related("status_events__changed_by")
        .order_by("scheduled_start")
    )

    # Doctor filter (KAN-2). Deliberately not remembered between page loads:
    # a filter left on from yesterday is how a receptionist ends up certain the
    # clinic is empty while somebody sits in the waiting room.
    doctors = User.objects.filter(role=Role.DOCTOR, is_active=True)
    chosen_doctor = None
    requested = request.GET.get("doctor", "").strip()
    if requested:
        chosen_doctor = doctors.filter(pk=requested).first()
        if chosen_doctor:
            visits = visits.filter(doctor=chosen_doctor)

    by_status = {}
    for visit in visits:
        by_status.setdefault(visit.status, []).append(visit)

    for visit in visits:
        # Whether "← Back" would actually move the card anywhere the user can
        # see. Booked and Confirmed share the Appointments stage now, so a
        # confirmed booking has a previous *status* but no previous *stage*, and
        # offering the button there gives the receptionist a control that
        # visibly does nothing — which reads as a broken screen rather than as
        # a correction they did not need.
        previous = visit.previous_status
        visit.can_step_back = (
            previous is not None
            and STAGE_OF_STATUS.get(previous) != STAGE_OF_STATUS.get(visit.status)
        )

    columns = []
    for key, label, statuses in QUEUE_COLUMNS:
        rows = [v for status in statuses for v in by_status.get(status, [])]
        rows.sort(key=lambda v: v.scheduled_start)
        columns.append({"key": key, "label": label, "visits": rows, "count": len(rows)})

    # Cancelled visits and no-shows are deliberately not on this board. They are
    # not work: nothing on them can be acted on, and a panel of rows that only
    # ever grows through the day is noise on the one screen that has to be
    # scannable at a glance. They stay in the record and on the Bookings screen.

    # Anything still open from a previous day. A patient left showing as in the
    # cabin overnight is not a record of anything — it is a queue nobody closed,
    # and it makes today's board lie about who the doctor is seeing.
    stale = list(Visit.objects.unfinished_before(day))

    return {
        "day": day,
        "columns": columns,
        "total": len(visits),
        "doctors": doctors,
        "chosen_doctor": chosen_doctor,
        # Doctors may read the board — they need to see who is waiting — but
        # only reception works it. Actions are hidden here and refused by the
        # view underneath, so this is presentation, not the access control.
        "can_work_queue": request.user.role == Role.RECEPTIONIST or request.user.is_superuser,
        "stale": stale,
        "stale_count": len(stale),
        # What still has to happen before the receptionist can go home. Stage 4
        # holds both the unpaid and the paid now, so "still open" counts the
        # money owed rather than the whole column.
        "open_today": sum(
            column["count"] for column in columns if column["key"] != "billing"
        ) + len(by_status.get(VisitStatus.CONSULTED, [])),
    }


@role_required(Role.RECEPTIONIST, Role.DOCTOR)
def reception_home(request):
    """
    Today's queue. Refreshes itself so the board stays true without F5.

    Readable by doctors as well as reception: a doctor needs to see who is in
    the waiting room without asking. The stage buttons are reception's alone,
    and :func:`move_visit` still refuses anyone else.
    """
    return render(request, "portal/reception/queue.html", _queue_context(request))


@role_required(Role.RECEPTIONIST, Role.DOCTOR)
def queue_board(request):
    """Just the board, for the polling refresh."""
    return render(request, "portal/reception/_board_swap.html", _queue_context(request))


@role_required(Role.RECEPTIONIST)
def close_day(request):
    """
    Clear anything left open from a previous day.

    Every stale visit is closed through the same state machine as a live one, so
    the trail says what happened: a patient who never arrived becomes a no-show,
    and one who was seen but never billed is left alone, because money still has
    to be collected and that is a real task rather than an untidy row.
    """
    if request.method != "POST":
        return redirect("reception_home")

    closed, skipped = 0, 0
    for visit in Visit.objects.unfinished_before():
        # A booking nobody ever confirmed lapsed; one the patient confirmed and
        # then missed is a no-show, and the difference matters when the clinic
        # later asks how often people fail to turn up. The state machine draws
        # the same distinction, so no-show is only reachable from confirmed.
        target = {
            VisitStatus.BOOKED: VisitStatus.CANCELLED,
            VisitStatus.CONFIRMED: VisitStatus.NO_SHOW,
            VisitStatus.ARRIVED: VisitStatus.CANCELLED,
            VisitStatus.IN_CABIN: VisitStatus.CONSULTED,
        }.get(visit.status)
        if target is None:
            skipped += 1
            continue
        try:
            visit.transition_to(
                target, by_user=request.user, note="Closed by the end-of-day sweep",
            )
        except InvalidTransition:
            skipped += 1
        else:
            closed += 1
            record(
                request, AuditAction.UPDATE, obj=visit, patient=visit.patient,
                description=f"End-of-day sweep closed visit as {visit.get_status_display()}",
            )

    if closed:
        messages.success(request, f"Closed {closed} visit{'' if closed == 1 else 's'} "
                                  f"left open from previous days.")
    if skipped:
        messages.warning(
            request,
            f"{skipped} still need attention — a consultation that has not been "
            f"billed cannot simply be swept away.",
        )
    if not closed and not skipped:
        messages.info(request, "Nothing was left open. The board is clear.")
    return redirect("reception_home")


@role_required(Role.RECEPTIONIST)
def move_visit(request, pk, to_status):
    """
    Move one visit to the next step.

    Goes through ``Visit.transition_to`` so an out-of-order click is refused
    rather than corrupting the day, and so the change is attributed.

    Two callers, two answers. The queue board posts over HTMX and wants the
    board fragment swapped back in. The bookings page posts an ordinary form and
    wants a redirect — it previously got the board fragment too, which rendered
    a bare partial with no page around it.
    """
    visit = get_object_or_404(Visit.objects.select_related("patient", "doctor"), pk=pk)

    moved = False
    if request.method == "POST":
        # Two receptionists clicking Confirm on the same card is not an error —
        # they both did the right thing, and one of them simply got there
        # second. Say so plainly rather than reporting a failure.
        if visit.status == to_status:
            messages.info(
                request,
                f"{visit.patient.full_name} was already "
                f"{visit.get_status_display().lower()}.",
            )
        else:
            try:
                visit.transition_to(to_status, by_user=request.user)
            except InvalidTransition as exc:
                messages.error(request, str(getattr(exc, "message", exc)))
            else:
                moved = True
                record(
                    request, AuditAction.UPDATE, obj=visit, patient=visit.patient,
                    description=f"Visit moved to {visit.get_status_display()}",
                )

    if request.headers.get("HX-Request"):
        return render(
            request, "portal/reception/_board_swap.html",
            _queue_context(request, timezone.localtime(visit.scheduled_start).date()),
        )

    if moved:
        messages.success(
            request,
            f"{visit.patient.full_name} — {visit.get_status_display().lower()}.",
        )
    return redirect(request.POST.get("next") or "reception_bookings")


# ── Bookings ──────────────────────────────────────────────────────────────────

#: Two tabs: what is still to come, and what is finished and paid for.
#:
#: The old "Confirmation calls to make" tab has gone with the telephone
#: confirmation step itself. Its bookings are not lost — a booking nobody has
#: confirmed is simply an upcoming appointment, which is what it always was.
BOOKING_TABS = [
    ("upcoming", "Upcoming appointments"),
    ("completed", "Completed appointments"),
]

#: A visit is upcoming while it has not yet reached the doctor. CONFIRMED is
#: included so that a patient moved back out of the waiting room reappears here
#: rather than vanishing from both screens.
UPCOMING_STATUSES = (VisitStatus.BOOKED, VisitStatus.CONFIRMED)


def _as_date(value):
    try:
        return timezone.datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _chosen_filters(request):
    """What the filter form was set to, whichever tab is showing."""
    return {
        "from": request.GET.get("from", "").strip(),
        "to": request.GET.get("to", "").strip(),
        "doctor": request.GET.get("doctor", "").strip(),
        "patient_id": request.GET.get("patient_id", "").strip(),
        "condition": request.GET.get("condition", "").strip(),
    }


def _apply_date_and_doctor(visits, chosen):
    """The two filters both tabs share."""
    start, end = _as_date(chosen["from"]), _as_date(chosen["to"])
    if start:
        visits = visits.filter(scheduled_start__date__gte=start)
    if end:
        visits = visits.filter(scheduled_start__date__lte=end)
    if chosen["doctor"]:
        visits = visits.filter(doctor_id=chosen["doctor"])
    return visits


def _upcoming_filters(request):
    """
    Appointments still to happen, split into today and the days after it.

    Today's group is *not* narrowed to slots that have not passed yet. A patient
    twenty minutes late is exactly the one reception is looking for, and hiding
    their appointment the moment the clock passes it is how they end up being
    told there is no booking.
    """
    chosen = _chosen_filters(request)
    today = timezone.localdate()

    visits = (
        Visit.objects.filter(status__in=UPCOMING_STATUSES, scheduled_start__date__gte=today)
        .with_related()
        .order_by("scheduled_start")
    )
    visits = _apply_date_and_doctor(visits, chosen)

    if chosen["patient_id"]:
        visits = visits.filter(
            Q(patient__patient_id__icontains=chosen["patient_id"])
            | Q(patient__phone__icontains=chosen["patient_id"])
            | Q(patient__first_name__icontains=chosen["patient_id"])
            | Q(patient__last_name__icontains=chosen["patient_id"])
        )

    rows = list(visits)
    return (
        [v for v in rows if timezone.localtime(v.scheduled_start).date() == today],
        [v for v in rows if timezone.localtime(v.scheduled_start).date() > today],
        chosen,
    )


def _past_filters(request):
    """
    Completed appointments: seen, billed, and nothing left owing.

    Filtered on the money rather than on the stage. A visit sitting at BILLED
    with a part payment against it is not finished — somebody still has to
    collect the rest — and listing it as completed is how that balance stops
    being chased.

    "Nothing owing" rather than "fully paid", because they are not the same
    thing. A visit with no charge on it at all owes nothing: that is every
    historical visit recorded before the clinic billed through this system, and
    excluding them does not protect anybody from anything — it just deletes the
    clinic's own history from the one screen built to look it up.
    """
    total_due = (
        F("charge__consultation_fee") + F("charge__procedure_fee") - F("charge__discount")
    )
    visits = (
        Visit.objects.filter(status__in=(VisitStatus.BILLED, VisitStatus.COMPLETED))
        .with_related()
        .select_related("charge")
        .annotate(
            _paid=Coalesce(
                Sum("charge__payments__amount"), Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=10, decimal_places=2),
            ),
        )
        .filter(Q(charge__isnull=True) | Q(_paid__gte=total_due))
        .order_by("-scheduled_start")
    )

    chosen = _chosen_filters(request)
    visits = _apply_date_and_doctor(visits, chosen)

    if chosen["patient_id"]:
        visits = visits.filter(
            Q(patient__patient_id__icontains=chosen["patient_id"])
            | Q(patient__phone__icontains=chosen["patient_id"])
            | Q(patient__first_name__icontains=chosen["patient_id"])
            | Q(patient__last_name__icontains=chosen["patient_id"])
        )
    if chosen["condition"]:
        # Matched against the diagnoses recorded for the patient rather than
        # free text on the visit, so "thyroid" finds the visits of patients
        # actually carrying that diagnosis.
        visits = visits.filter(
            Q(patient__diagnoses__description__icontains=chosen["condition"])
            | Q(reason__icontains=chosen["condition"])
        ).distinct()

    return visits, chosen


@role_required(Role.RECEPTIONIST)
def bookings(request):
    """The diary ahead, and what has been seen and paid for."""
    tab = request.GET.get("tab", "upcoming")
    if tab not in dict(BOOKING_TABS):
        tab = "upcoming"

    today_rows, ahead_rows, chosen = _upcoming_filters(request)

    past, past_chosen = _past_filters(request)
    total_collected = sum(
        (v.charge.amount_paid for v in past[:500] if getattr(v, "charge", None)), Decimal("0.00")
    )

    return render(request, "portal/reception/bookings.html", {
        "tabs": BOOKING_TABS,
        "active_tab": tab,
        "today_rows": today_rows,
        "ahead_rows": ahead_rows,
        "upcoming_count": len(today_rows) + len(ahead_rows),
        "past": past[:300],
        "past_count": past.count(),
        "total_collected": total_collected,
        "filters": chosen if tab == "upcoming" else past_chosen,
        "doctors": User.objects.filter(role=Role.DOCTOR, is_active=True),
    })


@role_required(Role.RECEPTIONIST)
def export_bookings(request):
    """
    The filtered history as a CSV, one row per visit.

    Written straight to the response rather than built in memory: the clinic
    owner asking for a year of payments should not be limited by how much of it
    fits in RAM.
    """
    visits, chosen = _past_filters(request)

    response = HttpResponse(content_type="text/csv")
    stamp = timezone.localdate().strftime("%Y-%m-%d")
    response["Content-Disposition"] = f'attachment; filename="bookings-{stamp}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Date", "Time", "Doctor", settings.CLINIC.PATIENT_ID_LABEL, "Patient",
        "Age", "Sex", "Reason", "Diagnoses", "Consultation fee", "Procedure fee",
        "Discount", "Total", "Paid", "Balance", "Status",
    ])

    for visit in visits.iterator(chunk_size=200):
        charge = getattr(visit, "charge", None)
        diagnoses = ", ".join(
            d.description for d in visit.patient.diagnoses.all()[:5]
        )
        local = timezone.localtime(visit.scheduled_start)
        writer.writerow([
            local.strftime("%Y-%m-%d"),
            local.strftime("%H:%M"),
            visit.doctor.display_name,
            visit.patient.patient_id,
            visit.patient.full_name,
            visit.patient.age_years,
            visit.patient.get_sex_display(),
            visit.reason,
            diagnoses,
            charge.consultation_fee if charge else "",
            charge.procedure_fee if charge else "",
            charge.discount if charge else "",
            charge.total if charge else "",
            charge.amount_paid if charge else "",
            charge.balance if charge else "",
            visit.get_status_display(),
        ])

    record(
        request, AuditAction.PRINT,
        description=f"Exported {visits.count()} bookings to CSV",
    )
    return response


@role_required(Role.RECEPTIONIST)
def new_booking(request):
    """
    Take a booking at the desk or over the phone.

    Registers a new patient inline when the caller has never attended before —
    the commonest reason a phone booking stalls is having to leave the screen
    to create the patient first.
    """
    existing_id = request.GET.get("patient_id", "").strip()
    patient = None
    if existing_id:
        patient = Patient.objects.filter(patient_id__iexact=existing_id).first()

    if request.method == "POST":
        form = clinic_forms.BookingForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    visit = form.save(booked_by=request.user)
            except IntegrityError:
                # The exclusion constraint refused it — somebody took the slot
                # between the page loading and this submission.
                form.add_error(
                    "slot",
                    "That slot has just been taken. Please choose another time.",
                )
            else:
                record(
                    request, AuditAction.CREATE, obj=visit, patient=visit.patient,
                    description="Booking taken at reception",
                )
                messages.success(
                    request,
                    f"Booked {visit.patient.full_name} with {visit.doctor.display_name} "
                    f"on {timezone.localtime(visit.scheduled_start):%d %b at %H:%M}.",
                )
                return redirect(_safe_next(request, "reception_bookings"))
    else:
        form = clinic_forms.BookingForm(initial={"patient": patient} if patient else None)

    return render(request, "portal/reception/new_booking.html", {
        "form": form,
        "chosen_patient": patient,
        "cancel_url": _safe_next(request, "reception_bookings"),
        "next": request.GET.get("next", ""),
    })


@role_required(Role.RECEPTIONIST)
def slot_options(request):
    """Free slots for the chosen doctor and date, loaded as the form changes."""
    doctor_id = request.GET.get("doctor")
    day_value = request.GET.get("day")

    slots = []
    doctor = None
    day = None

    if doctor_id and day_value:
        doctor = User.objects.filter(pk=doctor_id, role=Role.DOCTOR).first()
        try:
            day = timezone.datetime.strptime(day_value, "%Y-%m-%d").date()
        except ValueError:
            day = None
        if doctor and day:
            # The whole grid, not only what is free. A taken slot shown greyed
            # tells the receptionist the doctor works then and somebody else has
            # it; a taken slot left out looks exactly like a time the doctor
            # does not work, and she stops offering it.
            slots = scheduling.slot_grid(doctor, day)

    return render(request, "portal/reception/_slots.html", {
        "slots": slots,
        "free_count": sum(1 for s in slots if s["state"] == scheduling.SLOT_FREE),
        "doctor": doctor,
        "day": day,
        # Asked per doctor, not clinic-wide: the clinic is open every day now,
        # so a day with no slots means either a holiday or this doctor not
        # working it, and the message needs to say which.
        "closed": bool(day and doctor) and not scheduling.is_working_day(day, doctor),
        "holiday": ClinicHoliday.objects.filter(date=day).first() if day else None,
    })


@role_required(Role.RECEPTIONIST)
def patient_lookup(request):
    """Type-ahead search by name, UHID or mobile, for the booking form."""
    query = request.GET.get("q", "").strip()

    results = []
    if len(query) >= 2:
        results = Patient.objects.filter(is_active=True).filter(
            Q(patient_id__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(phone__icontains=query)
            | Q(guardian_phone__icontains=query)
        ).order_by("first_name")[:8]

    return render(request, "portal/reception/_patient_results.html", {
        "results": results, "query": query,
    })


def _possible_duplicates(form):
    """
    Patients who look like the one being registered.

    Name *and* number together, not either alone: families share a mobile
    constantly — a mother's number sits on three of her children's records —
    so matching on the number by itself would stop the ordinary case rather
    than the mistaken one.
    """
    first = (form.cleaned_data.get("first_name") or "").strip()
    last = (form.cleaned_data.get("last_name") or "").strip()
    phone = (form.cleaned_data.get("phone") or "").strip()

    if not (first and phone):
        return Patient.objects.none()

    return Patient.objects.filter(
        is_active=True, phone=phone, first_name__iexact=first, last_name__iexact=last
    )[:5]


@role_required(Role.RECEPTIONIST)
def register_patient(request):
    """Register a patient who has never attended before."""
    duplicates = None

    if request.method == "POST":
        form = clinic_forms.PatientForm(request.POST)
        if form.is_valid():
            # A second record for somebody already here does not just make a
            # mess: it splits their history in two and the doctor reads half of
            # it. So the existing record is offered before a new one is made —
            # once, and then reception decides.
            if not request.POST.get("confirm"):
                duplicates = _possible_duplicates(form)

        if form.is_valid() and not duplicates:
            patient = form.save()
            record(
                request, AuditAction.CREATE, obj=patient, patient=patient,
                description="Patient registered at reception",
            )
            messages.success(
                request, f"Registered {patient.full_name} — {patient.patient_id}."
            )
            # Straight on to booking them in; that is why they are on the
            # phone. Unless they came from the board with a filter applied, in
            # which case that is where they expect to land back (KAN-10 AC-4).
            after = _safe_next(request, "reception_new_booking")
            if request.GET.get("next") or request.POST.get("next"):
                return redirect(after)
            return redirect(f"{reverse('reception_new_booking')}?patient_id={patient.patient_id}")
    else:
        form = clinic_forms.PatientForm()

    return render(request, "portal/reception/register_patient.html", {
        "form": form,
        "duplicates": duplicates,
        "cancel_url": _safe_next(request),
        # Carried on the form as well as the query string: the warning above
        # re-posts the page, and losing the filter on the way back would defeat
        # the point of keeping it (AC-4).
        "next": request.GET.get("next") or request.POST.get("next", ""),
    })


# ── Billing ───────────────────────────────────────────────────────────────────

def _take_payment(request, visit, charge, form):
    """
    Record one payment against a charge and issue its receipt.

    Shared by the billing page and the Generate receipt pop-up, because the part
    that must not be got wrong is the same in both: the charge is re-read under
    a row lock and the balance checked there. The form has already refused an
    amount larger than the balance, but two clicks — or two people on the same
    bill — each read that balance before either wrote to it, and only the lock
    sees that collision.

    Returns the receipt, or ``None`` when the bill was already settled.
    """
    with transaction.atomic():
        locked = Charge.objects.select_for_update().get(pk=charge.pk)

        if locked.balance <= 0:
            return None

        payment = form.save(commit=False)
        payment.charge = locked
        payment.received_by = request.user
        payment.save()
        receipt = Receipt.objects.create(payment=payment)

        # Only settle the visit once nothing is outstanding — a part payment
        # must leave it on the billing list.
        if locked.balance <= 0 and visit.status == VisitStatus.CONSULTED:
            visit.transition_to(VisitStatus.BILLED, by_user=request.user)

    record(
        request, AuditAction.CREATE, obj=payment, patient=visit.patient,
        description=f"Payment received, receipt {receipt.receipt_number}",
    )
    messages.success(
        request,
        f"Receipt {receipt.receipt_number} issued for "
        f"{settings.CLINIC.CURRENCY_SYMBOL}{payment.amount}.",
    )
    return receipt


@role_required(Role.RECEPTIONIST)
def generate_receipt(request, pk):
    """
    Take the fee without leaving the board (Stage 4, "Generate receipt").

    A pop-up rather than a page: collecting the money is a ten-second job at the
    desk with the patient standing there, and sending the receptionist to
    another screen and back lost the board — and, with it, whichever doctor
    filter she had set.

    The full billing page is still there for anything harder: a part payment
    that needs the history, or a visit somebody wants to read before taking
    money for it.
    """
    visit = get_object_or_404(
        Visit.objects.select_related("patient", "doctor", "charge"), pk=pk
    )
    charge = getattr(visit, "charge", None)

    if charge is None:
        # Nothing to collect: the doctor has not set a fee. Said in the dialog
        # the user opened rather than as a 404, which would read as a bug.
        return HttpResponse(render_to_string(
            "portal/reception/_payment_modal.html",
            {"visit": visit, "charge": None, "form": None},
            request=request,
        ))

    if request.method == "POST":
        form = clinic_forms.PaymentForm(request.POST, charge=charge)
        if form.is_valid():
            if _take_payment(request, visit, charge, form) is None:
                messages.info(
                    request, "This bill was already settled; nothing further was taken."
                )
            # Close the dialog and put the refreshed board back underneath it,
            # so the card moves to Settled while the receptionist is still
            # looking at it.
            return HttpResponse(render_to_string(
                "portal/reception/_payment_done.html",
                {"board_html": render_to_string(
                    "portal/reception/_board.html", _queue_context(request), request=request,
                )},
                request=request,
            ))
    else:
        form = clinic_forms.PaymentForm(
            initial={"amount": charge.balance} if charge.balance > 0 else {},
            charge=charge,
        )

    return HttpResponse(render_to_string(
        "portal/reception/_payment_modal.html",
        {"visit": visit, "charge": charge, "form": form},
        request=request,
    ))


@role_required(Role.RECEPTIONIST)
def billing(request, pk):
    """
    Settle one visit: show what the doctor charged, take the money, issue a
    receipt, then release the prescription for printing.
    """
    visit = get_object_or_404(
        Visit.objects.select_related("patient", "doctor"), pk=pk
    )
    record_patient_view(request, visit.patient, "Opened billing for a visit")

    charge = getattr(visit, "charge", None)
    prescription = getattr(visit, "prescription", None)

    if request.method == "POST" and charge:
        form = clinic_forms.PaymentForm(request.POST, charge=charge)
        if form.is_valid():
            if _take_payment(request, visit, charge, form) is None:
                messages.info(
                    request, "This bill was already settled; nothing further was taken."
                )
            return redirect("reception_billing", pk=visit.pk)
    else:
        initial = {}
        if charge and charge.balance > 0:
            initial["amount"] = charge.balance
        form = clinic_forms.PaymentForm(initial=initial, charge=charge)

    return render(request, "portal/reception/billing.html", {
        "visit": visit,
        "patient": visit.patient,
        "charge": charge,
        "prescription": prescription,
        "form": form,
        "payments": charge.payments.select_related("receipt", "received_by") if charge else [],
    })


@role_required(Role.RECEPTIONIST)
def complete_visit(request, pk):
    """Mark the patient as finished and gone."""
    visit = get_object_or_404(Visit, pk=pk)
    if request.method == "POST":
        try:
            visit.transition_to(VisitStatus.COMPLETED, by_user=request.user)
        except InvalidTransition as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{visit.patient.full_name} checked out.")
    return redirect("reception_home")


# ── Printing ──────────────────────────────────────────────────────────────────

@role_required(Role.RECEPTIONIST, Role.DOCTOR)
def print_prescription(request, pk):
    """Print-ready prescription on the clinic's letterhead."""
    visit = get_object_or_404(Visit.objects.select_related("patient", "doctor"), pk=pk)
    prescription = getattr(visit, "prescription", None)
    if prescription is None:
        return HttpResponse("No prescription has been issued for this visit.", status=404)

    # KAN-8 FR-6: a reprint changes nothing. ``printed_at`` records that the
    # document reached paper at all, so it is stamped once and then left alone —
    # otherwise every reprint would edit a settled record, and "when was this
    # first given to the patient" would quietly become "when was it last
    # printed". Each print is still recorded in the audit log, which is where a
    # history of prints belongs.
    if prescription.printed_at is None and (
        request.method == "POST" or request.GET.get("mark")
    ):
        prescription.printed_at = timezone.now()
        prescription.save(update_fields=["printed_at", "updated_at"])

    record(
        request, AuditAction.PRINT, obj=prescription, patient=visit.patient,
        description="Printed prescription",
    )

    return render(request, "portal/print/prescription.html", {
        "visit": visit,
        "patient": visit.patient,
        "prescription": prescription,
        "doctor_profile": getattr(visit.doctor, "doctor_profile", None),
        "items": prescription.items.all(),
    })


@role_required(Role.RECEPTIONIST)
def print_receipt(request, pk):
    """Print-ready receipt."""
    receipt = get_object_or_404(
        Receipt.objects.select_related("payment__charge__patient", "payment__charge__visit"),
        pk=pk,
    )
    charge = receipt.payment.charge

    # Stamped once, then never again — see the note on print_prescription.
    if receipt.printed_at is None and (
        request.method == "POST" or request.GET.get("mark")
    ):
        receipt.printed_at = timezone.now()
        receipt.save(update_fields=["printed_at"])

    record(
        request, AuditAction.PRINT, obj=receipt, patient=charge.patient,
        description=f"Printed receipt {receipt.receipt_number}",
    )

    return render(request, "portal/print/receipt.html", {
        "receipt": receipt,
        "payment": receipt.payment,
        "charge": charge,
        "patient": charge.patient,
        "visit": charge.visit,
    })


# ── Amending a booking ────────────────────────────────────────────────────────

@role_required(Role.RECEPTIONIST)
def edit_booking(request, pk):
    """
    Move a booking to a different slot, or close it off.

    Rescheduling is deliberately not "cancel and rebook": that loses the thread
    between the two, and the patient rang about one appointment, not two. The
    visit keeps its identity and the move is recorded against it.
    """
    visit = get_object_or_404(
        Visit.objects.select_related("patient", "doctor"), pk=pk
    )

    if visit.status in (VisitStatus.CANCELLED, VisitStatus.NO_SHOW,
                        VisitStatus.COMPLETED, VisitStatus.BILLED):
        messages.error(
            request,
            f"This booking is already {visit.get_status_display().lower()} and "
            f"cannot be changed.",
        )
        return redirect("reception_bookings")

    if request.method == "POST":
        action = request.POST.get("action", "reschedule")

        if action in ("cancel", "no_show"):
            target = VisitStatus.CANCELLED if action == "cancel" else VisitStatus.NO_SHOW
            reason = request.POST.get("reason", "").strip()
            try:
                visit.transition_to(target, by_user=request.user, note=reason)
            except InvalidTransition as exc:
                messages.error(request, str(getattr(exc, "message", exc)))
            else:
                record(
                    request, AuditAction.UPDATE, obj=visit, patient=visit.patient,
                    description=f"Booking marked {visit.get_status_display().lower()}"
                                + (f" — {reason}" if reason else ""),
                )
                messages.success(
                    request,
                    f"{visit.patient.full_name}'s appointment is now "
                    f"{visit.get_status_display().lower()}.",
                )
            return redirect("reception_bookings")

        form = clinic_forms.RescheduleForm(request.POST, visit=visit)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save(by_user=request.user)
            except IntegrityError:
                form.add_error("slot", "That slot has just been taken. Choose another.")
            else:
                record(
                    request, AuditAction.UPDATE, obj=visit, patient=visit.patient,
                    description="Booking rescheduled",
                )
                messages.success(
                    request,
                    f"Moved {visit.patient.full_name} to "
                    f"{timezone.localtime(visit.scheduled_start):%d %b at %H:%M}.",
                )
                return redirect("reception_bookings")
    else:
        form = clinic_forms.RescheduleForm(visit=visit)

    return render(request, "portal/reception/edit_booking.html", {
        "visit": visit,
        "patient": visit.patient,
        "form": form,
    })


# ── Doctor availability ───────────────────────────────────────────────────────

@role_required(Role.RECEPTIONIST)
def availability(request):
    """
    Working days, hours, holidays and leave — all on one screen.

    Kept together on purpose. "Is Dr Kulkarni in on Thursday" is one question,
    and answering it from three separate screens is how a patient gets booked
    against a day the doctor is away.
    """
    doctors = User.objects.filter(role=Role.DOCTOR, is_active=True)
    today = timezone.localdate()

    return render(request, "portal/reception/availability.html", {
        "doctors": doctors,
        "schedules": DoctorSchedule.objects.select_related("doctor").order_by(
            "doctor__first_name", "weekday", "start_time"
        ),
        "holidays": ClinicHoliday.objects.filter(date__gte=today).order_by("date"),
        "leave": DoctorLeave.objects.select_related("doctor").filter(
            date__gte=today
        ).order_by("date"),
        "overrides": ScheduleOverride.objects.select_related("doctor").filter(
            date__gte=today
        ).order_by("date"),
        "schedule_form": clinic_forms.DoctorScheduleForm(),
        "leave_form": clinic_forms.DoctorLeaveForm(),
        "holiday_form": clinic_forms.ClinicHolidayForm(),
        "override_form": clinic_forms.ScheduleOverrideForm(),
        "default_hours": (
            f"{settings.CLINIC.CONSULTING_START}–{settings.CLINIC.CONSULTING_END}"
        ),
        "default_slot": settings.CLINIC.SLOT_MINUTES,
    })


#: Which form handles which button on the availability screen.
AVAILABILITY_FORMS = {
    "schedule": (clinic_forms.DoctorScheduleForm, "Working hours saved."),
    "override": (clinic_forms.ScheduleOverrideForm, "One-off hours saved."),
    "holiday": (clinic_forms.ClinicHolidayForm, "Holiday added."),
    "leave": (clinic_forms.DoctorLeaveForm, None),
}


@role_required(Role.RECEPTIONIST)
def add_availability(request, kind):
    """Add one schedule row, override, holiday or period of leave."""
    if request.method != "POST" or kind not in AVAILABILITY_FORMS:
        return redirect("reception_availability")

    form_class, success_message = AVAILABILITY_FORMS[kind]
    form = form_class(request.POST)

    if not form.is_valid():
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{field}: {error}")
        return redirect("reception_availability")

    obj = form.save(commit=False)
    for attribute in ("created_by",):
        if hasattr(obj, attribute):
            setattr(obj, attribute, request.user)
    obj.save()

    record(request, AuditAction.CREATE, obj=obj, description=f"Availability: {obj}")

    if kind == "leave":
        # The whole point of recording leave late is this list. Patients already
        # confirmed for the day have to be rung and moved, and nobody will do it
        # if the screen does not say so.
        stranded = obj.affected_visits()
        if stranded:
            names = ", ".join(
                f"{v.patient.full_name} ({timezone.localtime(v.scheduled_start):%H:%M})"
                for v in stranded
            )
            messages.warning(
                request,
                f"{len(stranded)} patient{'' if len(stranded) == 1 else 's'} already "
                f"booked with {obj.doctor.display_name} on "
                f"{obj.date:%d %b} must be rung and moved: {names}",
            )
        else:
            messages.success(request, f"Leave recorded. Nobody was booked in.")
    elif success_message:
        messages.success(request, success_message)

    return redirect("reception_availability")


@role_required(Role.RECEPTIONIST)
def remove_availability(request, kind, pk):
    """Delete one schedule row, override, holiday or period of leave."""
    models_by_kind = {
        "schedule": DoctorSchedule,
        "override": ScheduleOverride,
        "holiday": ClinicHoliday,
        "leave": DoctorLeave,
    }
    if request.method != "POST" or kind not in models_by_kind:
        return redirect("reception_availability")

    obj = get_object_or_404(models_by_kind[kind], pk=pk)
    description = str(obj)
    obj.delete()
    record(request, AuditAction.DELETE, description=f"Availability removed: {description}")
    messages.success(request, "Removed.")
    return redirect("reception_availability")


# ── Corrections (KAN-9) ───────────────────────────────────────────────────────

@role_required(Role.RECEPTIONIST)
def move_visit_back(request, pk):
    """
    Put a visit back one stage, to undo a mis-click.

    Reception's action, not the doctor's: the mistakes this fixes are made at
    the desk. Refused once the doctor has finished — see ``Visit.move_back`` for
    why that is the line.
    """
    visit = get_object_or_404(Visit.objects.select_related("patient", "doctor"), pk=pk)

    if request.method == "POST":
        try:
            visit.move_back(by_user=request.user, note=request.POST.get("reason", "").strip())
        except InvalidTransition as exc:
            messages.error(request, str(getattr(exc, "message", exc)))
        else:
            record(
                request, AuditAction.UPDATE, obj=visit, patient=visit.patient,
                description=f"Visit moved back to {visit.get_status_display()}",
            )
            messages.success(
                request,
                f"{visit.patient.full_name} moved back to "
                f"{visit.get_status_display().lower()}.",
            )

    if request.headers.get("HX-Request"):
        return render(
            request, "portal/reception/_board_swap.html",
            _queue_context(request, timezone.localtime(visit.scheduled_start).date()),
        )
    return redirect("reception_home")


# ── Settled (KAN-8) ───────────────────────────────────────────────────────────

@role_required(Role.RECEPTIONIST, Role.DOCTOR)
def settled_visit(request, pk):
    """
    A finished visit, read only.

    No form and no save anywhere on this view — the documents are reprintable
    and nothing else about the visit can be touched. The lock itself is on the
    model, so this is the honest presentation of it rather than the enforcement.
    """
    visit = get_object_or_404(
        Visit.objects.select_related("patient", "doctor", "charge"), pk=pk
    )
    record_patient_view(request, visit.patient, "Opened a settled visit")

    charge = getattr(visit, "charge", None)
    payments = (
        charge.payments.select_related("receipt", "received_by") if charge else []
    )
    receipt = next((p.receipt for p in payments if hasattr(p, "receipt")), None)

    return render(request, "portal/reception/settled.html", {
        "visit": visit,
        "patient": visit.patient,
        "charge": charge,
        "payments": payments,
        "receipt": receipt,
        "prescription": getattr(visit, "prescription", None),
        "events": visit.status_events.select_related("changed_by").order_by("created_at"),
    })
