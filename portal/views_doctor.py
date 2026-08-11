"""
The doctor's screens.

Two pages: today's queue with a patient-ID box, and the patient dashboard. The
dashboard's tabs are loaded over HTMX so switching between clinical notes and
the growth chart does not reload the whole file.
"""

import csv

from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.permissions import role_required
from accounts.models import Role
from appointments import scheduling
from appointments.models import InvalidTransition, Visit, VisitStatus
from audit.models import AuditAction
from audit.services import record, record_patient_view
from patients import matching
from patients.models import Patient

from . import analytics
from . import forms as clinic_forms
from . import services

#: Tab name → (template partial, context builder)
TABS = {
    "summary": ("portal/doctor/_tab_summary.html", services.summary_context),
    "notes": ("portal/doctor/_tab_notes.html", services.notes_context),
    "investigations": ("portal/doctor/_tab_investigations.html", services.investigations_context),
    "growth": ("portal/doctor/_tab_growth.html", services.growth_context),
    "prescriptions": ("portal/doctor/_tab_prescriptions.html", services.prescriptions_context),
    "reference_letters": (
        "portal/doctor/_tab_reference_letters.html", services.reference_letters_context,
    ),
}

TAB_LABELS = [
    ("summary", "Summary"),
    ("notes", "Clinical Notes"),
    ("investigations", "Investigations"),
    ("growth", "Growth Chart"),
    ("prescriptions", "Prescriptions"),
    ("reference_letters", "Reference Letters"),
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


def _is_editable(patient):
    """
    Whether this chart takes new edits right now.

    Read-only unless the patient is actually in the cabin. "Open" — from the
    queue or from search, the same read-only view either way — is a glance
    at the file; "Send in" and "Start consultation" are the only two things
    that move a visit to IN_CABIN, so that transition is what unlocks it.

    One rule, checked in one place, so if the clinic ever wants "Open" itself
    to be editable again, this is the only line that has to change.
    """
    visit = _current_visit(patient)
    return bool(visit and visit.status == VisitStatus.IN_CABIN)


def _editable_for_tab(patient, tab):
    """
    ``_is_editable``, with the one deliberate exception reference letters get
    — see ``edit_record``'s own comment on the same carve-out. Kept next to
    ``_is_editable`` so the two cannot quietly drift apart.
    """
    if tab == "reference_letters":
        return True
    return _is_editable(patient)


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


def _unconfirmed_count(doctor):
    """
    Today's confirmed bookings still waiting to walk in.

    A booking is CONFIRMED the moment it is made — there is no separate
    phone-call step any more — so "unconfirmed" here means "confirmed for
    today but not yet arrived", the thing actually worth a doctor's
    attention. A future day's confirmed booking is not unconfirmed in this
    sense; it just has not happened yet.
    """
    return Visit.objects.for_date().filter(doctor=doctor, status=VisitStatus.CONFIRMED).count()


def _waiting_count(queue):
    """
    How many of today's queue are actually waiting to be seen.

    Not the whole queue — that includes everything from a confirmed booking
    still hours away to an already-billed visit. Only ARRIVED counts: once
    the doctor calls someone in (IN_CABIN), they are being seen, not waiting,
    even though the row stays on screen.
    """
    return sum(1 for visit in queue if visit.status == VisitStatus.ARRIVED)


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

    queue = services.todays_queue(request.user)
    return render(
        request,
        "portal/doctor/home.html",
        {
            "queue": queue, "query": query,
            "waiting_count": _waiting_count(queue),
            "unconfirmed_count": _unconfirmed_count(request.user),
        },
    )


@role_required(Role.DOCTOR)
def register_patient(request):
    """
    A doctor registering a patient who has never attended before.

    Reception's own version of this exists and is unchanged — this is for a
    walk-in the doctor is looking at right now, so it goes straight to the
    new chart rather than to a booking screen, which is reception's job, not
    something this screen offers.
    """
    duplicates = None

    if request.method == "POST":
        form = clinic_forms.PatientRegistrationForm(request.POST)
        if form.is_valid():
            # Same rule as reception's own form — see register_patient in
            # views_reception.py — kept in one place, patients.matching, so
            # the two callers cannot end up disagreeing about what counts as
            # the same person.
            if not request.POST.get("confirm"):
                duplicates = matching.find_duplicates(
                    form.cleaned_data.get("first_name"),
                    form.cleaned_data.get("last_name"),
                    form.cleaned_data.get("phone"),
                )

        if form.is_valid() and not duplicates:
            patient = form.save()
            record(
                request, AuditAction.CREATE, obj=patient, patient=patient,
                description="Patient registered by doctor",
            )
            messages.success(
                request, f"Registered {patient.full_name} — {patient.patient_id}."
            )
            return redirect("doctor_patient_dashboard", patient_id=patient.patient_id)
    else:
        form = clinic_forms.PatientRegistrationForm()

    return render(request, "portal/doctor/register_patient.html", {
        "form": form,
        "duplicates": duplicates,
    })


@role_required(Role.DOCTOR)
def doctor_queue(request):
    """
    Just the queue table, for the polling refresh.

    The queue used to sit still until somebody pressed F5, so a patient sent
    through from reception did not appear at all — the doctor had no way of
    knowing anybody was waiting. The unconfirmed count sits in this same
    header, so it is refreshed on the same 15-second poll rather than only
    ever matching what was true when the page first loaded.
    """
    queue = services.todays_queue(request.user)
    return render(
        request, "portal/doctor/_queue.html",
        {
            "queue": queue,
            "waiting_count": _waiting_count(queue),
            "unconfirmed_count": _unconfirmed_count(request.user),
        },
    )


@role_required(Role.DOCTOR)
def unconfirmed_appointments(request):
    """
    Today's confirmed bookings not yet arrived, in a pop-up off Today's
    clinic — see ``_unconfirmed_count`` for why "unconfirmed" means this now.
    """
    visits = (
        Visit.objects.for_date()
        .filter(doctor=request.user, status=VisitStatus.CONFIRMED)
        .with_related()
        .order_by("scheduled_start")
    )
    return render(
        request, "portal/doctor/_unconfirmed_modal.html", {"visits": visits},
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

    active_visit = _current_visit(patient)

    context = {
        "patient": patient,
        "tabs": list(_visible_tabs(patient)),
        "active_tab": "summary",
        "active_visit": active_visit,
        "is_editable": _is_editable(patient),
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
    context["is_editable"] = _editable_for_tab(patient, tab)
    context.update(services.capped_add_flags(patient))
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
            return redirect("doctor_home")
        else:
            messages.success(request, f"{visit.patient.full_name} sent in.")
            # Straight to the chart, not back to the queue: sending a patient
            # in is the doctor saying they are ready for them right now, and
            # the file is the next thing they need — not a second click on
            # "Open" to get to it.
            return redirect("doctor_patient_dashboard", patient_id=visit.patient.patient_id)


@role_required(Role.DOCTOR)
def start_consultation(request, patient_id):
    """
    The doctor taking a patient in themselves — no reception, no appointment.

    Lives on the read-only chart, not the queue: "Open" from search and
    "Open" from the queue already land on the same read-only view, and this
    is the one action there that unlocks it, exactly as "Send in" does from
    the queue.

    If reception (or the doctor, via an earlier booking) already has this
    patient waiting, that visit is reused — the same transition "Send in"
    makes, so the two end up identical and the visit keeps whatever it
    already was (an appointment or a walk-in). Only when there is no visit
    at all does this create one, and only that one is marked direct: reception
    has no record of it, so its billing does not go through reception either.
    """
    patient = get_object_or_404(Patient, patient_id__iexact=patient_id)

    if request.method != "POST":
        return redirect("doctor_patient_dashboard", patient_id=patient.patient_id)

    visit = (
        patient.visits.filter(doctor=request.user, status=VisitStatus.ARRIVED)
        .order_by("scheduled_start")
        .first()
    )

    try:
        if visit is not None:
            visit.transition_to(VisitStatus.IN_CABIN, by_user=request.user)
        else:
            # Visit.objects.create() bypasses transition_to, so the
            # one-patient-per-cabin rule it enforces has to be checked here
            # instead — otherwise a doctor already with someone in the cabin
            # could start a second, ad-hoc consultation straight past it.
            occupied = (
                Visit.objects.filter(
                    doctor=request.user,
                    status=VisitStatus.IN_CABIN,
                    scheduled_start__date=timezone.localdate(),
                )
                .select_related("patient")
                .first()
            )
            if occupied is not None:
                raise InvalidTransition(
                    f"{occupied.patient.full_name} is already in your cabin. "
                    "Finish that consultation first."
                )
            start = scheduling.next_free_moment(request.user)
            now = timezone.now()
            visit = Visit.objects.create(
                patient=patient,
                doctor=request.user,
                scheduled_start=start,
                scheduled_end=start + scheduling.slot_length(),
                status=VisitStatus.IN_CABIN,
                is_direct=True,
                booked_by=request.user,
                arrived_at=now,
                entered_cabin_at=now,
            )
    except InvalidTransition as exc:
        messages.error(request, str(getattr(exc, "message", exc)))
        return redirect("doctor_patient_dashboard", patient_id=patient.patient_id)

    record(
        request, AuditAction.UPDATE, obj=visit, patient=patient,
        description="Consultation started directly by the doctor",
    )
    messages.success(request, f"Consultation started for {patient.full_name}.")
    return redirect("doctor_patient_dashboard", patient_id=patient.patient_id)


def _own_patients_only(request, chosen):
    """
    Pin the filters to this doctor's own patients.

    A doctor's analytics is implicitly their own patients, so the Doctor and
    Specialisation pickers were removed from the filter bar — but that is
    only true if it is enforced here too. Overwriting whatever the
    querystring says, rather than trusting the absent fields, keeps that true
    for a request built by hand as well as one built by the form.
    """
    chosen["doctor"] = request.user.pk
    chosen["specialisation"] = None
    return chosen


@role_required(Role.DOCTOR)
def analytics_view(request):
    """
    Research on the doctor's own patients: a full records table, and a
    dashboard built from the same filters.

    Every filter is optional. Leaving all of them blank is not an error state
    — it is "every patient of mine the clinic has", which is what the
    dashboard and the records table both show until the doctor narrows them.
    """
    tab = request.GET.get("tab", "dashboard")
    if tab not in ("dashboard", "download"):
        tab = "dashboard"

    chosen = _own_patients_only(request, analytics.parse_filters(request.GET))
    patients = analytics.filtered_patients(chosen)
    visits = analytics.visits_for(patients, chosen)

    context = {
        "tab": tab,
        "filters": chosen,
        "filter_qs": analytics.filter_querystring(request.GET),
        **analytics.filter_options(),
    }

    if tab == "dashboard":
        context.update(analytics.dashboard_stats(patients, visits, chosen))
    else:
        context["download_count"] = patients.count()
        context["rows"] = analytics.patient_rows(patients)

    return render(request, "portal/doctor/analytics.html", context)


@role_required(Role.DOCTOR)
def analytics_export(request):
    """
    The filtered patient set as a CSV, one row per patient.

    Written straight to the response rather than built in memory, the same
    reason reception's own bookings export is: a doctor pulling years of
    history should not be limited by how much of it fits in RAM.
    """
    chosen = _own_patients_only(request, analytics.parse_filters(request.GET))
    patients = analytics.filtered_patients(chosen).select_related().order_by(
        "last_name", "first_name"
    )

    response = HttpResponse(content_type="text/csv")
    stamp = timezone.localdate().strftime("%Y-%m-%d")
    response["Content-Disposition"] = f'attachment; filename="patient-analysis-{stamp}.csv"'

    writer = csv.writer(response)
    writer.writerow([
        settings.CLINIC.PATIENT_ID_LABEL, "Name", "Age", "Gender", "Phone",
        "Active diagnoses", "Visit count", "Last visit",
    ])

    count = 0
    for patient in patients.iterator(chunk_size=200):
        diagnoses = ", ".join(
            d.description for d in patient.diagnoses.filter(status="ACTIVE")[:5]
        )
        patient_visits = patient.visits.order_by("-scheduled_start")
        last_visit = patient_visits.first()
        writer.writerow([
            patient.patient_id,
            patient.full_name,
            patient.age_years,
            patient.get_sex_display(),
            patient.phone,
            diagnoses,
            patient_visits.count(),
            timezone.localtime(last_visit.scheduled_start).strftime("%Y-%m-%d")
            if last_visit else "",
        ])
        count += 1

    record(
        request, AuditAction.PRINT,
        description=f"Exported {count} patients from Analytics to CSV",
    )
    return response

    return redirect("doctor_home")
