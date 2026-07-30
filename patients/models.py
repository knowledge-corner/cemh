"""
Patient records.

The patient ID is the spine of this system: every visit, note, investigation,
measurement and prescription hangs off it, and in endocrinology that
longitudinal thread is the most valuable thing the clinic owns.
"""

from datetime import date

from django.conf import settings
from django.db import connection, models
from django.utils import timezone

from accounts.models import phone_validator

#: Name of the Postgres sequence backing patient-ID allocation.
#: Created in migration 0002; see :func:`allocate_patient_id`.
PATIENT_ID_SEQUENCE = "patient_id_seq"


def allocate_patient_id():
    """
    Return the next unused patient ID, e.g. ``KEC-26-00137``.

    Backed by a Postgres sequence rather than ``MAX(id) + 1``: two
    receptionists registering patients at the same moment would otherwise race
    and produce a duplicate. ``nextval`` is atomic and never hands the same
    number to two callers.

    Sequence numbers are not reused, so a rolled-back registration leaves a gap.
    That is intentional — gaps are harmless, collisions are not.
    """
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT nextval('{PATIENT_ID_SEQUENCE}')")
        serial = cursor.fetchone()[0]

    prefix = settings.CLINIC.PATIENT_ID_PREFIX
    year = timezone.localdate().strftime("%y")
    return f"{prefix}-{year}-{serial:05d}"


class Sex(models.TextChoices):
    MALE = "M", "Male"
    FEMALE = "F", "Female"
    OTHER = "O", "Other"


class BloodGroup(models.TextChoices):
    A_POS = "A+", "A+"
    A_NEG = "A-", "A-"
    B_POS = "B+", "B+"
    B_NEG = "B-", "B-"
    AB_POS = "AB+", "AB+"
    AB_NEG = "AB-", "AB-"
    O_POS = "O+", "O+"
    O_NEG = "O-", "O-"


class Patient(models.Model):
    """A person registered at the clinic."""

    patient_id = models.CharField(
        max_length=20,
        unique=True,
        editable=False,
        db_index=True,
        help_text="Clinic-wide unique identifier. Generated on registration; never changes.",
    )

    # Identity
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100, blank=True)
    date_of_birth = models.DateField()
    sex = models.CharField(max_length=1, choices=Sex.choices)
    blood_group = models.CharField(max_length=3, choices=BloodGroup.choices, blank=True)

    # Contact
    phone = models.CharField(max_length=15, validators=[phone_validator])
    alternate_phone = models.CharField(
        max_length=15, blank=True, validators=[phone_validator]
    )
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    pincode = models.CharField(max_length=10, blank=True)

    # Paediatric patients are accompanied, and the guardian is who the clinic
    # actually calls.
    guardian_name = models.CharField(max_length=200, blank=True)
    guardian_phone = models.CharField(
        max_length=15, blank=True, validators=[phone_validator]
    )
    guardian_relation = models.CharField(max_length=50, blank=True)

    referred_by = models.CharField(max_length=200, blank=True)

    # Set only if this patient has a login for the patient portal. Most will
    # not, so it stays null.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="patient_record",
    )

    is_active = models.BooleanField(default=True)
    registered_on = models.DateField(default=timezone.localdate)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["first_name", "last_name"]
        indexes = [
            models.Index(fields=["last_name", "first_name"]),
            models.Index(fields=["phone"]),
        ]

    def __str__(self):
        return f"{self.patient_id} — {self.full_name}"

    def save(self, *args, **kwargs):
        if not self.patient_id:
            self.patient_id = allocate_patient_id()
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("doctor_patient_dashboard", args=[self.patient_id])

    # ── Derived values ────────────────────────────────────────────────────────

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def age_in_days(self):
        return (date.today() - self.date_of_birth).days

    @property
    def age_years(self):
        """Completed years, accounting for whether this year's birthday has passed."""
        today = date.today()
        born = self.date_of_birth
        return today.year - born.year - ((today.month, today.day) < (born.month, born.day))

    @property
    def age_display(self):
        """Human-readable age — months matter enormously for infants."""
        today = date.today()
        born = self.date_of_birth
        years = self.age_years
        if years >= 2:
            return f"{years} yr"
        months = (today.year - born.year) * 12 + today.month - born.month
        if today.day < born.day:
            months -= 1
        if months >= 1:
            return f"{months} mo"
        return f"{(today - born).days} d"

    @property
    def is_paediatric(self):
        return self.age_years < settings.CLINIC.PAEDIATRIC_AGE_LIMIT

    @property
    def contact_phone(self):
        """Who to actually ring — the guardian for a child, else the patient."""
        if self.is_paediatric and self.guardian_phone:
            return self.guardian_phone
        return self.phone


class PatientHistory(models.Model):
    """
    Standing background on a patient, as opposed to what happened at one visit.

    Kept separate from :class:`~clinical.models.ClinicalNote` because these
    facts persist across visits and the doctor wants them on screen every time.
    """

    patient = models.OneToOneField(
        Patient, on_delete=models.CASCADE, related_name="history"
    )
    presenting_complaints = models.TextField(blank=True)
    past_medical_history = models.TextField(blank=True)
    family_history = models.TextField(
        blank=True, help_text="Especially diabetes, thyroid and endocrine disorders."
    )
    birth_history = models.TextField(
        blank=True, help_text="Paediatric: birth weight, gestation, delivery, milestones."
    )
    allergies = models.TextField(blank=True)
    current_medications = models.TextField(blank=True)
    surgical_history = models.TextField(blank=True)
    lifestyle_notes = models.TextField(blank=True)

    # Fields the doctor asks for later, before they have earned a column.
    extra = models.JSONField(default=dict, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "patient history"
        verbose_name_plural = "patient histories"

    def __str__(self):
        return f"History — {self.patient.patient_id}"

    @property
    def has_allergies(self):
        return bool(self.allergies.strip())
