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


class LabTest(models.Model):
    """
    The lab's master list of orderable tests — what can be tested, not what
    counts as normal.

    Loaded once from a seed catalogue (``clinical/data/lab_tests.tsv``, 500
    common tests). Deliberately carries no reference values itself: the
    catalogue it was seeded from ships every test with reference_low,
    reference_high and every other clinical column blank, on purpose — see
    LabReferenceRange, which is a separate table for exactly that reason.
    """

    code = models.CharField(max_length=20, unique=True, db_index=True)
    name = models.CharField(max_length=200, db_index=True)
    category = models.CharField(max_length=100, blank=True, db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]

    def __str__(self):
        return self.name


class ReferenceSex(models.TextChoices):
    ANY = "ANY", "Any"
    MALE = "MALE", "Male"
    FEMALE = "FEMALE", "Female"


class ReferenceStatus(models.TextChoices):
    #: Where every row starts — nothing here has been checked against a real
    #: source yet, matching the seed catalogue's own vocabulary for "we know
    #: the test exists, we do not yet know the range."
    REFERENCE_REQUIRED = "MASTER_ONLY_REFERENCE_REQUIRED", "Reference required"
    REVIEW_REQUIRED = "REVIEW_REQUIRED", "Review required"
    #: The only status that ever drives an automatic normal/abnormal flag —
    #: see clinical.lab_reference.evaluate_value. Everything else is visible
    #: in the admin and on the downloadable template, never silently acted on.
    VALIDATED = "VALIDATED", "Validated"
    DEPRECATED = "DEPRECATED", "Deprecated"


class LabReferenceRange(models.Model):
    """
    One clinically valid interval, for one band of patients, for one test.

    Its own table rather than columns on LabTest because a single test
    routinely needs several of these — a different band by sex, by age, by
    pregnancy state — and each band needs its own source and status kept
    separately, not merged into one number that quietly stops being correct
    for half the clinic's patients.

    Nothing here is seeded or invented. Every row exists because a clinician
    entered it — by hand in the admin, or through the downloadable
    spreadsheet template in ``clinical/lab_reference_csv.py`` — and reference
    material recommends preserving exactly this shape (source, version,
    population, provenance) rather than a bare number.
    """

    lab_test = models.ForeignKey(LabTest, on_delete=models.CASCADE, related_name="reference_ranges")

    sex = models.CharField(max_length=6, choices=ReferenceSex.choices)
    age_min = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    age_max = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    age_unit = models.CharField(max_length=10, default="years")
    pregnancy_status = models.CharField(max_length=50, blank=True)
    fasting_status = models.CharField(max_length=50, blank=True)

    #: At least one of these is required — some analytes only report a
    #: ceiling ("less than 5"), some only a floor.
    low = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    high = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    unit = models.CharField(max_length=50)

    source = models.CharField(max_length=300)
    source_year = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)

    status = models.CharField(
        max_length=32, choices=ReferenceStatus.choices,
        default=ReferenceStatus.REFERENCE_REQUIRED,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["lab_test__name", "sex", "age_min"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(low__isnull=False) | models.Q(high__isnull=False),
                name="reference_range_needs_a_bound",
            ),
        ]

    def __str__(self):
        band = "Any" if self.sex == ReferenceSex.ANY else self.get_sex_display()
        bounds = f"{self.low if self.low is not None else '?'}–{self.high if self.high is not None else '?'}"
        return f"{self.lab_test.name} ({band}) — {bounds} {self.unit}"

    def covers_age(self, age_years):
        """Whether this band applies at all, or applies to this age."""
        if age_years is None:
            return self.age_min is None and self.age_max is None
        if self.age_min is not None and age_years < float(self.age_min):
            return False
        if self.age_max is not None and age_years > float(self.age_max):
            return False
        return True


class LabUnitConversion(models.Model):
    """
    ``value_in_to_unit = value_in_from_unit * multiplier + offset``

    Scoped to one test by default, because the seed catalogue's own
    documentation is explicit that most lab unit conversions are
    analyte-specific (a mass-to-molar conversion needs that analyte's molar
    mass) and cannot be represented by one universal multiplier. ``lab_test``
    is left blank only for a conversion that genuinely is universal — a
    straightforward metric-prefix change, say — never as a default.
    """

    lab_test = models.ForeignKey(
        LabTest, on_delete=models.CASCADE, related_name="unit_conversions",
        null=True, blank=True,
        help_text="Leave blank only for a conversion that holds for every test.",
    )
    from_unit = models.CharField(max_length=50)
    to_unit = models.CharField(max_length=50)
    multiplier = models.DecimalField(max_digits=18, decimal_places=8)
    offset = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["from_unit", "to_unit"])]

    def __str__(self):
        scope = self.lab_test.name if self.lab_test_id else "any test"
        return f"{self.from_unit} → {self.to_unit} ({scope})"

    def convert(self, value):
        return value * self.multiplier + self.offset


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

    lab_test = models.ForeignKey(
        LabTest, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="investigations",
        help_text="Set when the test name was picked from the master list.",
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


class ICD10Code(models.Model):
    """
    WHO ICD-10 diagnostic codes, loaded once from the published classification
    (see ``clinical/data/icd10_codes.tsv`` and the migration that reads it).

    Reference data, not clinic-authored — nothing here is ever created or
    edited through the app itself. Looked up by the diagnosis autocomplete
    (``portal.views_doctor.icd10_search``); ``Diagnosis.icd10_code`` stays a
    plain text field rather than a foreign key to this table, so a diagnosis
    typed before a match is chosen, or one that never matches any code, still
    saves cleanly as free text.
    """

    code = models.CharField(max_length=10, unique=True, db_index=True)
    description = models.CharField(max_length=300, db_index=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "ICD-10 code"
        verbose_name_plural = "ICD-10 codes"

    def __str__(self):
        return f"{self.code} — {self.description}"


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

    At most one per consultation, the same cap as a clinical note or
    prescription. ``visit`` is nullable for the same reason it is on
    Prescription: a letter can still be written with nobody currently in the
    cabin, in which case it stands alone rather than being forced onto a
    visit — see ``portal.views_edit._attach_reference_letter``.
    """

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="reference_letters")
    visit = models.ForeignKey(
        Visit, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="reference_letters",
    )
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
