"""
Edit forms for the doctor's chart.

Every form here follows the same shape so the generic edit view in
``views_edit.py`` can drive all of them, and so a new editable record type is a
form class plus a registry entry rather than a new view.
"""

import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django import forms
from django.db import transaction
from django.urls import reverse, reverse_lazy
from django.utils.text import slugify
from django.forms import inlineformset_factory
from django.utils import timezone

from accounts.models import (
    DoctorProfile, Role, Specialisation, User, phone_validator,
)
from appointments import scheduling, signoff
from appointments import weekdays as weekday_codes
from appointments.models import Visit, VisitStatus
from billing.models import Charge, Payment
from clinical.models import ClinicalNote, Diagnosis, Investigation, LabTest, ReferenceLetter
from patients.models import Patient, PatientHistory, age_in_years
from pharmacy.models import Prescription, PrescriptionItem

INPUT = {"class": "input"}
TEXTAREA = {"class": "input", "rows": 3}
DATE = {"class": "input", "type": "date"}


class StyledModelForm(forms.ModelForm):
    """Applies the shared field styling without repeating widget attrs per form."""

    def __init__(self, *args, **kwargs):
        # The patient this record belongs to, when the view constructing the
        # form knows it (edit_record always does) — most forms have no use
        # for it, but one needing it (InvestigationForm, to build a
        # patient-scoped lookup URL it can't know at class-definition time)
        # shouldn't need edit_record to know which forms care.
        self.patient = kwargs.pop("patient", None)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                continue
            existing = widget.attrs.get("class", "")
            if "input" not in existing:
                widget.attrs["class"] = (existing + " input").strip()


#: Nobody is 120. A date of birth further back than this is a typed year, and
#: letting it through puts a nonsense age on the chart and the growth curve.
MAX_PLAUSIBLE_AGE_YEARS = 120

#: Under this, the clinic needs to know who brings the patient in.
GUARDIAN_AGE = 18


class PatientForm(StyledModelForm):
    """
    The whole patient record, as the doctor's chart edits it.

    Deliberately still the full field set. Trimming the registration form is
    about what reception is asked for with a patient standing in front of them;
    editing an existing record is a different job, done later and with the notes
    open, and the blood group and address have to be reachable somewhere.
    """

    class Meta:
        model = Patient
        fields = [
            "first_name", "last_name", "date_of_birth", "sex", "blood_group",
            "phone", "alternate_phone", "email",
            "guardian_name", "guardian_relation", "guardian_phone",
            "address", "city", "pincode", "referred_by",
        ]
        widgets = {"date_of_birth": forms.DateInput(attrs=DATE, format="%Y-%m-%d")}
        labels = {"sex": "Gender"}


class PatientRegistrationForm(StyledModelForm):
    """
    Registering a patient at the desk.

    Five fields, and two more only when the patient is a child. Everything the
    clinic does not need at that moment came off: the columns are still on the
    model, still edited from the chart and still in the admin, so nothing is
    lost — but a receptionist with a patient in front of her is not asked for a
    blood group nobody has taken yet.
    """

    class Meta:
        model = Patient
        fields = [
            "first_name", "last_name", "date_of_birth", "sex", "phone",
            "guardian_name", "guardian_relation",
        ]
        widgets = {"date_of_birth": forms.DateInput(attrs=DATE, format="%Y-%m-%d")}
        labels = {
            "sex": "Gender",
            "phone": "Phone",
        }
        help_texts = {
            # The ticket contradicts itself on whether this becomes "Guardian
            # phone" for a minor. It stays one field with one label — see the
            # note on the ticket — and the help text carries the meaning
            # instead, which is true at any age and does not move as a date of
            # birth is typed.
            "phone": "The number the clinic should ring. For a child, the guardian's.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Guardian details are asked for only when the patient is a child. The
        # section is hidden by the page until a date of birth says otherwise;
        # required-ness is decided here, where it cannot be skipped by posting
        # straight at the endpoint.
        for name in ("guardian_name", "guardian_relation"):
            self.fields[name].required = False

        # The model allows a blank surname; this form does not, because the
        # surname is part of the key the duplicate check compares on and a
        # missing one weakens it to a first name and a number.
        #
        # This does block registering somebody who genuinely has one name, which
        # is not rare here. The ticket asks for it and raises the same worry in
        # its own Open Questions — flagged there rather than decided quietly.
        self.fields["last_name"].required = True

        today = timezone.localdate()
        self.fields["date_of_birth"].widget.attrs["max"] = today.isoformat()
        self.fields["date_of_birth"].widget.attrs["min"] = today.replace(
            year=today.year - MAX_PLAUSIBLE_AGE_YEARS
        ).isoformat()

    def clean_date_of_birth(self):
        dob = self.cleaned_data["date_of_birth"]
        today = timezone.localdate()

        if dob > today:
            raise forms.ValidationError("A date of birth cannot be in the future.")

        if dob < today.replace(year=today.year - MAX_PLAUSIBLE_AGE_YEARS):
            raise forms.ValidationError(
                f"That is over {MAX_PLAUSIBLE_AGE_YEARS} years ago. Check the year."
            )
        return dob

    def clean(self):
        cleaned = super().clean()
        dob = cleaned.get("date_of_birth")
        if dob is None:
            return cleaned

        if age_in_years(dob) < GUARDIAN_AGE:
            for name, label in (
                ("guardian_name", "guardian's name"),
                ("guardian_relation", "relation to the patient"),
            ):
                if not (cleaned.get(name) or "").strip():
                    self.add_error(
                        name,
                        f"This patient is under {GUARDIAN_AGE}, so the {label} "
                        f"is needed.",
                    )
        else:
            # An adult carries no guardian, even if the fields were filled in
            # before the date of birth was corrected. Discarded here rather than
            # in the page, so a stale value cannot be posted back.
            cleaned["guardian_name"] = ""
            cleaned["guardian_relation"] = ""

        return cleaned


class PatientHistoryForm(StyledModelForm):
    class Meta:
        model = PatientHistory
        fields = [
            "presenting_complaints", "past_medical_history", "family_history",
            "birth_history", "allergies", "current_medications",
            "surgical_history", "lifestyle_notes",
        ]
        widgets = {name: forms.Textarea(attrs=TEXTAREA) for name in fields}


class DiagnosisForm(StyledModelForm):
    class Meta:
        model = Diagnosis
        fields = ["description", "icd10_code", "status", "diagnosed_on", "resolved_on", "notes"]
        widgets = {
            # Typing here looks up matching ICD-10 codes (see
            # portal.views_doctor.icd10_search and _icd10_results.html, which
            # is what hx-target="#icd10-suggestions" swaps into — that
            # container lives in _form_modal.html, next to this field, only
            # when the modal is showing a diagnosis). Picking a suggestion
            # fills icd10_code too; typing something with no match just saves
            # as free text, same as before this existed.
            "description": forms.TextInput(attrs={
                **INPUT,
                "autocomplete": "off",
                "hx-get": reverse_lazy("doctor_icd10_search"),
                "hx-trigger": "keyup changed delay:200ms, search",
                "hx-target": "#icd10-suggestions",
            }),
            "icd10_code": forms.TextInput(attrs={
                **INPUT, "placeholder": "Filled in from the list above, or type your own",
            }),
            "diagnosed_on": forms.DateInput(attrs=DATE, format="%Y-%m-%d"),
            "resolved_on": forms.DateInput(attrs=DATE, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs=TEXTAREA),
        }


class ReferenceLetterForm(StyledModelForm):
    class Meta:
        model = ReferenceLetter
        fields = ["to", "note"]
        widgets = {
            "to": forms.TextInput(attrs=INPUT),
            "note": forms.Textarea(attrs={**TEXTAREA, "rows": 10}),
        }


#: Suggestions for the unit field's <datalist> — not an enforced choice list.
#: A lab report can legitimately use a unit not on this list, and an existing
#: record's unit must never be blanked just because it doesn't match one of
#: these exactly, so this stays advisory rather than a ChoiceField.
LAB_UNIT_CHOICES = [
    "mg/dL", "g/dL", "mg/L", "g/L", "µg/dL", "µg/L", "ng/mL", "ng/dL", "ng/L",
    "pg/mL", "pg/dL", "mmol/L", "µmol/L", "nmol/L", "pmol/L",
    "U/L", "IU/L", "mIU/L", "µIU/mL", "IU/mL", "mIU/mL",
    "mEq/L", "%", "mm/hr", "sec",
    "cells/µL", "/µL", "x10^3/µL", "x10^6/µL", "x10^9/L", "x10^12/L", "fL",
    "mg/24hr", "g/24hr", "mg/g creatinine",
]


class InvestigationForm(StyledModelForm):
    #: Set by picking a suggestion from the test-name autocomplete (see
    #: portal.views_doctor.lab_test_search and _lab_test_results.html).
    #: Not required: a test typed free-hand, with no match in the master
    #: list, still saves — this only records which master-list test was
    #: actually meant, when one was. Carries its own hx-get so picking a
    #: suggestion can trigger the evaluate lookup directly (see
    #: _lab_test_results.html's pickLabTest) — value and unit still trigger
    #: it too, for a value corrected or a unit changed after that first fill.
    lab_test = forms.ModelChoiceField(
        queryset=LabTest.objects.all(), required=False, widget=forms.HiddenInput(attrs={
            "hx-trigger": "change",
            "hx-target": "#lab-evaluation",
            "hx-include": "#id_value, #id_unit",
        }),
    )

    class Meta:
        model = Investigation
        fields = [
            "lab_test", "test_name", "category", "performed_on", "value",
            "unit", "reference_range", "is_abnormal", "lab_name", "notes",
        ]
        widgets = {
            # Typing here looks up matching tests from the master list (see
            # portal.views_doctor.lab_test_search) — picking one fills
            # lab_test above, which fires the same evaluate lookup value and
            # unit trigger themselves, so category, unit and reference range
            # show up before a value is even typed.
            "test_name": forms.TextInput(attrs={
                **INPUT,
                "autocomplete": "off",
                "hx-get": reverse_lazy("doctor_lab_test_search"),
                "hx-trigger": "keyup changed delay:200ms, search",
                "hx-target": "#lab-test-suggestions",
            }),
            "performed_on": forms.DateInput(attrs=DATE, format="%Y-%m-%d"),
            # value and unit both trigger the same "evaluate against the
            # reference range" lookup — value because that's what gets
            # compared (parsed as a number where it can be; see save() below
            # for the value_numeric it derives), unit because changing it
            # (say, correcting mg/dL to mmol/L) can change what the
            # comparison should be. The hx-get URL itself is patient-scoped
            # (the match depends on the patient's age and sex) and so can't
            # be known here at class-definition time — see __init__, which
            # fills it in from self.patient.
            "value": forms.TextInput(attrs={
                **INPUT,
                "hx-trigger": "keyup changed delay:400ms, change",
                "hx-target": "#lab-evaluation",
                "hx-include": "#id_lab_test, #id_unit",
            }),
            "unit": forms.TextInput(attrs={
                **INPUT,
                "list": "lab-unit-options",
                "hx-trigger": "change",
                "hx-target": "#lab-evaluation",
                "hx-include": "#id_lab_test, #id_value",
            }),
            "notes": forms.Textarea(attrs={**TEXTAREA, "rows": 2}),
        }
        help_texts = {
            "value": "A number where possible (e.g. 5.69) — that's what gets trended "
                     "and compared to the reference range. Free text (e.g. \"Negative\") "
                     "is fine when a test doesn't report one.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.unit_choices = LAB_UNIT_CHOICES
        if self.patient is not None:
            evaluate_url = reverse("doctor_lab_evaluate", args=[self.patient.patient_id])
            self.fields["lab_test"].widget.attrs["hx-get"] = evaluate_url
            self.fields["value"].widget.attrs["hx-get"] = evaluate_url
            self.fields["unit"].widget.attrs["hx-get"] = evaluate_url

    def save(self, commit=True):
        # The one field the doctor actually fills in. value_numeric is
        # derived here rather than typed separately — auto-flagging
        # (clinical.lab_reference.evaluate_value) and the analytics trend
        # chart both read value_numeric directly, so this is what keeps them
        # working without asking for a second box.
        instance = super().save(commit=False)
        try:
            instance.value_numeric = Decimal(instance.value)
        except (InvalidOperation, TypeError):
            instance.value_numeric = None
        if commit:
            instance.save()
        return instance


class ClinicalNoteForm(StyledModelForm):
    """
    Two boxes only: what the doctor wants on record for themselves, and what
    should go on the printed prescription for this visit. The older complaint
    / examination / assessment / plan / vitals fields still exist on the
    model — see :class:`clinical.models.ClinicalNote` — but are not offered
    here any more.
    """

    class Meta:
        model = ClinicalNote
        fields = ["clinical_notes", "prescription_note"]
        widgets = {
            "clinical_notes": forms.Textarea(attrs=TEXTAREA),
            "prescription_note": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "clinical_notes": "Clinical notes (doctor reference)",
            "prescription_note": "Notes to print on prescription",
        }


#: Displayed as a dropdown rather than typed, per the clinic's request — a
#: number this small is quicker to pick than to type correctly every time.
FOLLOW_UP_NUMBER_CHOICES = [("", "—")] + [(n, n) for n in range(1, 13)]


class PrescriptionForm(StyledModelForm):
    """
    Advice no longer lives here — see ``ClinicalNoteForm.prescription_note``,
    which is what the printed prescription now shows for the visit it was
    written on. The follow-up date became "in N months/years" because a
    doctor thinks in a duration, not a calendar date, at the point they set it
    — the print sheet works the calendar date out from today.
    """

    follow_up_number = forms.TypedChoiceField(
        choices=FOLLOW_UP_NUMBER_CHOICES, coerce=int, required=False, empty_value=None,
        widget=forms.Select(attrs=INPUT),
    )

    class Meta:
        model = Prescription
        fields = [
            "investigations_advised", "follow_up_number", "follow_up_unit", "follow_up_notes",
            "scanned_file",
        ]
        widgets = {
            "investigations_advised": forms.Textarea(attrs=TEXTAREA),
            "follow_up_unit": forms.Select(attrs=INPUT),
            "follow_up_notes": forms.TextInput(attrs=INPUT),
            "scanned_file": forms.ClearableFileInput(attrs={"accept": "image/*,.pdf"}),
        }
        labels = {
            "scanned_file": "Handwritten prescription (photo or scan)",
            "follow_up_number": "Follow up after",
            "follow_up_unit": " ",
            "follow_up_notes": "Follow-up notes",
        }
        help_texts = {
            "scanned_file": "Attach instead of typing the medication list below, if this "
                            "prescription was written on paper.",
        }


class PrescriptionScanForm(StyledModelForm):
    """
    Reception's own narrow edit of a prescription: attach or replace the
    scanned file, nothing else. Used from the settled-visit page, which is
    otherwise entirely read-only — see
    ``views_reception.upload_prescription_scan`` for why this one exception
    exists.
    """

    class Meta:
        model = Prescription
        fields = ["scanned_file"]
        widgets = {
            "scanned_file": forms.ClearableFileInput(attrs={"accept": "image/*,.pdf"}),
        }
        labels = {"scanned_file": "Photo or scan of the prescription"}


PrescriptionItemFormSet = inlineformset_factory(
    Prescription,
    PrescriptionItem,
    fields=["drug_name", "strength", "dosage", "frequency", "duration", "instructions"],
    extra=3,
    can_delete=True,
    widgets={
        name: forms.TextInput(attrs=INPUT)
        for name in ["drug_name", "strength", "dosage", "frequency", "duration", "instructions"]
    },
)


class ChargeForm(StyledModelForm):
    """
    The fee, entered by the doctor as the consultation ends.

    Recorded here rather than at the desk so the receptionist is told what to
    collect instead of having to ask.
    """

    class Meta:
        model = Charge
        fields = ["consultation_fee", "procedure_fee", "discount", "notes"]
        widgets = {"notes": forms.TextInput(attrs=INPUT)}
        labels = {
            "consultation_fee": "Consultation fee",
            "procedure_fee": "Procedure / other charges",
            "discount": "Discount",
            "notes": "Work done",
        }
        help_texts = {
            "notes": "Required if the visit is being made free of charge.",
        }

    # KAN-5 AC-5 and the zero-or-negative edge case. The doctor is typing into
    # this box at the end of a consultation with the next patient waiting, so
    # the numbers are checked here rather than being noticed at the desk.

    def _not_negative(self, name):
        value = self.cleaned_data.get(name)
        if value is not None and value < 0:
            raise forms.ValidationError("This cannot be a negative amount.")
        return value

    def clean_consultation_fee(self):
        return self._not_negative("consultation_fee")

    def clean_procedure_fee(self):
        return self._not_negative("procedure_fee")

    def clean_discount(self):
        return self._not_negative("discount")

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned

        fees = cleaned.get("consultation_fee", 0) + cleaned.get("procedure_fee", 0)
        discount = cleaned.get("discount", 0)

        if discount > fees:
            raise forms.ValidationError(
                "The discount is more than the fee, which would leave the clinic "
                "owing the patient money. Reduce the discount."
            )

        # A free visit is a real thing — a colleague's child, a repeat wound
        # check — so zero is not refused outright. It has to be deliberate
        # though, because a bill of nothing is otherwise indistinguishable from
        # a fee that was never typed in.
        if fees - discount <= 0 and not cleaned.get("notes"):
            raise forms.ValidationError(
                "This bill comes to nothing. If the visit really is free of "
                "charge, say why in the notes; otherwise enter the fee."
            )

        return cleaned


class PaymentForm(StyledModelForm):
    """
    Money taken at the desk.

    Given the charge it is being taken against, so the amount can be checked
    against what is actually outstanding. Part payments are allowed — the visit
    stays on the billing list until nothing is left — but paying more than the
    bill, or a negative amount, is not: a refund is a decision somebody makes,
    not a minus sign typed into the payment box.
    """

    class Meta:
        model = Payment
        fields = ["amount", "method", "reference", "notes"]
        widgets = {"notes": forms.TextInput(attrs=INPUT)}
        help_texts = {"reference": "UPI reference or card approval code, if any."}

    def __init__(self, *args, charge=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.charge = charge

    def clean_amount(self):
        amount = self.cleaned_data["amount"]

        if amount <= 0:
            raise forms.ValidationError(
                "Enter the amount taken. A payment of nothing would still issue "
                "a receipt number."
            )

        if self.charge is not None:
            outstanding = self.charge.balance
            if outstanding <= 0:
                raise forms.ValidationError(
                    "This bill is already paid in full. Nothing further is due."
                )
            if amount > outstanding:
                raise forms.ValidationError(
                    f"That is more than the {outstanding} outstanding on this "
                    "bill. Enter what was actually taken."
                )

        return amount


class BookingForm(forms.Form):
    """
    Booking taken at the desk or over the phone.

    A plain form rather than a ModelForm: the slot the receptionist picks is a
    single choice that has to be expanded into a start and an end, and the
    doctor and date drive which slots are offered at all.
    """

    patient = forms.ModelChoiceField(
        queryset=Patient.objects.filter(is_active=True),
        widget=forms.HiddenInput,
        error_messages={"required": "Choose a patient, or register a new one."},
    )
    doctor = forms.ModelChoiceField(
        queryset=None,
        empty_label="Select a doctor",
        widget=forms.Select(attrs=INPUT),
    )
    day = forms.DateField(
        label="Date",
        required=False,
        widget=forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
        # `min` and `max` are set per instance in __init__ — the booking window
        # moves with the calendar, and a bound computed at import time would be
        # yesterday's by the following morning.
    )
    slot = forms.DateTimeField(required=False, widget=forms.HiddenInput)
    reason = forms.CharField(
        max_length=300, required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "e.g. Thyroid review"}),
    )
    is_follow_up = forms.BooleanField(required=False, label="Follow-up visit")
    #: KAN task #22. A patient standing at the desk with no appointment —
    #: "day" and "slot" are required for everyone else, but this one is not
    #: told to come back after picking a time; the soonest free moment is
    #: found for them, so those two fields are optional here instead and
    #: filled in by clean().
    walk_in = forms.BooleanField(
        required=False,
        label="Walk-in — see them today, as soon as the doctor is free",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import Role, User

        self.fields["doctor"].queryset = User.objects.filter(
            role=Role.DOCTOR, is_active=True
        )

        # Stop the date picker from offering a day that will only be refused.
        # clean() still checks both ends: `min` is a courtesy from the browser,
        # not a control — a typed date or a crafted post walks straight past it.
        opens, closes = scheduling.booking_window()
        self.fields["day"].widget.attrs["min"] = opens.isoformat()
        self.fields["day"].widget.attrs["max"] = closes.isoformat()

    def clean(self):
        cleaned = super().clean()
        doctor = cleaned.get("doctor")
        slot = cleaned.get("slot")
        day = cleaned.get("day")

        if cleaned.get("walk_in"):
            if not doctor:
                return cleaned
            today = timezone.localdate()
            # An unsigned earlier day holds new arrivals of any kind, the same
            # rule move_visit enforces for "Mark arrived" — a walk-in is
            # exactly that, just taken a step earlier, and letting one through
            # would be a back door round the same hold.
            unsigned = signoff.is_due()
            if unsigned:
                self.add_error(
                    "doctor",
                    f"{unsigned:%d %B} has not been signed off yet, so a "
                    "walk-in cannot be taken in until it is. Sign off from "
                    "All bookings first.",
                )
                return cleaned
            # A holiday is the one thing that still refuses a walk-in: it is
            # the clinic recorded as shut for everyone, not a question about
            # one doctor's own hours. No per-doctor schedule check beyond
            # that — reception is looking at the doctor right now and knows
            # they are free to see this patient, whatever the calendar does
            # or does not say about it. Only the doctor's other active
            # visits are consulted, so two walk-ins can never land on the
            # same moment and trip the database's no-double-booking
            # constraint.
            if scheduling.is_holiday(today):
                self.add_error(
                    "doctor",
                    "The clinic is closed today, so there is no free slot "
                    "left today. Try another day, or book ahead instead of "
                    "a walk-in.",
                )
                return cleaned
            cleaned["day"] = today
            cleaned["slot"] = scheduling.next_free_moment(doctor)
            return cleaned

        if not day:
            self.add_error("day", "Choose a date.")
        if not slot:
            self.add_error("slot", "Choose a time.")
        if not (doctor and slot and day):
            return cleaned

        local_slot = timezone.localtime(slot)
        if local_slot.date() != day:
            self.add_error("slot", "That time is not on the selected date.")
            return cleaned

        # A date that has already gone is a mis-key, never an intention. This
        # was previously only discouraged by which slots were offered, which a
        # typed-in or back-dated date walked straight past.
        today = timezone.localdate()
        if day < today:
            self.add_error(
                "day",
                "That date has passed. Appointments can only be booked for "
                "today or a later date.",
            )
            return cleaned

        _, horizon = scheduling.booking_window()
        if day > horizon:
            self.add_error("day", f"Bookings are only taken up to {horizon:%d %b %Y}.")
            return cleaned

        if not scheduling.is_working_day(day, doctor):
            self.add_error("day", f"{doctor.display_name} is not consulting on that day.")
            return cleaned

        if slot <= timezone.now():
            self.add_error("slot", "That time has already passed. Choose a later one.")
            return cleaned

        # Re-check availability at submission time. The database constraint is
        # the real guarantee; this exists to give a readable error instead of
        # an IntegrityError when somebody simply took the slot first.
        free = {start for start, _ in scheduling.available_slots(doctor, day)}
        if slot not in free:
            self.add_error("slot", "That slot is no longer free. Please choose another time.")

        return cleaned

    def save(self, booked_by=None):
        slot = self.cleaned_data["slot"]
        walk_in = self.cleaned_data.get("walk_in")
        return Visit.objects.create(
            patient=self.cleaned_data["patient"],
            doctor=self.cleaned_data["doctor"],
            scheduled_start=slot,
            scheduled_end=slot + scheduling.slot_length(),
            reason=self.cleaned_data.get("reason", ""),
            is_follow_up=self.cleaned_data.get("is_follow_up", False),
            booked_by=booked_by,
            # Picking a slot off the grid is itself the confirmation — there is
            # no separate phone call to wait on, so a booking starts CONFIRMED
            # rather than sitting behind a step nobody performs. A walk-in
            # skips even that: standing at the desk right now, they go
            # straight into today's waiting room instead.
            status=VisitStatus.ARRIVED if walk_in else VisitStatus.CONFIRMED,
            arrived_at=timezone.now() if walk_in else None,
            is_walk_in=bool(walk_in),
        )


def measurement_form_class():
    """
    Built lazily because ``growth`` is an optional app.

    Importing its model at module level would break a clinic that has removed
    growth charts — exactly the coupling the optional-app design avoids.
    """
    from growth.models import Measurement

    class MeasurementForm(StyledModelForm):
        class Meta:
            model = Measurement
            fields = [
                "measured_on", "height_cm", "weight_kg", "head_circumference_cm",
                "waist_cm", "puberty_stage", "bone_age_years",
                "mother_height_cm", "father_height_cm", "notes",
            ]
            widgets = {
                "measured_on": forms.DateInput(attrs=DATE, format="%Y-%m-%d"),
                "notes": forms.Textarea(attrs={**TEXTAREA, "rows": 2}),
            }
            help_texts = {
                "mother_height_cm": "Used to compute the mid-parental target height.",
                "bone_age_years": "From an X-ray, e.g. 8.3 for 8 years 3 months.",
            }

    return MeasurementForm


# ── Amending a booking ────────────────────────────────────────────────────────

class RescheduleForm(forms.Form):
    """
    Move an existing booking to another slot.

    The visit keeps its identity rather than being cancelled and re-created:
    the patient rang about one appointment, and the history should say it moved
    rather than that one vanished and another appeared.
    """

    day = forms.DateField(
        label="New date",
        widget=forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
    )
    slot = forms.DateTimeField(
        widget=forms.HiddenInput,
        error_messages={"required": "Choose a new time."},
    )
    note = forms.CharField(
        max_length=200, required=False, label="Reason for the change",
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "e.g. Doctor on leave"}),
    )

    def __init__(self, *args, visit=None, **kwargs):
        self.visit = visit
        super().__init__(*args, **kwargs)
        if visit is not None and not self.is_bound:
            self.fields["day"].initial = timezone.localtime(visit.scheduled_start).date()

    def clean(self):
        cleaned = super().clean()
        day, slot = cleaned.get("day"), cleaned.get("slot")
        if not (day and slot and self.visit):
            return cleaned

        if timezone.localtime(slot).date() != day:
            self.add_error("slot", "That time is not on the selected date.")
            return cleaned
        if day < timezone.localdate():
            self.add_error("day", "Appointments cannot be moved into the past.")
            return cleaned
        if slot <= timezone.now():
            self.add_error("slot", "That time has already passed.")
            return cleaned

        doctor = self.visit.doctor
        if not scheduling.is_working_day(day, doctor):
            self.add_error("day", f"{doctor.display_name} is not consulting on that day.")
            return cleaned

        # The slot this visit already holds is legitimately "taken" by itself,
        # so availability is checked with this visit set aside.
        free = {start for start, _ in scheduling.available_slots(doctor, day)}
        if slot not in free and slot != self.visit.scheduled_start:
            self.add_error("slot", "That slot is not free. Choose another time.")

        return cleaned

    def save(self, by_user=None):
        from appointments.models import VisitStatusEvent

        visit = self.visit
        was = timezone.localtime(visit.scheduled_start)
        slot = self.cleaned_data["slot"]

        visit.scheduled_start = slot
        visit.scheduled_end = slot + scheduling.slot_length()
        visit.save(update_fields=["scheduled_start", "scheduled_end", "updated_at"])

        # Rescheduling is not a status change, but it is exactly the kind of
        # thing somebody later asks "who moved this, and when" about.
        VisitStatusEvent.objects.create(
            visit=visit,
            from_status=visit.status,
            to_status=visit.status,
            changed_by=by_user,
            note=(f"Rescheduled from {was:%d %b %H:%M} to "
                  f"{timezone.localtime(slot):%d %b %H:%M}"
                  + (f" — {self.cleaned_data['note']}" if self.cleaned_data.get("note") else "")),
        )
        return visit


# ── Doctor availability ───────────────────────────────────────────────────────

def _all_doctors():
    return User.objects.filter(role=Role.DOCTOR, is_active=True)


def _schedulable_doctors():
    """
    Doctors who may be given hours.

    A doctor who has not set their password cannot sign in, so hours for them
    would put a patient in front of an empty cabin (KAN-21 FR-7).

    Both halves of the exclusion matter. A doctor with *no* profile row at all
    predates invitations and is perfectly schedulable; excluding on
    ``activated_at__isnull`` alone would sweep them up too, because a missing
    row reads as a null column.
    """
    return _all_doctors().exclude(
        doctor_profile__isnull=False, doctor_profile__activated_at__isnull=True
    )


class _SchedulableIterator(forms.models.ModelChoiceIterator):
    """Offers only the doctors who can actually be given hours."""

    def __iter__(self):
        if self.field.empty_label is not None:
            yield ("", self.field.empty_label)
        for obj in self.queryset.exclude(
            doctor_profile__isnull=False, doctor_profile__activated_at__isnull=True
        ):
            yield self.choice(obj)


class DoctorChoiceField(forms.ModelChoiceField):
    """
    A doctor picker that leaves out the ones who cannot sign in — but can still
    *resolve* them if one is submitted anyway.

    The distinction earns its keep in the error message. Narrowing the queryset
    would also work, and the guard would still hold, but the receptionist would
    be told "Select a valid choice. That choice is not one of the available
    choices" — which is true, useless, and gives her nothing to act on. Keeping
    the doctor resolvable lets :meth:`clean` say that they have not set their
    password yet and where to re-send the invitation.
    """

    iterator = _SchedulableIterator

    def label_from_instance(self, obj):
        # User.__str__ appends the role, which in a field already labelled
        # "Doctor" reads as "Asha Rao (Doctor)" on every single option.
        return obj.display_name

    def clean(self, value):
        doctor = super().clean(value)
        if doctor is None:
            return doctor
        profile = getattr(doctor, "doctor_profile", None)
        if profile is not None and profile.is_pending:
            raise forms.ValidationError(
                f"{doctor.display_name} has not set their password yet, so they "
                f"cannot be given hours. Re-send their invitation from the "
                f"Doctors screen."
            )
        return doctor


class CabinForm(forms.ModelForm):
    """A consulting room (KAN-22 FR-1, FR-2)."""

    class Meta:
        from appointments.models import Cabin as _Cabin

        model = _Cabin
        fields = ["name", "note"]
        widgets = {
            "name": forms.TextInput(attrs={**INPUT, "placeholder": "e.g. Cabin 1"}),
            "note": forms.TextInput(attrs={**INPUT, "placeholder": "Optional"}),
        }

    def clean_name(self):
        """
        Refuse a duplicate here rather than letting the constraint do it.

        The database already stops two cabins sharing a name, but it does so
        with a 500 — and reception typing a name that already exists deserves
        the sentence, not the error page (AC-4).
        """
        from appointments.models import Cabin

        name = " ".join((self.cleaned_data.get("name") or "").split())
        clash = Cabin.objects.filter(name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        existing = clash.first()
        if existing is not None:
            raise forms.ValidationError(
                f"There is already a cabin called {existing.name}."
                + ("" if existing.is_active else
                   " It has been retired — bring it back rather than adding a second.")
            )
        return name


class ClinicHolidayForm(forms.ModelForm):
    """A day the clinic is shut to everyone."""

    class Meta:
        from appointments.models import ClinicHoliday as _ClinicHoliday

        model = _ClinicHoliday
        fields = ["date", "name", "note"]
        widgets = {
            "date": forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
            "name": forms.TextInput(attrs={**INPUT, "placeholder": "e.g. Diwali"}),
            "note": forms.TextInput(attrs=INPUT),
        }


def _end_of_month(day):
    """The last calendar date of the month ``day`` falls in."""
    import calendar as _stdlib_calendar

    last_day = _stdlib_calendar.monthrange(day.year, day.month)[1]
    return day.replace(day=last_day)


def _conflict_occurrence(conflict):
    """One line for the Conflict Detected dialog: the date and the existing
    hours it clashes with — named plainly, not as a jargon reason code."""
    entry = conflict.entry
    return f"{conflict.day:%d-%b-%Y}, {entry.start:%I:%M %p} to {entry.end:%I:%M %p}"


class CalendarEventForm(forms.Form):
    """
    The calendar's add-event pop-up (KAN-22 FR-8, FR-9).

    One form whose shape follows the event type, rather than a separate pop-up
    per type. Reception opens it by clicking a day; what they are adding is a
    choice they make inside it, and making that choice before the pop-up opens
    would mean knowing the answer to get to the question.

    Working hours all land in :class:`DoctorSchedule` — one row per date,
    whether the booking is a single day or a recurring run of chosen weekdays
    between two dates. There is no "recurs forever" shape any more: a
    recurring booking always names an end date, so it is simply several rows
    generated up front, sharing one ``series_id``.

    One event is one continuous stretch of time. A doctor running a morning
    and an evening clinic on the same day submits this form twice — that is
    what lets "which cabin is this doctor in right now" stay a single lookup
    rather than a doctor's whole day being one row with two ranges inside it.

    The cabin is never chosen here — it is allocated once the dates and time
    are known, by :func:`appointments.calendar.allocate_cabins`. "Which room"
    is a question about what else is booked, and asking reception to work
    that out by eye against the day view before typing an answer is exactly
    the step this removes.

    The same form edits one existing row too — pass it as ``instance`` — so
    correcting a typo in a time or swapping a cabin goes through the same
    conflict check and the same cabin allocation a fresh booking does, rather
    than a second code path that could disagree with the first about what
    counts as a clash.
    """

    HOURS = "hours"
    HOLIDAY = "holiday"
    EVENT_TYPES = [
        (HOURS, "Doctor working hours"),
        (HOLIDAY, "Clinic holiday"),
    ]

    event_type = forms.ChoiceField(
        label="Event type", choices=EVENT_TYPES,
        widget=forms.Select(attrs={**INPUT, "id": "id_event_type"}),
    )

    date = forms.DateField(
        label="Date",
        widget=forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
    )
    #: The clinic holiday's own end date — a plain range, never gated behind
    #: anything, exactly as it always was. Kept separate from the recurring
    #: booking's end date below: two fields rendered as one shared "Last date"
    #: input would need the same HTML id twice, which no label or script can
    #: point at unambiguously.
    until = forms.DateField(
        label="Last date", required=False,
        help_text="Leave empty for a single day.",
        widget=forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
    )

    # Working hours
    doctor = DoctorChoiceField(
        label="Doctor", queryset=User.objects.none(), required=False,
        widget=forms.Select(attrs=INPUT),
    )
    start_time = forms.TimeField(
        label="From", required=False,
        widget=forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
    )
    end_time = forms.TimeField(
        label="To", required=False,
        widget=forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
    )
    #: A checkbox rather than the old three-way "repeat" choice: off is one
    #: day, on asks for an end date and which weekdays.
    is_recurring = forms.BooleanField(
        label="Recurring", required=False,
        widget=forms.CheckboxInput(attrs={"id": "id_is_recurring"}),
    )
    recur_until = forms.DateField(
        label="Last date", required=False,
        widget=forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
    )
    #: "M-W-F", chosen as circles rather than typed or a plain checkbox list —
    #: the calendar-app picker reception already knows from a phone.
    weekdays = forms.MultipleChoiceField(
        label="On these days", choices=weekday_codes.CHOICES, required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "daypick-input"}),
    )

    # Holiday
    name = forms.CharField(
        label="Holiday name", max_length=120, required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "e.g. Diwali"}),
    )

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["doctor"].queryset = _all_doctors()
        # Django's "---------" empty option is noise on a field that is
        # effectively required the moment "Doctor working hours" is chosen.
        self.fields["doctor"].empty_label = "Select a doctor"
        #: The single row being edited, or None while adding. Editing never
        #: recurs — it changes the one occurrence that was clicked, not the
        #: pattern that generated it — so a recurring booking is corrected by
        #: editing each date that needs it, the same as any other date.
        self.instance = instance

    # ── Validation ───────────────────────────────────────────────────────────

    def clean(self):
        cleaned = super().clean()
        event_type = cleaned.get("event_type")

        if event_type == self.HOURS:
            self._clean_hours(cleaned)
        elif event_type == self.HOLIDAY:
            self._clean_holiday(cleaned)
        return cleaned

    def _dates(self, cleaned, *, start_field="date", until_field="until",
               recurring=False):
        """Every date the event covers, or ``None`` when the range is unusable."""
        start = cleaned.get(start_field)
        if start is None:
            return None
        if not recurring:
            return [start]

        until = cleaned.get(until_field) or _end_of_month(start)
        if until < start:
            self.add_error(until_field, "The last day cannot be before the first.")
            return None
        span = (until - start).days + 1
        if span > weekday_codes.MAX_RANGE_DAYS:
            self.add_error(
                until_field,
                f"That is {span} days. Check the year — the most that can be "
                f"added at once is {weekday_codes.MAX_RANGE_DAYS}.",
            )
            return None
        return [start + timedelta(days=offset) for offset in range(span)]

    def _clean_hours(self, cleaned):
        for field in ("doctor", "start_time", "end_time"):
            if not cleaned.get(field):
                self.add_error(field, "Required for working hours.")

        start, end = cleaned.get("start_time"), cleaned.get("end_time")
        if start and end and end <= start:
            self.add_error("end_time", "The end time must be after the start time.")
        if start and end and start == end:
            self.add_error("end_time", "An entry with no length is not working hours.")

        # Editing a row never recurs — see __init__ — so whatever the
        # Recurring checkbox and its fields happen to carry is ignored rather
        # than validated, the same way the client already hides them.
        recurring = bool(cleaned.get("is_recurring")) and self.instance is None
        chosen_days = [] if self.instance is not None else (cleaned.get("weekdays") or [])
        if recurring and not chosen_days:
            self.add_error("weekdays", "Choose at least one day of the week.")
        elif not recurring and chosen_days:
            # Ticked, then Recurring was switched off. Acting on them anyway
            # would create a rota nobody asked for.
            self.add_error(
                "weekdays",
                "Days of the week only apply to a recurring booking. Tick "
                "Recurring, or clear these.",
            )

        dates = self._dates(cleaned, until_field="recur_until", recurring=recurring)
        if self.errors or dates is None:
            return

        if recurring:
            wanted = {weekday_codes.CODE_TO_WEEKDAY[c] for c in chosen_days}
            dates = [d for d in dates if d.weekday() in wanted]
            if not dates:
                self.add_error(
                    "weekdays",
                    "No "
                    + ", ".join(weekday_codes.CODE_TO_NAME[c] for c in chosen_days)
                    + " falls between those two dates.",
                )
                return
            if len(dates) > weekday_codes.MAX_GENERATED:
                self.add_error(
                    "recur_until",
                    f"That would create {len(dates)} entries. Check the dates — "
                    f"the most that can be added at once is "
                    f"{weekday_codes.MAX_GENERATED}.",
                )
                return

        if not cleaned.get("doctor"):
            return

        from appointments import calendar as clinic_calendar

        # Editing excludes the row itself from its own conflict check —
        # otherwise saving a booking unchanged would clash with the one row
        # it is.
        exclude_pk = self.instance.pk if self.instance is not None else None

        doctor_clashes = clinic_calendar.find_conflicts(
            doctor=cleaned["doctor"], cabin=None, start=start, end=end, dates=dates,
            exclude_pk=exclude_pk,
        )
        clashed = {conflict.day for conflict in doctor_clashes}
        occurrences = [_conflict_occurrence(conflict) for conflict in doctor_clashes]

        # Cabin availability is only worth checking for the dates that do not
        # already clash on the doctor themselves — one clash there means the
        # date is out regardless of what a room would say.
        remaining = [d for d in dates if d not in clashed]
        if remaining:
            by_date, unavailable = clinic_calendar.allocate_cabins(
                doctor=cleaned["doctor"], dates=remaining, start=start, end=end,
                exclude_pk=exclude_pk,
            )
        else:
            by_date, unavailable = {}, []
        occurrences += [
            f"{day:%d-%b-%Y}, {start:%I:%M %p} to {end:%I:%M %p} — no cabin available"
            for day in unavailable
        ]

        conflicting = clashed | set(unavailable)
        if conflicting and not self.data.get("skip_conflicts"):
            # Nothing is written yet. add_calendar_event reopens this same
            # form with these occurrences named, so reception can choose to
            # skip just the conflicting dates or go back and change the
            # booking — rather than the whole recurring run being refused
            # with no way to see which date was the problem.
            self._conflicts = occurrences
            return

        final_dates = [d for d in dates if d not in conflicting]
        if not final_dates:
            self.add_error(
                None,
                "Every date in this booking conflicts with hours already on "
                "record. Change the doctor, time or dates and try again.",
            )
            return

        self._planned_dates = final_dates
        self._cabin_by_date = by_date

    def _clean_holiday(self, cleaned):
        from appointments.models import ClinicHoliday

        if not cleaned.get("name"):
            self.add_error("name", "Give the holiday a name.")

        dates = self._dates(cleaned, recurring=True)
        if dates is None:
            return

        taken = set(
            ClinicHoliday.objects.filter(date__in=dates).values_list("date", flat=True)
        )
        if taken and len(taken) == len(dates):
            self.add_error(
                "date",
                "Already recorded as a holiday: "
                + ", ".join(f"{d:%d %b %Y}" for d in sorted(taken)) + ".",
            )
        self._existing_holidays = taken
        self._planned_dates = dates

    # ── Saving ───────────────────────────────────────────────────────────────

    def save(self, created_by=None):
        """Create the rows and return them."""
        from appointments.models import ClinicHoliday, DoctorSchedule

        cleaned = self.cleaned_data
        created = []

        if cleaned["event_type"] == self.HOLIDAY:
            skipped = getattr(self, "_existing_holidays", set())
            for day in self._planned_dates:
                if day in skipped:
                    continue
                created.append(ClinicHoliday.objects.create(
                    date=day, name=cleaned["name"],
                ))
            return created

        if self.instance is not None:
            # One row, in place — its series_id (if any) is untouched, so it
            # stays grouped with the booking it came from for "remove whole
            # booking" purposes.
            day = self._planned_dates[0]
            self.instance.doctor = cleaned["doctor"]
            self.instance.date = day
            self.instance.cabin = self._cabin_by_date[day]
            self.instance.start_time = cleaned["start_time"]
            self.instance.end_time = cleaned["end_time"]
            self.instance.save()
            return [self.instance]

        series_id = uuid.uuid4() if cleaned.get("is_recurring") else None
        for day in self._planned_dates:
            created.append(DoctorSchedule.objects.create(
                doctor=cleaned["doctor"],
                date=day,
                cabin=self._cabin_by_date[day],
                start_time=cleaned["start_time"],
                end_time=cleaned["end_time"],
                series_id=series_id,
                created_by=created_by,
            ))
        return created


class DoctorForm(forms.Form):
    """
    Adding a doctor (KAN-21).

    A plain form rather than a ModelForm because it writes two rows — the user
    account and the doctor profile — and neither on its own is a doctor.

    There is no password field, and there is not meant to be one. Reception
    never chooses a doctor's password; the doctor sets their own by following
    the emailed link.
    """

    full_name = forms.CharField(
        label="Doctor's name",
        max_length=150,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "e.g. Vrushali Kulkarni"}),
    )
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs=INPUT),
        help_text="Where the set-password link is sent. Also how they sign in.",
    )
    phone = forms.CharField(
        label="Contact number", max_length=15, required=False,
        widget=forms.TextInput(attrs=INPUT),
    )
    #: The sentinel the dropdown carries for "not on this list".
    ADD_NEW = "__new__"

    specialisation = forms.ChoiceField(
        label="Specialisation",
        widget=forms.Select(attrs={**INPUT, "id": "id_specialisation"}),
    )
    new_specialisation = forms.CharField(
        label="New specialisation",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            **INPUT, "id": "id_new_specialisation",
            "placeholder": "e.g. Reproductive Endocrinology",
        }),
        help_text="Added to the list for everyone, so check it is not already "
                  "there under another spelling.",
    )
    registration_number = forms.CharField(
        label="Medical council registration number", max_length=50, required=False,
        widget=forms.TextInput(attrs=INPUT),
        help_text="Printed on prescriptions.",
    )
    qualification = forms.CharField(
        label="Qualification", max_length=200, required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "e.g. MBBS, MD, DM"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Read at request time, not at import: a specialisation added a minute
        # ago by the previous receptionist has to be in this list.
        self.fields["specialisation"].choices = (
            [("", "Select a specialisation")]
            + [(str(s.pk), s.name)
               for s in Specialisation.objects.filter(is_active=True)]
            + [(self.ADD_NEW, "+ Add a new specialisation...")]
        )

    def clean_new_specialisation(self):
        """
        Collapse the whitespace before anything compares it.

        Doing this only in ``Specialisation.save()`` was too late: "General
        Medicine" typed with two spaces did not match the stored name, so the
        duplicate check passed, a second row was attempted, and the unique
        constraint turned a typo into a 500.
        """
        return " ".join((self.cleaned_data.get("new_specialisation") or "").split())

    def clean(self):
        cleaned = super().clean()
        chosen = cleaned.get("specialisation")
        typed = (cleaned.get("new_specialisation") or "").strip()

        if chosen != self.ADD_NEW:
            cleaned["new_specialisation"] = ""
            return cleaned

        if not typed:
            self.add_error(
                "new_specialisation",
                "Type the specialisation you want to add, or choose one from "
                "the list.",
            )
            return cleaned

        # Already there under a different capitalisation or spacing — reuse it
        # rather than making a second row that means the same thing.
        existing = Specialisation.objects.filter(name__iexact=typed).first()
        if existing:
            if not existing.is_active:
                self.add_error(
                    "new_specialisation",
                    f"'{existing.name}' already exists but has been retired. "
                    f"Bring it back from the admin rather than adding it twice.",
                )
            else:
                cleaned["specialisation"] = str(existing.pk)
                cleaned["new_specialisation"] = ""

        return cleaned

    def _resolve_specialisation(self, by=None):
        """The chosen row, creating it first when the user typed a new one."""
        chosen = self.cleaned_data["specialisation"]
        if chosen != self.ADD_NEW:
            return Specialisation.objects.get(pk=chosen)

        # get_or_create rather than create: two receptionists adding the same
        # one at the same moment must not trip the unique constraint.
        name = self.cleaned_data["new_specialisation"].strip()
        existing = Specialisation.objects.filter(name__iexact=name).first()
        if existing:
            return existing
        return Specialisation.objects.create(name=name, created_by=by)

    def clean_email(self):
        # FR-8 / AC-7. Checked here for a readable message; the column is
        # unique, so two receptionists saving the same address at the same
        # instant still produce exactly one account.
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Somebody is already registered with that email address."
            )
        return email

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if phone:
            phone_validator(phone)
        return phone

    def _username_for(self, email):
        """
        A login name derived from the email, made unique.

        The doctor signs in with their email; this exists because the user
        table still has a username column and it has to hold something.
        """
        base = slugify(email.split("@")[0]) or "doctor"
        candidate, suffix = base, 1
        while User.objects.filter(username=candidate).exists():
            suffix += 1
            candidate = f"{base}{suffix}"
        return candidate

    @transaction.atomic
    def save(self, added_by=None):
        """Create the account and its profile, both pending activation."""
        name = self.cleaned_data["full_name"].strip()
        first, _, last = name.partition(" ")

        user = User.objects.create_user(
            username=self._username_for(self.cleaned_data["email"]),
            email=self.cleaned_data["email"],
            first_name=first,
            last_name=last.strip(),
            phone=self.cleaned_data.get("phone", ""),
            role=Role.DOCTOR,
            # Cannot sign in until the invitation is followed, and there is no
            # password to sign in with either.
            is_active=False,
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])

        DoctorProfile.objects.create(
            user=user,
            specialisation=self._resolve_specialisation(by=added_by),
            registration_number=self.cleaned_data.get("registration_number", ""),
            qualification=self.cleaned_data.get("qualification", ""),
        )
        return user


class DoctorEditForm(forms.Form):
    """
    Correcting a doctor's own details after they have already been added.

    Not the add-doctor form reused: that one also creates the login account
    and sends an invitation, neither of which applies here.

    Email is editable — a doctor asking to correct or change their address is
    routine. It is *not* what they sign in with, though: sign-in uses the
    ``username`` Django gave the account when it was added (derived once from
    the email at that moment, e.g. "vrushali" from vrushali@example.in), and
    it does not change when the email later does. It is shown read-only on the
    edit screen for exactly that reason — changing the address here would
    otherwise look like it should change how they log in, and it does not.

    The specialisation "choose or add one" behaviour mirrors ``DoctorForm``'s
    rather than sharing it — the two forms save completely different things,
    and the small duplication reads more clearly than a shared base built for
    two callers.
    """

    full_name = forms.CharField(
        label="Doctor's name",
        max_length=150,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "e.g. Vrushali Kulkarni"}),
    )
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs=INPUT),
        help_text="Where the set-password link and any notifications go. "
                  "Not what they sign in with — see the username above.",
    )
    phone = forms.CharField(
        label="Contact number", max_length=15, required=False,
        widget=forms.TextInput(attrs=INPUT),
    )

    ADD_NEW = "__new__"

    specialisation = forms.ChoiceField(
        label="Specialisation",
        widget=forms.Select(attrs={**INPUT, "id": "id_specialisation"}),
    )
    new_specialisation = forms.CharField(
        label="New specialisation",
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            **INPUT, "id": "id_new_specialisation",
            "placeholder": "e.g. Reproductive Endocrinology",
        }),
        help_text="Added to the list for everyone, so check it is not already "
                  "there under another spelling.",
    )
    registration_number = forms.CharField(
        label="Medical council registration number", max_length=50, required=False,
        widget=forms.TextInput(attrs=INPUT),
        help_text="Printed on prescriptions.",
    )
    qualification = forms.CharField(
        label="Qualification", max_length=200, required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "e.g. MBBS, MD, DM"}),
    )

    def __init__(self, *args, doctor=None, **kwargs):
        self.doctor = doctor
        super().__init__(*args, **kwargs)

        current = getattr(doctor, "doctor_profile", None)
        choices = [("", "Select a specialisation")]
        # The doctor's current specialisation stays selectable even if it has
        # since been retired — an edit that silently dropped it the moment
        # somebody else retired the option would look like data loss.
        seen_active = set()
        for s in Specialisation.objects.filter(is_active=True):
            choices.append((str(s.pk), s.name))
            seen_active.add(s.pk)
        if current and current.specialisation_id and current.specialisation_id not in seen_active:
            choices.append((str(current.specialisation_id), f"{current.specialisation.name} (retired)"))
        choices.append((self.ADD_NEW, "+ Add a new specialisation..."))
        self.fields["specialisation"].choices = choices

        if not self.is_bound and doctor is not None:
            name = doctor.get_full_name() or doctor.first_name
            self.initial.setdefault("full_name", name)
            self.initial.setdefault("email", doctor.email)
            self.initial.setdefault("phone", doctor.phone)
            if current:
                self.initial.setdefault(
                    "specialisation",
                    str(current.specialisation_id) if current.specialisation_id else "",
                )
                self.initial.setdefault("registration_number", current.registration_number)
                self.initial.setdefault("qualification", current.qualification)

    def clean_new_specialisation(self):
        return " ".join((self.cleaned_data.get("new_specialisation") or "").split())

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if phone:
            phone_validator(phone)
        return phone

    def clean_email(self):
        # Excludes this doctor's own row — otherwise saving the form without
        # touching the address would trip over itself.
        email = self.cleaned_data["email"].strip().lower()
        clash = User.objects.filter(email__iexact=email)
        if self.doctor is not None:
            clash = clash.exclude(pk=self.doctor.pk)
        if clash.exists():
            raise forms.ValidationError(
                "Somebody is already registered with that email address."
            )
        return email

    def clean(self):
        cleaned = super().clean()
        chosen = cleaned.get("specialisation")
        typed = (cleaned.get("new_specialisation") or "").strip()

        if chosen != self.ADD_NEW:
            cleaned["new_specialisation"] = ""
            return cleaned

        if not typed:
            self.add_error(
                "new_specialisation",
                "Type the specialisation you want to add, or choose one from "
                "the list.",
            )
            return cleaned

        existing = Specialisation.objects.filter(name__iexact=typed).first()
        if existing:
            if not existing.is_active:
                self.add_error(
                    "new_specialisation",
                    f"'{existing.name}' already exists but has been retired. "
                    f"Bring it back from the admin rather than adding it twice.",
                )
            else:
                cleaned["specialisation"] = str(existing.pk)
                cleaned["new_specialisation"] = ""

        return cleaned

    def _resolve_specialisation(self, by=None):
        chosen = self.cleaned_data["specialisation"]
        if chosen != self.ADD_NEW:
            return Specialisation.objects.get(pk=chosen)

        name = self.cleaned_data["new_specialisation"].strip()
        existing = Specialisation.objects.filter(name__iexact=name).first()
        if existing:
            return existing
        return Specialisation.objects.create(name=name, created_by=by)

    @transaction.atomic
    def save(self):
        """Update the account and its profile in place."""
        name = self.cleaned_data["full_name"].strip()
        first, _, last = name.partition(" ")

        self.doctor.first_name = first
        self.doctor.last_name = last.strip()
        self.doctor.email = self.cleaned_data["email"]
        self.doctor.phone = self.cleaned_data.get("phone", "")
        self.doctor.save(update_fields=["first_name", "last_name", "email", "phone"])

        profile, _ = DoctorProfile.objects.get_or_create(user=self.doctor)
        profile.specialisation = self._resolve_specialisation()
        profile.registration_number = self.cleaned_data.get("registration_number", "")
        profile.qualification = self.cleaned_data.get("qualification", "")
        profile.save(update_fields=["specialisation", "registration_number", "qualification"])

        return self.doctor
