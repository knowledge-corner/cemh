"""
Anthropometry and growth charts.

**This is an optional app.** A clinic that does not need growth charts removes
``growth`` from ``OPTIONAL_APPS`` in ``config/clinic.py`` and the models, admin
and dashboard tab all disappear together. Nothing in the core apps imports from
here — that is what keeps the removal clean.

For a paediatric endocrinologist this is the centrepiece: a single height is
almost meaningless, but height plotted against age on a percentile chart, and
the velocity between visits, is most of the diagnosis.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from appointments.models import Visit
from patients.models import Patient


class PubertyStage(models.TextChoices):
    """Tanner staging — sexual maturity rating."""

    T1 = "1", "Stage 1 (prepubertal)"
    T2 = "2", "Stage 2"
    T3 = "3", "Stage 3"
    T4 = "4", "Stage 4"
    T5 = "5", "Stage 5 (adult)"


class Measurement(models.Model):
    """
    One set of anthropometric readings, normally taken at a visit.

    Height and weight are typed decimals rather than JSON precisely because
    they are charted and trended — this is the line the ``extra``-JSON approach
    used elsewhere deliberately does not cross.
    """

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="measurements")
    visit = models.ForeignKey(
        Visit, on_delete=models.SET_NULL, null=True, blank=True, related_name="measurements"
    )

    measured_on = models.DateField(default=timezone.localdate, db_index=True)

    height_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    head_circumference_cm = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        help_text="Clinically meaningful under about 3 years.",
    )
    waist_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)

    puberty_stage = models.CharField(max_length=1, choices=PubertyStage.choices, blank=True)

    #: From an X-ray reading (e.g. Greulich-Pyle), in decimal years — 8.3 for
    #: "8 years 3 months". Recorded per measurement, like everything else here,
    #: because a child is X-rayed only occasionally and the reading belongs to
    #: whichever visit ordered it, not to the patient generally.
    bone_age_years = models.DecimalField(
        max_digits=4, decimal_places=1, null=True, blank=True,
        help_text="From an X-ray reading, e.g. 8.3 for 8 years 3 months.",
    )

    # Parental heights change rarely but are needed to compute the child's
    # target height, so they are recorded against the measurement that used them.
    mother_height_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    father_height_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)

    notes = models.CharField(max_length=300, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="recorded_measurements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-measured_on"]
        indexes = [models.Index(fields=["patient", "-measured_on"])]

    def __str__(self):
        return f"{self.patient.patient_id} on {self.measured_on:%d %b %Y}"

    # ── Derived values ────────────────────────────────────────────────────────

    @property
    def bmi(self):
        """Body mass index, kg/m². Computed rather than stored so it can never
        drift out of step with the height and weight it comes from."""
        if not (self.height_cm and self.weight_kg):
            return None
        metres = Decimal(self.height_cm) / Decimal("100")
        if metres <= 0:
            return None
        return round(Decimal(self.weight_kg) / (metres * metres), 1)

    @property
    def age_days(self):
        """Patient's age on the day of measurement — not today."""
        return (self.measured_on - self.patient.date_of_birth).days

    @property
    def age_months(self):
        return self.age_days / 30.4375

    @property
    def age_years(self):
        return self.age_days / 365.25

    @property
    def bone_age_delta_years(self):
        """
        Bone age minus chronological age, at the time of this measurement.

        Positive means advanced (skeleton reading older than the birthday
        says), negative means delayed. ``None`` unless a bone age was
        actually recorded — most measurements never have one.
        """
        if self.bone_age_years is None:
            return None
        return round(Decimal(self.bone_age_years) - Decimal(str(round(self.age_years, 1))), 1)

    @property
    def mid_parental_height_cm(self):
        """
        Target adult height from parental heights.

        Boys:  (father + mother + 13) / 2
        Girls: (father + mother - 13) / 2

        Returns ``None`` unless both parental heights and the patient's sex are
        known.
        """
        if not (self.mother_height_cm and self.father_height_cm):
            return None
        total = Decimal(self.mother_height_cm) + Decimal(self.father_height_cm)
        if self.patient.sex == "M":
            return round((total + Decimal("13")) / 2, 1)
        if self.patient.sex == "F":
            return round((total - Decimal("13")) / 2, 1)
        return None
