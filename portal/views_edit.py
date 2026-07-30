"""
Editing records from the doctor's chart.

One generic view drives every edit button. A record type is described once in
``EDITABLE`` — its form, which tab shows it, and how a new one is attached to
the patient — rather than getting a view of its own.

The flow is the same everywhere:

  1. Button issues ``hx-get`` → this view returns the form in a modal.
  2. Form posts back to the same URL.
  3. On success the response clears the modal and swaps the refreshed tab and
     sidebar back into the page out-of-band, so the doctor sees the change
     without a reload and without losing their place.
"""

from dataclasses import dataclass
from typing import Callable

from django.conf import settings
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.models import Role
from accounts.permissions import role_required
from appointments.models import VisitStatus
from audit.services import record
from audit.models import AuditAction
from clinical.models import ClinicalNote, Diagnosis, Investigation
from patients.models import Patient, PatientHistory
from pharmacy.models import Prescription

from . import forms as clinic_forms
from . import services
from .views_doctor import TABS, _visible_tabs


@dataclass(frozen=True)
class Editable:
    """How one record type is created, edited and shown."""

    model: Callable
    form_class: Callable
    tab: str
    title: str
    add_title: str
    #: Attaches a newly created record to its patient before saving.
    attach: Callable = lambda obj, patient, request: None
    #: Whether a new record of this kind can be created at all.
    can_add: bool = True


def _attach_simple(obj, patient, request):
    obj.patient = patient


def _attach_investigation(obj, patient, request):
    obj.patient = patient
    obj.recorded_by = request.user


def _attach_measurement(obj, patient, request):
    obj.patient = patient
    obj.recorded_by = request.user


def _attach_note(obj, patient, request):
    """
    A note belongs to a visit. Attach it to the visit the patient is here for
    today; without one there is nothing to write a consultation note against.
    """
    visit = patient.visits.active().order_by("-scheduled_start").first()
    if visit is None:
        raise Http404("There is no open visit to attach a note to.")
    obj.visit = visit
    obj.patient = patient
    obj.author = request.user


def _attach_prescription(obj, patient, request):
    visit = patient.visits.active().order_by("-scheduled_start").first()
    if visit is None:
        raise Http404("There is no open visit to attach a prescription to.")
    obj.visit = visit
    obj.patient = patient
    obj.doctor = request.user


def _measurement_model():
    from growth.models import Measurement

    return Measurement


EDITABLE = {
    "patient": Editable(
        model=lambda: Patient,
        form_class=lambda: clinic_forms.PatientForm,
        tab="summary",
        title="Edit patient details",
        add_title="",
        can_add=False,
    ),
    "history": Editable(
        model=lambda: PatientHistory,
        form_class=lambda: clinic_forms.PatientHistoryForm,
        tab="summary",
        title="Edit background history",
        add_title="Add background history",
        attach=_attach_simple,
    ),
    "diagnosis": Editable(
        model=lambda: Diagnosis,
        form_class=lambda: clinic_forms.DiagnosisForm,
        tab="summary",
        title="Edit problem",
        add_title="Add problem",
        attach=_attach_simple,
    ),
    "investigation": Editable(
        model=lambda: Investigation,
        form_class=lambda: clinic_forms.InvestigationForm,
        tab="investigations",
        title="Edit result",
        add_title="Add investigation result",
        attach=_attach_investigation,
    ),
    "note": Editable(
        model=lambda: ClinicalNote,
        form_class=lambda: clinic_forms.ClinicalNoteForm,
        tab="notes",
        title="Edit clinical note",
        add_title="New clinical note",
        attach=_attach_note,
    ),
    "measurement": Editable(
        model=_measurement_model,
        form_class=clinic_forms.measurement_form_class,
        tab="growth",
        title="Edit measurement",
        add_title="Add measurement",
        attach=_attach_measurement,
    ),
    "prescription": Editable(
        model=lambda: Prescription,
        form_class=lambda: clinic_forms.PrescriptionForm,
        tab="prescriptions",
        title="Edit prescription",
        add_title="New prescription",
        attach=_attach_prescription,
    ),
}


def _refreshed_panels(request, patient, tab):
    """
    Render the tab and sidebar that the save affected, for an out-of-band swap.

    Returning both keeps the screen truthful: editing allergies must update the
    sidebar, not only the tab the doctor happened to be on.
    """
    template, builder = TABS[tab]
    tab_context = builder(patient) or {}
    tab_context["active_tab"] = tab
    tab_html = render_to_string(template, tab_context, request=request)

    sidebar_context = services.summary_context(patient)
    sidebar_html = render_to_string(
        "portal/doctor/_storyboard.html", sidebar_context, request=request
    )

    return render_to_string(
        "portal/doctor/_saved.html",
        {"tab_html": tab_html, "sidebar_html": sidebar_html},
        request=request,
    )


@role_required(Role.DOCTOR)
def edit_record(request, patient_id, kind, pk=None):
    """Create or edit one record on a patient's chart."""
    spec = EDITABLE.get(kind)
    if spec is None:
        raise Http404("Unknown record type")

    patient = get_object_or_404(Patient, patient_id__iexact=patient_id)
    model = spec.model()

    if kind == "patient":
        instance = patient
    elif pk is not None:
        instance = get_object_or_404(model, pk=pk, patient=patient)
    else:
        if not spec.can_add:
            raise Http404("This record cannot be created here")
        # A patient has at most one history record; editing beats duplicating.
        if kind == "history" and hasattr(patient, "history"):
            instance = patient.history
        else:
            instance = None

    form_class = spec.form_class()
    is_new = instance is None

    formset = None
    show_items = kind == "prescription"

    if request.method == "POST":
        form = form_class(request.POST, instance=instance)
        if show_items:
            formset = clinic_forms.PrescriptionItemFormSet(request.POST, instance=instance)

        forms_valid = form.is_valid() and (formset is None or formset.is_valid())

        if forms_valid:
            obj = form.save(commit=False)
            if is_new:
                spec.attach(obj, patient, request)
            obj.save()
            form.save_m2m()

            if formset is not None:
                formset.instance = obj
                formset.save()

            record(
                request,
                AuditAction.CREATE if is_new else AuditAction.UPDATE,
                obj=obj,
                patient=patient,
                description=f"{'Created' if is_new else 'Updated'} {model._meta.verbose_name}",
            )

            return HttpResponse(_refreshed_panels(request, patient, spec.tab))
    else:
        initial = {}
        if is_new and kind in ("investigation", "measurement", "diagnosis"):
            initial = {"performed_on": timezone.localdate(),
                       "measured_on": timezone.localdate(),
                       "diagnosed_on": timezone.localdate()}
        form = form_class(instance=instance, initial=initial)
        if show_items:
            formset = clinic_forms.PrescriptionItemFormSet(instance=instance)

    return render(
        request,
        "portal/doctor/_form_modal.html",
        {
            "patient": patient,
            "form": form,
            "formset": formset,
            "title": spec.add_title if is_new else spec.title,
            "action": request.path,
            "is_new": is_new,
        },
    )


@role_required(Role.DOCTOR)
def complete_consultation(request, patient_id):
    """
    End the consultation: record the fee, issue the prescription, hand over.

    This is the trigger the clinic described — one action that sets the fee,
    releases the prescription for printing and moves the visit to CONSULTED, so
    the patient appears on reception's billing list with everything they need.
    """
    patient = get_object_or_404(Patient, patient_id__iexact=patient_id)
    visit = patient.visits.filter(status=VisitStatus.IN_CABIN).order_by("-scheduled_start").first()

    if visit is None:
        raise Http404("This patient is not currently in the cabin.")

    charge = getattr(visit, "charge", None)
    prescription = getattr(visit, "prescription", None)

    if request.method == "POST":
        form = clinic_forms.ChargeForm(request.POST, instance=charge)
        if form.is_valid():
            with transaction.atomic():
                charge = form.save(commit=False)
                charge.visit = visit
                charge.patient = patient
                charge.set_by = request.user
                charge.save()

                # Issue whatever prescription exists; an empty one is still the
                # signal that the consultation is over.
                if prescription is None:
                    prescription = Prescription.objects.create(
                        visit=visit, patient=patient, doctor=request.user
                    )
                prescription.generate()

                visit.transition_to(VisitStatus.CONSULTED, by_user=request.user)

            record(
                request, AuditAction.UPDATE, obj=visit, patient=patient,
                description="Consultation completed; fee and prescription sent to reception",
            )
            return HttpResponse(_refreshed_panels(request, patient, "prescriptions"))
    else:
        initial = {}
        if charge is None:
            initial["consultation_fee"] = settings.CLINIC.DEFAULT_CONSULTATION_FEE
        form = clinic_forms.ChargeForm(instance=charge, initial=initial)

    return render(
        request,
        "portal/doctor/_complete_modal.html",
        {
            "patient": patient,
            "visit": visit,
            "form": form,
            "prescription": prescription,
            "action": request.path,
        },
    )


@role_required(Role.DOCTOR)
def generate_prescription(request, patient_id, pk):
    """
    Finalise a prescription and release it to reception.

    Until this happens the prescription is a draft the receptionist must not
    print — this is the handover point in the clinic's workflow.
    """
    patient = get_object_or_404(Patient, patient_id__iexact=patient_id)
    prescription = get_object_or_404(Prescription, pk=pk, patient=patient)

    if request.method == "POST":
        prescription.generate()
        record(
            request, AuditAction.UPDATE, obj=prescription, patient=patient,
            description="Generated prescription for reception",
        )

    return HttpResponse(_refreshed_panels(request, patient, "prescriptions"))
