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
        widget=forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
        # `min` and `max` are set per instance in __init__ — the booking window
        # moves with the calendar, and a bound computed at import time would be
        # yesterday's by the following morning.
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
        return Visit.objects.create(
            patient=self.cleaned_data["patient"],
            doctor=self.cleaned_data["doctor"],
            scheduled_start=slot,
            scheduled_end=slot + scheduling.slot_length(),
            reason=self.cleaned_data.get("reason", ""),
            is_follow_up=self.cleaned_data.get("is_follow_up", False),
            booked_by=booked_by,
            # Every booking starts unconfirmed. The receptionist telephones the
            # patient on the appointment day, and confirming is that call — so
            # the board can show her who she still has to ring.
            status=VisitStatus.BOOKED,
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

class DoctorScheduleForm(forms.ModelForm):
    """One sitting in a doctor's ordinary week."""

    class Meta:
        from appointments.models import DoctorSchedule as _DoctorSchedule

        model = _DoctorSchedule
        fields = ["doctor", "weekday", "start_time", "end_time", "slot_minutes"]
        widgets = {
            "doctor": forms.Select(attrs=INPUT),
            "weekday": forms.Select(attrs=INPUT),
            "start_time": forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
            "end_time": forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
            "slot_minutes": forms.NumberInput(attrs={**INPUT, "placeholder": "Clinic default"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import Role, User

        self.fields["doctor"].queryset = User.objects.filter(
            role=Role.DOCTOR, is_active=True
        )


class ScheduleOverrideForm(forms.ModelForm):
    """Different hours for one doctor on one date."""

    class Meta:
        from appointments.models import ScheduleOverride as _ScheduleOverride

        model = _ScheduleOverride
        fields = ["doctor", "date", "start_time", "end_time", "slot_minutes", "note"]
        widgets = {
            "doctor": forms.Select(attrs=INPUT),
            "date": forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
            "start_time": forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
            "end_time": forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
            "slot_minutes": forms.NumberInput(attrs={**INPUT, "placeholder": "Clinic default"}),
            "note": forms.TextInput(attrs={**INPUT, "placeholder": "e.g. Extra evening clinic"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import Role, User

        self.fields["doctor"].queryset = User.objects.filter(
            role=Role.DOCTOR, is_active=True
        )

    def clean_date(self):
        day = self.cleaned_data["date"]
        if day < timezone.localdate():
            raise forms.ValidationError("That date has passed.")
        return day


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


class DoctorLeaveForm(forms.ModelForm):
    """
    A doctor away for a day, or part of one.

    Leave taken after patients are already booked is the case that matters, so
    nothing here refuses a clash — the view surfaces who has to be rung instead.
    Refusing would only push the receptionist into recording it somewhere else.
    """

    class Meta:
        from appointments.models import DoctorLeave as _DoctorLeave

        model = _DoctorLeave
        fields = ["doctor", "date", "start_time", "end_time", "reason"]
        widgets = {
            "doctor": forms.Select(attrs=INPUT),
            "date": forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
            "start_time": forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
            "end_time": forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
            "reason": forms.TextInput(attrs={**INPUT, "placeholder": "Optional"}),
        }
        help_texts = {
            "start_time": "Leave both times empty for a whole day.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import Role, User

        self.fields["doctor"].queryset = User.objects.filter(
            role=Role.DOCTOR, is_active=True
        )
        self.fields["start_time"].required = False
        self.fields["end_time"].required = False
