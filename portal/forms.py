"""
Edit forms for the doctor's chart.

Every form here follows the same shape so the generic edit view in
``views_edit.py`` can drive all of them, and so a new editable record type is a
form class plus a registry entry rather than a new view.
"""

from django import forms
from django.forms import inlineformset_factory

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
