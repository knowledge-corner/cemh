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
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.utils import timezone

from accounts.models import Role
from accounts.permissions import role_required
from appointments.models import VisitStatus
from audit.services import record
from audit.models import AuditAction
from billing.models import Payment, PaymentMethod, Receipt
from clinical.models import ClinicalNote, Diagnosis, Investigation, ReferenceLetter
from patients.models import Patient, PatientHistory
from pharmacy.models import Prescription

from . import forms as clinic_forms
from . import services
from .views_doctor import (
    TABS, _current_visit, _editable_for_tab, _has_visit_today, _is_editable, _visible_tabs,
)


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


def _blocked(request, title, explanation):
    """
    Refuse an action inside the dialog the user opened, and say why.

    Returned instead of a 404 where the record plainly exists and the user can
    already see it on the board: a page that vanishes teaches nothing, and the
    doctor standing there needs to know it is the wrong screen, not a broken
    one. 403 so it is refused for a script posting at the endpoint too.
    """
    return HttpResponseForbidden(
        render_to_string(
            "portal/doctor/_blocked_modal.html",
            {"title": title, "explanation": explanation},
            request=request,
        )
    )


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
    """
    A prescription no longer needs an open visit — a doctor can write one for
    a patient at any time, printing it themselves rather than routing it
    through reception.

    Where a visit *is* open, still attach to it if that visit does not already
    have one: reception's own per-visit print reads ``visit.prescription`` as
    a single object, so at most one prescription may belong to any one visit.
    """
    obj.patient = patient
    obj.doctor = request.user
    visit = patient.visits.active().order_by("-scheduled_start").first()
    if visit is not None and getattr(visit, "prescription", None) is None:
        obj.visit = visit


def _attach_reference_letter(obj, patient, request):
    """
    Attach to the current consultation the same way a prescription does — see
    ``_attach_prescription`` — so the one-per-consultation cap (enforced in
    ``edit_record``) has something to check against.
    """
    obj.patient = patient
    obj.doctor = request.user
    obj.visit = services.consultation_anchor(patient)


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
    "reference_letter": Editable(
        model=lambda: ReferenceLetter,
        form_class=lambda: clinic_forms.ReferenceLetterForm,
        tab="reference_letters",
        title="Edit reference letter",
        add_title="New reference letter",
        attach=_attach_reference_letter,
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
    # A save just went through, which edit_record already gated on
    # _editable_for_tab — reference letters are the one kind that can be
    # saved on an otherwise read-only chart, so the flag for this tab has to
    # be recomputed the same way rather than assumed True.
    tab_context["is_editable"] = _editable_for_tab(patient, tab)
    tab_context.update(services.capped_add_flags(patient))
    tab_html = render_to_string(template, tab_context, request=request)

    sidebar_context = services.summary_context(patient)
    sidebar_context["is_editable"] = _is_editable(patient)
    sidebar_html = render_to_string(
        "portal/doctor/_storyboard.html", sidebar_context, request=request
    )

    # The header's status badge and its Complete/End/Start consultation
    # button go stale after a save that moved the visit on — most notably
    # Complete/End consultation itself, which is what this repaints for.
    header_html = render_to_string(
        "portal/doctor/_chart_header_actions.html",
        {
            "patient": patient,
            "active_visit": _current_visit(patient),
            "has_visit_today": _has_visit_today(patient, request.user),
        },
        request=request,
    )

    return render_to_string(
        "portal/doctor/_saved.html",
        {"tab_html": tab_html, "sidebar_html": sidebar_html, "header_html": header_html},
        request=request,
    )


#: Records capped at one per consultation, and locked to editing only the
#: patient's most recent one — see services.can_add_consultation_record and
#: services.is_latest_consultation_record.
_CONSULTATION_CAPPED_NOUNS = {
    "note": "clinical note",
    "prescription": "prescription",
    "reference_letter": "reference letter",
}


@role_required(Role.DOCTOR)
def edit_record(request, patient_id, kind, pk=None):
    """Create or edit one record on a patient's chart."""
    spec = EDITABLE.get(kind)
    if spec is None:
        raise Http404("Unknown record type")

    patient = get_object_or_404(Patient, patient_id__iexact=patient_id)

    # The clinical record proper — notes and prescriptions — is read-only
    # until the patient is sent in, enforced here too and not just by hiding
    # the button, since this is a POST endpoint a doctor's browser can be
    # pointed at directly. Everything else on the chart (the patient's own
    # details, problem list, investigation results, growth measurements,
    # reference letters) is reference data a doctor may reasonably need to
    # correct between visits, not only mid-consultation — see
    # views_doctor.ALWAYS_EDITABLE_TABS, which this mirrors exactly so the
    # button a doctor sees and what the server will actually accept can't
    # drift apart.
    if not _editable_for_tab(patient, spec.tab):
        return _blocked(
            request,
            "This file is read-only",
            f"{patient.full_name} has not been sent in yet. Use \"Send in\" "
            "from today's queue, or \"Start consultation\", before editing the file.",
        )

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

    noun = _CONSULTATION_CAPPED_NOUNS.get(kind)
    if noun is not None:
        if is_new:
            if not services.can_add_consultation_record(patient, kind):
                return _blocked(
                    request,
                    f"This consultation already has a {noun}",
                    f"Only one {noun} can be added per consultation — edit "
                    "the existing one instead of adding another.",
                )
        elif not services.is_latest_consultation_record(patient, kind, instance.pk):
            return _blocked(
                request,
                "This record is locked",
                f"Only the most recent {noun} can be edited. Older ones are "
                "kept exactly as written at the time.",
            )

    formset = None
    show_items = kind == "prescription"

    if request.method == "POST":
        form = form_class(request.POST, instance=instance, patient=patient)
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
        form = form_class(instance=instance, initial=initial, patient=patient)
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
            "kind": kind,
        },
    )


@role_required(Role.DOCTOR)
def complete_consultation(request, patient_id):
    """
    End the consultation: record the fee and the work done, move the visit on.

    Reference letters stay the doctor's own documents, written, edited and
    printed from their own tab independently of this action. The visit's
    prescription is different: finishing the consultation is what makes it
    final, so completing here also releases it to reception — a doctor does
    not separately decide to "send" it. It still moves the visit to
    CONSULTED, which is what puts the patient on reception's billing list.
    """
    patient = get_object_or_404(Patient, patient_id__iexact=patient_id)
    visit = (
        patient.visits.filter(status=VisitStatus.IN_CABIN)
        .select_related("doctor")
        .order_by("-scheduled_start")
        .first()
    )

    if visit is None:
        raise Http404("This patient is not currently in the cabin.")

    # KAN-5 FR-2 and AC-3. Only the doctor who has the patient in front of them
    # may end the consultation: the fee and the signature on the record both
    # belong to whoever saw the patient. Said plainly rather than raising a
    # 404 — every doctor can already see the whole board, so there is nothing
    # to conceal here.
    if visit.doctor_id != request.user.id:
        return _blocked(
            request,
            "This is not your consultation",
            f"{patient.full_name} is with {visit.doctor.display_name}. "
            "Only the doctor seeing the patient can complete the consultation.",
        )

    charge = getattr(visit, "charge", None)

    if request.method == "POST":
        form = clinic_forms.ChargeForm(request.POST, instance=charge)
        if form.is_valid():
            with transaction.atomic():
                charge = form.save(commit=False)
                charge.visit = visit
                charge.patient = patient
                charge.set_by = request.user
                charge.save()

                visit.transition_to(VisitStatus.CONSULTED, by_user=request.user)

                prescription = getattr(visit, "prescription", None)
                if prescription is not None:
                    prescription.generate()

            record(
                request, AuditAction.UPDATE, obj=visit, patient=patient,
                description="Consultation completed; fee recorded",
            )
            return HttpResponse(_refreshed_panels(request, patient, "summary"))
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
            "action": request.path,
        },
    )


@role_required(Role.DOCTOR)
def end_consultation(request, patient_id):
    """
    End a consultation the doctor started themselves (KAN task, "direct"
    consultations).

    Reception never saw this visit, so there is no billing worklist for it to
    land on. The fee entered here is taken to be collected on the spot —
    finishing this dialog records the payment and issues the receipt in the
    same step, and closes the visit out entirely, rather than leaving a
    second button for somebody else to press.
    """
    patient = get_object_or_404(Patient, patient_id__iexact=patient_id)
    visit = (
        patient.visits.filter(status=VisitStatus.IN_CABIN, is_direct=True)
        .select_related("doctor")
        .order_by("-scheduled_start")
        .first()
    )

    if visit is None:
        raise Http404("This patient has no direct consultation in the cabin.")

    # Same rule as complete_consultation: the fee and the signature both
    # belong to whoever actually saw the patient.
    if visit.doctor_id != request.user.id:
        return _blocked(
            request,
            "This is not your consultation",
            f"{patient.full_name} is with {visit.doctor.display_name}. "
            "Only the doctor seeing the patient can end the consultation.",
        )

    charge = getattr(visit, "charge", None)

    if request.method == "POST":
        form = clinic_forms.ChargeForm(request.POST, instance=charge)
        if form.is_valid():
            with transaction.atomic():
                charge = form.save(commit=False)
                charge.visit = visit
                charge.patient = patient
                charge.set_by = request.user
                charge.save()

                visit.transition_to(VisitStatus.CONSULTED, by_user=request.user)

                prescription = getattr(visit, "prescription", None)
                if prescription is not None:
                    prescription.generate()

                # A payment cannot be zero, so a free consultation has
                # nothing to record here — the charge itself already reads as
                # settled with nothing owed, the same as reception's own
                # "closed with no fee" case.
                if charge.total > 0:
                    payment = Payment.objects.create(
                        charge=charge,
                        amount=charge.total,
                        method=PaymentMethod.CASH,
                        received_by=request.user,
                        notes="Collected directly by the doctor",
                    )
                    Receipt.objects.create(payment=payment)

                visit.transition_to(VisitStatus.BILLED, by_user=request.user)
                visit.transition_to(VisitStatus.COMPLETED, by_user=request.user)

            record(
                request, AuditAction.UPDATE, obj=visit, patient=patient,
                description="Direct consultation ended; fee taken and billed by the doctor",
            )
            return HttpResponse(_refreshed_panels(request, patient, "summary"))
    else:
        initial = {}
        if charge is None:
            initial["consultation_fee"] = settings.CLINIC.DEFAULT_CONSULTATION_FEE
        form = clinic_forms.ChargeForm(instance=charge, initial=initial)

    return render(
        request,
        "portal/doctor/_end_consultation_modal.html",
        {
            "patient": patient,
            "visit": visit,
            "form": form,
            "action": request.path,
        },
    )
