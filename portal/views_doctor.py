"""
The doctor's screens.

Two pages: today's queue with a patient-ID box, and the patient dashboard. The
dashboard's tabs are loaded over HTMX so switching between clinical notes and
the growth chart does not reload the whole file.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import role_required
from accounts.models import Role
from appointments.models import VisitStatus
from audit.services import record_patient_view
from patients.models import Patient

from . import services

#: Tab name → (template partial, context builder)
TABS = {
    "summary": ("portal/doctor/_tab_summary.html", services.summary_context),
    "notes": ("portal/doctor/_tab_notes.html", services.notes_context),
    "investigations": ("portal/doctor/_tab_investigations.html", services.investigations_context),
    "growth": ("portal/doctor/_tab_growth.html", services.growth_context),
    "prescriptions": ("portal/doctor/_tab_prescriptions.html", services.prescriptions_context),
}

TAB_LABELS = [
    ("summary", "Summary"),
    ("notes", "Clinical Notes"),
    ("investigations", "Investigations"),
    ("growth", "Growth Chart"),
    ("prescriptions", "Prescriptions"),
]


def _current_visit(patient):
    """
    The visit this chart is open for.

    Order matters: the patient sitting in the cabin comes first, then anything
    else happening today, and only then the next thing in the diary. Taking the
    latest active visit instead would let a follow-up booked for next month hide
    the consultation actually in progress.
    """
    return (
        patient.visits.filter(status=VisitStatus.IN_CABIN).order_by("-scheduled_start").first()
        or patient.visits.for_date().active().order_by("scheduled_start").first()
        or patient.visits.active().order_by("scheduled_start").first()
    )


def _visible_tabs(patient):
    """
    Tabs this patient actually warrants.

    The growth tab is dropped for an adult, and dropped entirely when the
    growth app is not installed — an orthopaedic clinic never sees it.
    """
    for key, label in TAB_LABELS:
        if key == "growth":
            if not services.growth_installed():
                continue
            if not patient.is_paediatric:
                continue
        yield key, label


@role_required(Role.DOCTOR)
def doctor_home(request):
    """Today's queue, plus the patient-ID box the doctor types into."""
    query = request.GET.get("patient_id", "").strip()

    if query:
        patient = (
            Patient.objects.filter(patient_id__iexact=query).first()
            or Patient.objects.filter(phone=query).first()
        )
        if patient:
            return redirect("doctor_patient_dashboard", patient_id=patient.patient_id)
        messages.error(request, f"No patient found with ID or phone “{query}”.")

    return render(
        request,
        "portal/doctor/home.html",
        {"queue": services.todays_queue(request.user), "query": query},
    )


@role_required(Role.DOCTOR)
def patient_dashboard(request, patient_id):
    """
    The patient's file.

    Renders the shell plus the summary tab; the other tabs arrive over HTMX.
    Opening a file is recorded in the audit trail — that is the point at which
    confidential information reaches a screen.
    """
    patient = get_object_or_404(Patient, patient_id__iexact=patient_id)

    record_patient_view(request, patient, "Opened patient dashboard")

    context = {
        "patient": patient,
        "tabs": list(_visible_tabs(patient)),
        "active_tab": "summary",
        "active_visit": _current_visit(patient),
    }
    context.update(services.summary_context(patient))
    return render(request, "portal/doctor/dashboard.html", context)


@role_required(Role.DOCTOR)
def patient_tab(request, patient_id, tab):
    """One dashboard tab, rendered as a fragment for HTMX."""
    if tab not in TABS:
        raise Http404("Unknown tab")

    patient = get_object_or_404(Patient, patient_id__iexact=patient_id)

    if tab not in dict(_visible_tabs(patient)):
        raise Http404("This tab is not available for this patient")

    template, builder = TABS[tab]
    context = builder(patient)

    if context is None:
        raise Http404("This section is not enabled")

    context["active_tab"] = tab
    return render(request, template, context)
