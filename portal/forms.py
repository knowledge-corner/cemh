"""
Edit forms for the doctor's chart.

Every form here follows the same shape so the generic edit view in
``views_edit.py`` can drive all of them, and so a new editable record type is a
form class plus a registry entry rather than a new view.
"""

from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from appointments import scheduling
from appointments.models import Visit, VisitStatus
from billing.models import Charge, Payment
from clinical.models import ClinicalNote, Diagnosis, Investigation
from patients.models import Patient, PatientHistory
from pharmacy.models import Prescription, PrescriptionItem

INPUT = {"class": "input"}
TEXTAREA = {"class": "input", "rows": 3}
DATE = {"class": "input", "type": "date"}


class StyledModelForm(forms.ModelForm):
    """Applies the shared field styling without repeating widget attrs per form."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                continue
            existing = widget.attrs.get("class", "")
            if "input" not in existing:
                widget.attrs["class"] = (existing + " input").strip()


class PatientForm(StyledModelForm):
    class Meta:
        model = Patient
        fields = [
            "first_name", "last_name", "date_of_birth", "sex", "blood_group",
            "phone", "alternate_phone", "email",
            "guardian_name", "guardian_relation", "guardian_phone",
            "address", "city", "pincode", "referred_by",
        ]
        widgets = {"date_of_birth": forms.DateInput(attrs=DATE, format="%Y-%m-%d")}


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
            "diagnosed_on": forms.DateInput(attrs=DATE, format="%Y-%m-%d"),
            "resolved_on": forms.DateInput(attrs=DATE, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs=TEXTAREA),
        }


class InvestigationForm(StyledModelForm):
    class Meta:
        model = Investigation
        fields = [
            "test_name", "category", "performed_on", "value", "value_numeric",
            "unit", "reference_range", "is_abnormal", "lab_name", "notes",
        ]
        widgets = {
            "performed_on": forms.DateInput(attrs=DATE, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={**TEXTAREA, "rows": 2}),
        }
        help_texts = {
            "value_numeric": "Fill in when the result is a number — this is what gets trended.",
        }


class ClinicalNoteForm(StyledModelForm):
    class Meta:
        model = ClinicalNote
        fields = [
            "complaints", "examination", "assessment", "plan",
            "systolic_bp", "diastolic_bp", "pulse", "temperature_c",
        ]
        widgets = {
            "complaints": forms.Textarea(attrs=TEXTAREA),
            "examination": forms.Textarea(attrs=TEXTAREA),
            "assessment": forms.Textarea(attrs=TEXTAREA),
            "plan": forms.Textarea(attrs=TEXTAREA),
        }


class PrescriptionForm(StyledModelForm):
    class Meta:
        model = Prescription
        fields = ["advice", "investigations_advised", "follow_up_date", "follow_up_notes"]
        widgets = {
            "advice": forms.Textarea(attrs=TEXTAREA),
            "investigations_advised": forms.Textarea(attrs=TEXTAREA),
            "follow_up_date": forms.DateInput(attrs=DATE, format="%Y-%m-%d"),
        }


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
        }


class PaymentForm(StyledModelForm):
    class Meta:
        model = Payment
        fields = ["amount", "method", "reference", "notes"]
        widgets = {"notes": forms.TextInput(attrs=INPUT)}
        help_texts = {"reference": "UPI reference or card approval code, if any."}


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
        widget=forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
    )
    slot = forms.DateTimeField(
        widget=forms.HiddenInput,
        error_messages={"required": "Choose a time."},
    )
    reason = forms.CharField(
        max_length=300, required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "e.g. Thyroid review"}),
    )
    is_follow_up = forms.BooleanField(required=False, label="Follow-up visit")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import Role, User

        self.fields["doctor"].queryset = User.objects.filter(
            role=Role.DOCTOR, is_active=True
        )

    def clean(self):
        cleaned = super().clean()
        doctor = cleaned.get("doctor")
        slot = cleaned.get("slot")
        day = cleaned.get("day")

        if not (doctor and slot and day):
            return cleaned

        local_slot = timezone.localtime(slot)
        if local_slot.date() != day:
            self.add_error("slot", "That time is not on the selected date.")
            return cleaned

        if not scheduling.is_working_day(day):
            self.add_error("day", "The clinic is closed on that day.")
            return cleaned

        # Re-check availability at submission time. The database constraint is
        # the real guarantee; this exists to give a readable error instead of
        # an IntegrityError when somebody simply took the slot first.
        free = {start for start, _ in scheduling.available_slots(doctor, day, include_past=True)}
        if slot not in free:
            self.add_error("slot", "That slot is no longer free. Please choose another time.")

        return cleaned

    def save(self, booked_by=None):
        slot = self.cleaned_data["slot"]
        return Visit.objects.create(
            patient=self.cleaned_data["patient"],
            # `is_follow_up` is absent on the patient-facing subclass.
            doctor=self.cleaned_data["doctor"],
            scheduled_start=slot,
            scheduled_end=slot + scheduling.slot_length(),
            reason=self.cleaned_data.get("reason", ""),
            is_follow_up=self.cleaned_data.get("is_follow_up", False),
            booked_by=booked_by,
            # Taken by a member of staff, so it is confirmed on the spot.
            status=VisitStatus.CONFIRMED if booked_by else VisitStatus.BOOKED,
        )


class PatientBookingForm(BookingForm):
    """
    The same booking, made by the patient themselves.

    The patient is fixed to whoever is signed in, and the request lands as
    BOOKED so reception still confirms it.
    """

    def __init__(self, *args, patient=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._patient = patient
        # The patient is whoever is signed in, so it is not theirs to choose.
        del self.fields["patient"]
        del self.fields["is_follow_up"]

    def clean(self):
        cleaned = super().clean()
        cleaned["patient"] = self._patient
        return cleaned

    def save(self, booked_by=None):
        # booked_by stays empty: the request came from the patient, not the desk,
        # which is what leaves it BOOKED for reception to confirm.
        return super().save(booked_by=None)


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
                "waist_cm", "puberty_stage", "mother_height_cm", "father_height_cm", "notes",
            ]
            widgets = {
                "measured_on": forms.DateInput(attrs=DATE, format="%Y-%m-%d"),
                "notes": forms.Textarea(attrs={**TEXTAREA, "rows": 2}),
            }
            help_texts = {
                "mother_height_cm": "Used to compute the mid-parental target height.",
            }

    return MeasurementForm
