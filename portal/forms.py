"""
Edit forms for the doctor's chart.

Every form here follows the same shape so the generic edit view in
``views_edit.py`` can drive all of them, and so a new editable record type is a
form class plus a registry entry rather than a new view.
"""

from datetime import timedelta

from django import forms
from django.db import transaction
from django.utils.text import slugify
from django.forms import inlineformset_factory
from django.utils import timezone

from accounts.models import (
    DoctorProfile, Role, Specialisation, User, phone_validator,
)
from appointments import scheduling
from appointments import weekdays as weekday_codes
from appointments.models import Visit, VisitStatus
from billing.models import Charge, Payment
from clinical.models import ClinicalNote, Diagnosis, Investigation, ReferenceLetter
from patients.models import Patient, PatientHistory, age_in_years
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
        ]
        widgets = {
            "investigations_advised": forms.Textarea(attrs=TEXTAREA),
            "follow_up_unit": forms.Select(attrs=INPUT),
            "follow_up_notes": forms.TextInput(attrs=INPUT),
        }
        labels = {
            "follow_up_number": "Follow up after",
            "follow_up_unit": " ",
            "follow_up_notes": "Follow-up notes",
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
    #: told to come back after picking a time; today's earliest free slot is
    #: found for them, so those two fields are optional here instead and
    #: filled in by clean().
    walk_in = forms.BooleanField(
        required=False,
        label="Walk-in — see them today, as soon as a slot is free",
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
            free = scheduling.available_slots(doctor, today)
            if not free:
                self.add_error(
                    "doctor",
                    f"{doctor.display_name} has no free slot left today. Try "
                    f"another doctor, or book ahead instead of a walk-in.",
                )
                return cleaned
            cleaned["day"] = today
            cleaned["slot"] = free[0][0]
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
            # Every booking starts unconfirmed, and the receptionist telephones
            # the patient on the day to confirm — except a walk-in, who is
            # standing at the desk right now. Landing them on BOOKED would put
            # them behind a phone call that will never happen, so a walk-in
            # goes straight into today's waiting room instead.
            status=VisitStatus.ARRIVED if walk_in else VisitStatus.BOOKED,
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


class ConflictCheckedScheduleForm(forms.ModelForm):
    """
    Shared conflict checking for the two kinds of working-hours row.

    Both ask the same question of :mod:`appointments.calendar` — would this put
    two doctors in one cabin, or one doctor in two — and both must ask it before
    the row is committed rather than after (KAN-22 UI-14).
    """

    #: "schedule" for the weekly pattern, "override" for a single date.
    conflict_kind = None

    def _conflict_window(self):
        """``(date, weekday)`` — exactly one of which is set."""
        raise NotImplementedError

    def clean(self):
        cleaned = super().clean()

        doctor = cleaned.get("doctor")
        start = cleaned.get("start_time")
        end = cleaned.get("end_time")

        if start and end and end <= start:
            self.add_error("end_time", "The end time must be after the start time.")
            return cleaned
        if not (doctor and start and end):
            return cleaned

        from appointments import calendar as clinic_calendar

        date, weekday = self._conflict_window()
        if date is None and weekday is None:
            return cleaned

        conflicts = clinic_calendar.find_conflicts(
            kind=self.conflict_kind,
            doctor=doctor,
            cabin=cleaned.get("cabin"),
            start=start,
            end=end,
            date=date,
            weekday=weekday,
            exclude_pk=self.instance.pk,
        )
        for conflict in conflicts[:5]:
            self.add_error(None, str(conflict))
        if len(conflicts) > 5:
            self.add_error(
                None, f"…and {len(conflicts) - 5} more dates with the same clash."
            )
        return cleaned


class DoctorScheduleForm(ConflictCheckedScheduleForm):
    """One sitting in a doctor's ordinary week — the weekly repeat (FR-20)."""

    conflict_kind = "schedule"

    doctor = DoctorChoiceField(
        queryset=User.objects.none(), widget=forms.Select(attrs=INPUT),
    )

    class Meta:
        from appointments.models import DoctorSchedule as _DoctorSchedule

        model = _DoctorSchedule
        fields = ["doctor", "weekday", "cabin", "start_time", "end_time", "slot_minutes"]
        widgets = {
            "doctor": forms.Select(attrs=INPUT),
            "weekday": forms.Select(attrs=INPUT),
            "cabin": forms.Select(attrs=INPUT),
            "start_time": forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
            "end_time": forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
            "slot_minutes": forms.NumberInput(attrs={**INPUT, "placeholder": "Clinic default"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from appointments.models import Cabin

        self.fields["doctor"].queryset = _all_doctors()
        self.fields["cabin"].queryset = Cabin.objects.filter(is_active=True)
        self.fields["cabin"].empty_label = "No cabin set"

    def _conflict_window(self):
        weekday = self.cleaned_data.get("weekday")
        return None, (None if weekday in (None, "") else int(weekday))


class ScheduleOverrideForm(ConflictCheckedScheduleForm):
    """Different hours for one doctor on one date."""

    conflict_kind = "override"

    doctor = DoctorChoiceField(
        queryset=User.objects.none(), widget=forms.Select(attrs=INPUT),
    )

    class Meta:
        from appointments.models import ScheduleOverride as _ScheduleOverride

        model = _ScheduleOverride
        fields = ["doctor", "date", "cabin", "start_time", "end_time",
                  "slot_minutes", "note"]
        widgets = {
            "doctor": forms.Select(attrs=INPUT),
            "date": forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
            "cabin": forms.Select(attrs=INPUT),
            "start_time": forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
            "end_time": forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
            "slot_minutes": forms.NumberInput(attrs={**INPUT, "placeholder": "Clinic default"}),
            "note": forms.TextInput(attrs={**INPUT, "placeholder": "e.g. Extra evening clinic"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from appointments.models import Cabin

        self.fields["doctor"].queryset = _all_doctors()
        self.fields["cabin"].queryset = Cabin.objects.filter(is_active=True)
        self.fields["cabin"].empty_label = "No cabin set"

    def clean_date(self):
        day = self.cleaned_data["date"]
        if day < timezone.localdate():
            raise forms.ValidationError("That date has passed.")
        return day

    def _conflict_window(self):
        return self.cleaned_data.get("date"), None


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


#: A date range creating more rows than this is a mis-keyed year, not an
#: intention. 400 lets a full year plus a margin through and stops 2026 being
#: typed as 2260.
MAX_RANGE_DAYS = 400


class CalendarEventForm(forms.Form):
    """
    The calendar's add-event pop-up (KAN-22 FR-8, FR-9).

    One form whose shape follows the event type, rather than a separate pop-up
    per type. Reception opens it by clicking a day; what they are adding is a
    choice they make inside it, and making that choice before the pop-up opens
    would mean knowing the answer to get to the question.

    Working hours land in the same two tables the availability screen already
    writes — a weekly repeat is a :class:`DoctorSchedule` row, a one-off is a
    :class:`ScheduleOverride` — so there is one answer to "when does this doctor
    work" rather than a calendar's answer and a booking form's answer.

    KAN-50 removed that availability screen, and leave was the one thing on it
    the calendar could not do: the calendar *showed* who was away but the only
    way to record it was to delete one occurrence of a weekly pattern. A doctor
    on the clinic's default hours has no pattern to delete, so there was no way
    at all to say they were away. Leave is therefore a third event type here.
    """

    HOURS = "hours"
    LEAVE = "leave"
    HOLIDAY = "holiday"
    EVENT_TYPES = [
        (HOURS, "Doctor working hours"),
        (LEAVE, "Doctor away"),
        (HOLIDAY, "Clinic holiday"),
    ]

    ONCE = "once"
    SELECTED = "selected"
    WEEKLY = "weekly"
    REPEATS = [
        (ONCE, "Does not repeat"),
        (SELECTED, "On chosen weekdays, until the end date"),
        (WEEKLY, "Every week, until removed"),
    ]

    event_type = forms.ChoiceField(
        label="Event type", choices=EVENT_TYPES,
        widget=forms.Select(attrs={**INPUT, "id": "id_event_type"}),
    )

    date = forms.DateField(
        label="Date",
        widget=forms.DateInput(attrs={**INPUT, "type": "date"}, format="%Y-%m-%d"),
    )
    #: Not "Repeat daily until": this one field is the end of the range for all
    #: four things the dialog adds, and only one of them repeats daily. Under
    #: "Doctor away" it read as though leave were being repeated, and under
    #: "On chosen weekdays" it contradicted the picker directly beneath it. The
    #: repeat select beside it already says what happens between the two dates.
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
    cabin = forms.ModelChoiceField(
        label="Cabin", queryset=None, required=False,
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
    repeat = forms.ChoiceField(
        label="Repeat", choices=REPEATS, initial=ONCE, required=False,
        widget=forms.Select(attrs={**INPUT, "id": "id_repeat"}),
    )
    #: KAN-22: "M-W-F", chosen as checkboxes rather than typed. A doctor works
    #: Monday, Wednesday and Friday far more often than they work seven
    #: consecutive days, and before this the only way to say so was to add each
    #: date by hand and hope none were missed.
    weekdays = forms.MultipleChoiceField(
        label="On these days", choices=weekday_codes.CHOICES, required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "daypick"}),
    )

    # Away (KAN-50). Separate time fields from the working-hours pair rather
    # than shared ones: the hours section is disabled wholesale when another
    # event type is chosen, and a field that is required in one section and
    # optional in another is a field nobody can reason about.
    leave_start_time = forms.TimeField(
        label="Away from", required=False,
        widget=forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
    )
    leave_end_time = forms.TimeField(
        label="Away until", required=False,
        widget=forms.TimeInput(attrs={**INPUT, "type": "time"}, format="%H:%M"),
    )
    reason = forms.CharField(
        label="Reason", max_length=200, required=False,
        help_text="Optional, and shown to reception only.",
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "e.g. Conference"}),
    )

    # Holiday
    name = forms.CharField(
        label="Holiday name", max_length=120, required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": "e.g. Diwali"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from appointments.models import Cabin

        self.fields["doctor"].queryset = _all_doctors()
        self.fields["cabin"].queryset = Cabin.objects.filter(is_active=True)

        # Both fields are required for working hours, so neither empty option
        # may read as a choice. Django's "---------" is noise, and offering
        # "No cabin set" — which the schedule screen does, because a cabin is
        # optional there — would be offering an answer that is then refused.
        self.fields["doctor"].empty_label = "Select a doctor"
        self.fields["cabin"].empty_label = "Select a cabin"

    # ── Validation ───────────────────────────────────────────────────────────

    def clean(self):
        cleaned = super().clean()
        event_type = cleaned.get("event_type")

        if event_type == self.HOURS:
            self._clean_hours(cleaned)
        elif event_type == self.LEAVE:
            self._clean_leave(cleaned)
        elif event_type == self.HOLIDAY:
            self._clean_holiday(cleaned)
        return cleaned

    def _dates(self, cleaned):
        """Every date the event covers, or ``None`` when the range is unusable."""
        start = cleaned.get("date")
        if start is None:
            return None
        until = cleaned.get("until") or start
        if until < start:
            self.add_error("until", "The last day cannot be before the first.")
            return None
        span = (until - start).days + 1
        if span > MAX_RANGE_DAYS:
            self.add_error(
                "until",
                f"That is {span} days. Check the year — the most that can be "
                f"added at once is {MAX_RANGE_DAYS}.",
            )
            return None
        return [start + timedelta(days=offset) for offset in range(span)]

    def _clean_hours(self, cleaned):
        for field in ("doctor", "cabin", "start_time", "end_time"):
            if not cleaned.get(field):
                self.add_error(field, "Required for working hours.")

        start, end = cleaned.get("start_time"), cleaned.get("end_time")
        if start and end and end <= start:
            self.add_error("end_time", "The end time must be after the start time.")
        if start and end and start == end:
            self.add_error("end_time", "An entry with no length is not working hours.")

        repeat = cleaned.get("repeat")
        chosen_days = cleaned.get("weekdays") or []

        if repeat == self.SELECTED:
            # Both halves are the point of this mode: which days, and how far.
            if not chosen_days:
                self.add_error("weekdays", "Choose at least one day of the week.")
            if not cleaned.get("until"):
                self.add_error(
                    "until",
                    "Give the last date. Chosen weekdays repeat between the two "
                    "dates, so without an end date there is nothing to generate.",
                )
        elif chosen_days:
            # Ticked, then the mode changed. Acting on them anyway would create
            # a rota nobody asked for.
            self.add_error(
                "weekdays",
                "Days of the week only apply to 'On chosen weekdays'. Change "
                "the repeat, or clear these.",
            )

        weekly = repeat == self.WEEKLY
        if weekly and cleaned.get("until"):
            # A weekly pattern has no end date in the model, and pretending it
            # honours one would quietly create hours that never stop.
            self.add_error(
                "until",
                "A weekly pattern runs until it is removed. Leave the end date "
                "empty, or choose 'Does not repeat' to add a fixed range.",
            )

        dates = self._dates(cleaned)
        if self.errors or dates is None:
            return

        from appointments import calendar as clinic_calendar

        if repeat == self.SELECTED:
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
                    "until",
                    f"That would create {len(dates)} entries. Check the dates — "
                    f"the most that can be added at once is "
                    f"{weekday_codes.MAX_GENERATED}.",
                )
                return
            self._generated = dates

        if weekly:
            conflicts = clinic_calendar.find_conflicts(
                kind="schedule", doctor=cleaned["doctor"], cabin=cleaned["cabin"],
                start=start, end=end, weekday=dates[0].weekday(),
            )
        else:
            conflicts = []
            for day in dates:
                conflicts.extend(clinic_calendar.find_conflicts(
                    kind="override", doctor=cleaned["doctor"], cabin=cleaned["cabin"],
                    start=start, end=end, date=day,
                ))

        for conflict in conflicts[:5]:
            self.add_error(None, str(conflict))
        if len(conflicts) > 5:
            self.add_error(
                None, f"…and {len(conflicts) - 5} more dates with the same clash."
            )

    def _clean_leave(self, cleaned):
        """
        A doctor is away — the whole day, or a stretch of it (KAN-50).

        No conflict check: leave is allowed to land on top of working hours,
        because that is the entire point of recording it. What it must not do
        is land silently on top of *patients*, which the view reports after
        saving.
        """
        if not cleaned.get("doctor"):
            self.add_error("doctor", "Say who is away.")

        start = cleaned.get("leave_start_time")
        end = cleaned.get("leave_end_time")

        # Both or neither. One alone is somebody who started filling in a part
        # day and stopped, and storing it as a whole day would mark a doctor
        # away for hours they are in.
        if bool(start) != bool(end):
            self.add_error(
                "leave_end_time" if start else "leave_start_time",
                "Give both times, or leave both empty for the whole day.",
            )
        elif start and end and end <= start:
            self.add_error("leave_end_time", "The end time must be after the start time.")

        if cleaned.get("weekdays"):
            self.add_error(
                "weekdays",
                "Days of the week only apply to working hours.",
            )

        dates = self._dates(cleaned)
        if self.errors or dates is None:
            return

        from appointments.models import DoctorLeave

        # Already recorded is not an error — somebody extending a week of leave
        # by a day should not have to work out which days are new.
        self._existing_leave = set(
            DoctorLeave.objects.filter(
                doctor=cleaned["doctor"], date__in=dates,
                start_time=start, end_time=end,
            ).values_list("date", flat=True)
        )
        if len(self._existing_leave) == len(dates):
            self.add_error(
                "date",
                f"{cleaned['doctor'].display_name} is already recorded as away "
                + ("that day." if len(dates) == 1 else "on every one of those days."),
            )

    def _clean_holiday(self, cleaned):
        from appointments.models import ClinicHoliday

        if not cleaned.get("name"):
            self.add_error("name", "Give the holiday a name.")

        dates = self._dates(cleaned)
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

    # ── Saving ───────────────────────────────────────────────────────────────

    def save(self, created_by=None):
        """Create the rows and return them, newest question first."""
        from appointments.models import (
            ClinicHoliday, DoctorLeave, DoctorSchedule, ScheduleOverride,
        )

        cleaned = self.cleaned_data
        dates = self._dates(cleaned)
        created = []

        # Chosen weekdays narrow the range to the days actually ticked. Worked
        # out again rather than carried over from validation, so save() cannot
        # write a different set of dates from the one that was checked for
        # clashes.
        if cleaned.get("repeat") == self.SELECTED and cleaned.get("weekdays"):
            wanted = {
                weekday_codes.CODE_TO_WEEKDAY[c] for c in cleaned["weekdays"]
            }
            dates = [d for d in dates if d.weekday() in wanted]

        if cleaned["event_type"] == self.HOLIDAY:
            skipped = getattr(self, "_existing_holidays", set())
            for day in dates:
                if day in skipped:
                    continue
                created.append(ClinicHoliday.objects.create(
                    date=day, name=cleaned["name"],
                ))
            return created

        if cleaned["event_type"] == self.LEAVE:
            already = getattr(self, "_existing_leave", set())
            for day in dates:
                if day in already:
                    continue
                created.append(DoctorLeave.objects.create(
                    doctor=cleaned["doctor"],
                    date=day,
                    start_time=cleaned.get("leave_start_time") or None,
                    end_time=cleaned.get("leave_end_time") or None,
                    reason=cleaned.get("reason", ""),
                    created_by=created_by,
                ))
            return created

        if cleaned.get("repeat") == self.WEEKLY:
            created.append(DoctorSchedule.objects.create(
                doctor=cleaned["doctor"],
                weekday=dates[0].weekday(),
                cabin=cleaned["cabin"],
                start_time=cleaned["start_time"],
                end_time=cleaned["end_time"],
            ))
            return created

        for day in dates:
            created.append(ScheduleOverride.objects.create(
                doctor=cleaned["doctor"],
                date=day,
                cabin=cleaned["cabin"],
                start_time=cleaned["start_time"],
                end_time=cleaned["end_time"],
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
