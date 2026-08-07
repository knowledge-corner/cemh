"""
The clinical record: notes, investigations and diagnoses.

**On fields that are not settled yet.** The clinic has said the exact items the
doctor and receptionist capture will be worked out over the coming months. The
rule this app follows:

* Anything that must be **searched, charted, or reported on** gets a real typed
  column — lab values, dates, numeric results. Those are queryable and indexable.
* Everything else goes in the ``extra`` JSON field, described by a
  :class:`FormDefinition` row.

That means a new question can be added to a form from the admin, in production,
without a migration or a deploy — while the things that must be trended stay in
proper columns where the database can do real work on them.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from appointments.models import Visit
from patients.models import Patient


class ClinicalNote(models.Model):
    """
    What the doctor recorded during one consultation.

    Two boxes: ``clinical_notes`` is the doctor's own reference and is never
    printed; ``prescription_note`` is what appears on the printed prescription
    for this same visit. Older notes still carry the earlier complaint /
    examination / assessment / plan structure — those fields are kept, and
    shown read-only, so nothing already written is lost; the form just does
    not offer them for a new note any more.
    """

    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="note")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="authored_notes"
    )

    clinical_notes = models.TextField(
        blank=True, help_text="The doctor's own reference. Never printed."
    )
    prescription_note = models.TextField(
        blank=True, help_text="Printed on the prescription written for this visit."
    )

    #: Superseded by the two fields above. Kept for notes written before this
    #: change, and still shown where a note actually has content in them.
    complaints = models.TextField(blank=True, help_text="What the patient reports today.")
    examination = models.TextField(blank=True, help_text="Findings on examination.")
    assessment = models.TextField(blank=True, help_text="Clinical impression.")
    plan = models.TextField(blank=True, help_text="Management plan and advice.")

    # Vitals recorded at the consultation. Typed because they are trended.
    systolic_bp = models.PositiveSmallIntegerField(null=True, blank=True)
    diastolic_bp = models.PositiveSmallIntegerField(null=True, blank=True)
    pulse = models.PositiveSmallIntegerField(null=True, blank=True)
    temperature_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)

    #: Clinic-specific questions, shaped by whichever FormDefinition was
    #: current when the note was written.
    extra = models.JSONField(default=dict, blank=True)
    form_version = models.PositiveIntegerField(
        default=1,
        help_text="Which version of the note form produced `extra`. Lets old notes "
                  "keep rendering correctly after the form changes.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["patient", "-created_at"])]

    def __str__(self):
        return f"Note — {self.patient.patient_id} on {self.created_at:%d %b %Y}"

    @property
    def blood_pressure(self):
        if self.systolic_bp and self.diastolic_bp:
            return f"{self.systolic_bp}/{self.diastolic_bp}"
        return ""

    @property
    def has_legacy_content(self):
        """Whether this note carries the pre-simplification fields."""
        return any([self.complaints, self.examination, self.assessment, self.plan])

    @property
    def is_empty(self):
        return not any([
            self.clinical_notes, self.prescription_note, self.has_legacy_content,
        ])


class InvestigationCategory(models.TextChoices):
    THYROID = "THYROID", "Thyroid"
    DIABETES = "DIABETES", "Diabetes / Glycaemic"
    HORMONE = "HORMONE", "Hormone assay"
    BONE = "BONE", "Bone & mineral"
    LIPID = "LIPID", "Lipid profile"
    IMAGING = "IMAGING", "Imaging"
    OTHER = "OTHER", "Other"


class Investigation(models.Model):
    """
    One test result.

    Stored one row per analyte rather than one row per report, because the
    clinical question is almost always "how has this patient's TSH moved over
    three years", which needs each value on its own row to trend.
    """

    patient = models.ForeignKey(
        Patient, on_delete=models.CASCADE, related_name="investigations"
    )
    visit = models.ForeignKey(
        Visit,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="investigations",
        help_text="The visit this result was reviewed at, if any.",
    )

    test_name = models.CharField(max_length=200, db_index=True)
    category = models.CharField(
        max_length=20, choices=InvestigationCategory.choices, default=InvestigationCategory.OTHER
    )

    # Kept as text so free-form results ("Negative", "<0.01") survive alongside
    # numbers; `value_numeric` carries the machine-readable copy for charting.
    value = models.CharField(max_length=100)
    value_numeric = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        help_text="Numeric copy of the value, when it is a number. Used for trends.",
    )
    unit = models.CharField(max_length=50, blank=True)
    reference_range = models.CharField(max_length=100, blank=True)
    is_abnormal = models.BooleanField(default=False)

    performed_on = models.DateField(default=timezone.localdate, db_index=True)
    lab_name = models.CharField(max_length=200, blank=True)
    report_file = models.FileField(upload_to="reports/%Y/%m/", blank=True, null=True)
    notes = models.TextField(blank=True)

    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="recorded_investigations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-performed_on", "test_name"]
        indexes = [
            models.Index(fields=["patient", "test_name", "-performed_on"]),
        ]

    def __str__(self):
        return f"{self.test_name}: {self.value} {self.unit}".strip()

    @property
    def display_value(self):
        return f"{self.value} {self.unit}".strip()


class Diagnosis(models.Model):
    """
    A condition the patient carries.

    Attached to the patient rather than a single visit, because endocrine
    diagnoses persist — the doctor wants "known hypothyroid since 2019" on
    screen at every visit, not buried in one old note.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        RESOLVED = "RESOLVED", "Resolved"
        RULED_OUT = "RULED_OUT", "Ruled out"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="diagnoses")
    visit = models.ForeignKey(
        Visit, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="diagnoses", help_text="Visit at which this was first recorded.",
    )

    description = models.CharField(max_length=300)
    icd10_code = models.CharField(max_length=10, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    diagnosed_on = models.DateField(default=timezone.localdate)
    resolved_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-diagnosed_on"]
        verbose_name_plural = "diagnoses"
        indexes = [models.Index(fields=["patient", "status"])]

    def __str__(self):
        return self.description

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE


class ReferenceLetter(models.Model):
    """
    A letter written for the patient — school, insurance, travel, fitness — in
    the doctor's own words rather than a structured form.

    Not tied to a visit: the request for one often has nothing to do with why
    the patient was last seen, and can arrive well after the consultation it
    concerns.
    """

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="reference_letters")
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="reference_letters_issued"
    )

    to = models.CharField(max_length=200, help_text="Who this letter is addressed to.")
    note = models.TextField(help_text="The body of the letter.")

    #: A reprint changes nothing about the record — see Prescription.printed_at
    #: for why this is set once and then left alone.
    printed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["patient", "-created_at"])]

    def __str__(self):
        return f"Reference letter — {self.patient.patient_id} to {self.to}"


class FormDefinition(models.Model):
    """
    Describes the clinic-specific questions rendered into a record's ``extra``.

    This is how the system absorbs "the doctor wants us to also capture X"
    without a code change. ``schema`` is a list of field descriptors::

        [
          {"key": "insulin_regimen", "label": "Insulin regimen", "type": "text"},
          {"key": "hba1c_target",    "label": "HbA1c target %",  "type": "number"},
          {"key": "smoker",          "label": "Smoker",          "type": "boolean"},
          {"key": "diet",            "label": "Diet",            "type": "choice",
           "choices": ["Vegetarian", "Non-vegetarian", "Vegan"]}
        ]

    Definitions are versioned and never edited in place once used: a record
    stores the version it was captured under, so a note written last year still
    renders with the questions that were actually asked.
    """

    class Target(models.TextChoices):
        CLINICAL_NOTE = "CLINICAL_NOTE", "Clinical note"
        PATIENT_HISTORY = "PATIENT_HISTORY", "Patient history"
        RECEPTION_INTAKE = "RECEPTION_INTAKE", "Reception intake"

    target = models.CharField(max_length=30, choices=Target.choices)
    version = models.PositiveIntegerField(default=1)
    schema = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["target", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["target", "version"], name="formdef_unique_target_version"),
            # Exactly one active definition per target, so rendering code never
            # has to guess which one applies.
            models.UniqueConstraint(
                fields=["target"],
                condition=models.Q(is_active=True),
                name="formdef_one_active_per_target",
            ),
        ]

    def __str__(self):
        return f"{self.get_target_display()} v{self.version}"

    @classmethod
    def current(cls, target):
        return cls.objects.filter(target=target, is_active=True).first()
