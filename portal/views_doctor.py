"""
The doctor's screens.

Two pages: today's queue with a patient-ID box, and the patient dashboard. The
dashboard's tabs are loaded over HTMX so switching between clinical notes and
the growth chart does not reload the whole file.
"""

from django.contrib import messages
from django.db.models import Q
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
        messages.error(
            request,
            f"No patient found matching “{query}”. Try typing part of their name.",
        )

    return render(
        request,
        "portal/doctor/home.html",
        {"queue": services.todays_queue(request.user), "query": query},
    )


@role_required(Role.DOCTOR)
def doctor_queue(request):
    """
    Just the queue table, for the polling refresh.

    The queue used to sit still until somebody pressed F5, so a patient sent
    through from reception did not appear at all — the doctor had no way of
    knowing anybody was waiting.
    """
    return render(
        request, "portal/doctor/_queue.html",
        {"queue": services.todays_queue(request.user)},
    )


@role_required(Role.DOCTOR)
def doctor_patient_search(request):
    """
    Type-ahead over the whole patient list.

    The doctor previously had an exact-match box for a UHID or mobile number,
    which is fine when the paper file is in front of them and useless when the
    patient is remembered by name. This is the search reception already had.
    """
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

    return render(request, "portal/doctor/_search_results.html", {
        "results": results, "query": query,
    })


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


@role_required(Role.DOCTOR)
def send_for_patient(request, pk):
    """
    Call the next patient in from the waiting room (KAN-4 FR-3).

    The doctor triggers this, not reception. Reception knows who has arrived;
    only the doctor knows they are ready for the next one, and a patient sent
    in before then sits in an empty cabin.

    The one-patient-per-cabin rule lives in ``Visit.transition_to``, so a second
    send is refused there and reported here rather than being re-checked.
    """
    from appointments.models import InvalidTransition, Visit

    visit = get_object_or_404(
        Visit.objects.select_related("patient", "doctor"), pk=pk
    )

    # A doctor calls their own patients in. Said plainly and named, rather than
    # raised as a 404: every doctor can already see the whole waiting room, so
    # there is nothing here to conceal, and a covering doctor needs to know who
    # to ask rather than that a page is missing.
    if visit.doctor_id != request.user.id:
        messages.error(
            request,
            f"{visit.patient.full_name} is waiting for {visit.doctor.display_name}, "
            "so only they can call this patient in.",
        )
        return redirect("doctor_home")

    if request.method == "POST":
        try:
            visit.transition_to(VisitStatus.IN_CABIN, by_user=request.user)
        except InvalidTransition as exc:
            # AC-3: the cabin is occupied. Said plainly, and the patient stays
            # exactly where they were.
            messages.error(request, str(getattr(exc, "message", exc)))
        else:
            messages.success(request, f"{visit.patient.full_name} sent in.")

    return redirect("doctor_home")
