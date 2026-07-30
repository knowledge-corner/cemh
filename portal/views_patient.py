"""
The patient's own screens.

Deliberately small: their appointments, and a way to request a new one. A
request lands as BOOKED so reception still confirms it — the clinic decides
what actually goes in the diary, not the website.
"""

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Role, User
from accounts.permissions import role_required
from appointments import scheduling
from appointments.models import InvalidTransition, Visit, VisitStatus
from audit.models import AuditAction
from audit.services import record
from patients.models import Patient

from . import forms as clinic_forms


def _patient_for(user):
    """
    The record belonging to the signed-in patient.

    Returns ``None`` when their login has not been linked to a patient record
    yet — a real situation while reception is still matching accounts up.
    """
    return Patient.objects.filter(user=user).first()


@role_required(Role.PATIENT)
def patient_home(request):
    patient = _patient_for(request.user)

    upcoming, past = [], []
    if patient:
        visits = patient.visits.select_related("doctor").order_by("-scheduled_start")
        now = timezone.now()
        upcoming = [
            v for v in visits
            if v.scheduled_start >= now and v.status not in
            (VisitStatus.CANCELLED, VisitStatus.NO_SHOW)
        ]
        upcoming.reverse()
        past = [v for v in visits if v.scheduled_start < now]

    return render(request, "portal/patient/home.html", {
        "patient": patient,
        "upcoming": upcoming,
        "past": past[:20],
    })


@role_required(Role.PATIENT)
def book(request):
    """Request an appointment."""
    patient = _patient_for(request.user)
    if patient is None:
        return render(request, "portal/patient/not_linked.html")

    if request.method == "POST":
        form = clinic_forms.PatientBookingForm(request.POST, patient=patient)
        if form.is_valid():
            try:
                with transaction.atomic():
                    visit = form.save()
            except IntegrityError:
                form.add_error(
                    "slot", "That slot has just been taken. Please choose another time."
                )
            else:
                record(
                    request, AuditAction.CREATE, obj=visit, patient=patient,
                    description="Appointment requested by patient",
                )
                messages.success(
                    request,
                    "Your request has been sent. The clinic will confirm it shortly.",
                )
                return redirect("patient_home")
    else:
        form = clinic_forms.PatientBookingForm(patient=patient)

    first, last = scheduling.booking_window()
    return render(request, "portal/patient/book.html", {
        "patient": patient,
        "form": form,
        "min_date": first,
        "max_date": last,
    })


@role_required(Role.PATIENT)
def patient_slot_options(request):
    """Free slots for the doctor and date the patient has chosen."""
    doctor_id = request.GET.get("doctor")
    day_value = request.GET.get("day")

    slots, doctor, day = [], None, None
    if doctor_id and day_value:
        doctor = User.objects.filter(pk=doctor_id, role=Role.DOCTOR).first()
        day = timezone.datetime.strptime(day_value, "%Y-%m-%d").date()
        if doctor and day:
            # Patients never see past slots, unlike reception.
            slots = scheduling.available_slots(doctor, day)

    return render(request, "portal/reception/_slots.html", {
        "slots": slots,
        "doctor": doctor,
        "day": day,
        "closed": bool(day) and not scheduling.is_working_day(day),
    })


@role_required(Role.PATIENT)
def cancel_visit(request, pk):
    """Let a patient cancel their own appointment."""
    patient = _patient_for(request.user)
    visit = get_object_or_404(Visit, pk=pk, patient=patient)

    if request.method == "POST":
        try:
            visit.transition_to(VisitStatus.CANCELLED, by_user=request.user)
        except InvalidTransition:
            messages.error(
                request,
                "This appointment can no longer be cancelled online. "
                "Please telephone the clinic.",
            )
        else:
            record(
                request, AuditAction.UPDATE, obj=visit, patient=patient,
                description="Appointment cancelled by patient",
            )
            messages.success(request, "Your appointment has been cancelled.")

    return redirect("patient_home")
