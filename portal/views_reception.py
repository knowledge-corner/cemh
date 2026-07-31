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

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from accounts.permissions import role_required
from appointments import scheduling
from appointments.models import InvalidTransition, Visit, VisitStatus
from audit.models import AuditAction
from audit.services import record, record_patient_view
from billing.models import Charge, Payment, Receipt
from patients.models import Patient

from . import forms as clinic_forms


# ── Queue ─────────────────────────────────────────────────────────────────────

#: Columns of the queue board, in the order a patient moves through them.
#:
#: "To confirm" is separate from "Confirmed" on purpose: the receptionist rings
#: each patient on the appointment day, and this is how she sees who is left.
QUEUE_COLUMNS = [
    ("to_confirm", "To confirm", (VisitStatus.BOOKED,)),
    ("confirmed", "Confirmed", (VisitStatus.CONFIRMED,)),
    ("waiting", "In the waiting room", (VisitStatus.ARRIVED,)),
    ("with_doctor", "With the doctor", (VisitStatus.IN_CABIN,)),
    ("to_bill", "Ready to bill", (VisitStatus.CONSULTED,)),
    ("done", "Settled", (VisitStatus.BILLED, VisitStatus.COMPLETED)),
]


def _queue_context(request, day=None):
    day = day or timezone.localdate()

    visits = (
        Visit.objects.filter(scheduled_start__date=day)
        .with_related()
        .select_related("charge", "prescription")
        .order_by("scheduled_start")
    )

    by_status = {}
    for visit in visits:
        by_status.setdefault(visit.status, []).append(visit)

    columns = []
    for key, label, statuses in QUEUE_COLUMNS:
        rows = [v for status in statuses for v in by_status.get(status, [])]
        rows.sort(key=lambda v: v.scheduled_start)
        columns.append({"key": key, "label": label, "visits": rows, "count": len(rows)})

    cancelled = [
        v for status in (VisitStatus.CANCELLED, VisitStatus.NO_SHOW)
        for v in by_status.get(status, [])
    ]

    return {
        "day": day,
        "is_today": day == timezone.localdate(),
        "columns": columns,
        "cancelled": cancelled,
        "total": len(visits),
        "prev_day": day - timedelta(days=1),
        "next_day": day + timedelta(days=1),
    }


@role_required(Role.RECEPTIONIST)
def reception_home(request):
    """The day's queue. Refreshes itself so the board stays true without F5."""
    day = timezone.localdate()
    requested = request.GET.get("day")
    if requested:
        parsed = timezone.datetime.strptime(requested, "%Y-%m-%d").date()
        day = parsed

    return render(request, "portal/reception/queue.html", _queue_context(request, day))


@role_required(Role.RECEPTIONIST)
def queue_board(request):
    """Just the board, for the polling refresh."""
    day = timezone.localdate()
    requested = request.GET.get("day")
    if requested:
        day = timezone.datetime.strptime(requested, "%Y-%m-%d").date()
    return render(request, "portal/reception/_board.html", _queue_context(request, day))


@role_required(Role.RECEPTIONIST)
def move_visit(request, pk, to_status):
    """
    Move one visit to the next step.

    Goes through ``Visit.transition_to`` so an out-of-order click is refused
    rather than corrupting the day, and so the change is attributed.
    """
    visit = get_object_or_404(Visit, pk=pk)

    if request.method == "POST":
        try:
            visit.transition_to(to_status, by_user=request.user)
        except InvalidTransition as exc:
            messages.error(request, str(exc.message if hasattr(exc, "message") else exc))
        else:
            record(
                request, AuditAction.UPDATE, obj=visit, patient=visit.patient,
                description=f"Visit moved to {visit.get_status_display()}",
            )

    day = timezone.localtime(visit.scheduled_start).date()
    return render(request, "portal/reception/_board.html", _queue_context(request, day))


# ── Bookings ──────────────────────────────────────────────────────────────────

@role_required(Role.RECEPTIONIST)
def bookings(request):
    """Requests waiting to be confirmed, plus everything coming up."""
    today = timezone.localdate()

    pending = (
        Visit.objects.filter(status=VisitStatus.BOOKED, scheduled_start__date__gte=today)
        .with_related().order_by("scheduled_start")
    )
    upcoming = (
        Visit.objects.filter(
            status=VisitStatus.CONFIRMED, scheduled_start__date__gt=today
        ).with_related().order_by("scheduled_start")[:40]
    )

    return render(request, "portal/reception/bookings.html", {
        "pending": pending,
        "upcoming": upcoming,
    })


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
                return redirect("reception_bookings")
    else:
        form = clinic_forms.BookingForm(initial={"patient": patient} if patient else None)

    return render(request, "portal/reception/new_booking.html", {
        "form": form,
        "chosen_patient": patient,
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
        day = timezone.datetime.strptime(day_value, "%Y-%m-%d").date()
        if doctor and day:
            slots = scheduling.available_slots(doctor, day, include_past=True)

    return render(request, "portal/reception/_slots.html", {
        "slots": slots,
        "doctor": doctor,
        "day": day,
        "closed": bool(day) and not scheduling.is_working_day(day),
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


@role_required(Role.RECEPTIONIST)
def register_patient(request):
    """Register a patient who has never attended before."""
    if request.method == "POST":
        form = clinic_forms.PatientForm(request.POST)
        if form.is_valid():
            patient = form.save()
            record(
                request, AuditAction.CREATE, obj=patient, patient=patient,
                description="Patient registered at reception",
            )
            messages.success(
                request, f"Registered {patient.full_name} — {patient.patient_id}."
            )
            # Straight on to booking them in; that is why they are on the phone.
            return redirect(f"{reverse('reception_new_booking')}?patient_id={patient.patient_id}")
    else:
        form = clinic_forms.PatientForm()

    return render(request, "portal/reception/register_patient.html", {"form": form})


# ── Billing ───────────────────────────────────────────────────────────────────

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
        form = clinic_forms.PaymentForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                payment = form.save(commit=False)
                payment.charge = charge
                payment.received_by = request.user
                payment.save()
                receipt = Receipt.objects.create(payment=payment)

                # Only settle the visit once nothing is outstanding — a part
                # payment must leave it on the billing list.
                if charge.balance <= 0 and visit.status == VisitStatus.CONSULTED:
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
            return redirect("reception_billing", pk=visit.pk)
    else:
        initial = {}
        if charge and charge.balance > 0:
            initial["amount"] = charge.balance
        form = clinic_forms.PaymentForm(initial=initial)

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

    if request.method == "POST" or request.GET.get("mark"):
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

    if request.method == "POST" or request.GET.get("mark"):
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
