"""
Prescriptions.

Printing moved to the doctor's own tab: the doctor writes, edits and prints a
prescription without needing reception at all. ``generated_at`` stays as the
one remaining reception-facing signal — a doctor who explicitly sends a
prescription to reception (still available on the tab) is still marking it
printable from Stage 4 alongside the receipt, exactly as before.

A prescription is not required to belong to a visit any more: a doctor can
write one for a patient at any time, not only while a consultation is open.
Where a visit *is* open, a new prescription still attaches to it (at most one
per visit, since reception's per-visit print still reads ``visit.prescription``
as a single object).
"""

import calendar

from django.conf import settings
from django.db import models
from django.utils import timezone

from appointments.models import Visit
from patients.models import Patient


def add_months_or_years(base_date, number, unit):
    """
    ``base_date`` plus ``number`` months or years, without a third-party
    dependency — a day past the end of the target month clamps to the last
    day of it (31 Jan + 1 month lands on 28/29 Feb, not into March).
    """
    if unit == Prescription.FollowUpUnit.YEAR:
        year = base_date.year + number
        day = min(base_date.day, calendar.monthrange(year, base_date.month)[1])
        return base_date.replace(year=year, day=day)

    month_index = base_date.month - 1 + number
    year = base_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base_date.day, calendar.monthrange(year, month)[1])
    return base_date.replace(year=year, month=month, day=day)


class Prescription(models.Model):
    """The medication and advice issued at one visit, or standing alone."""

    class FollowUpUnit(models.TextChoices):
        MONTH = "MONTH", "Month(s)"
        YEAR = "YEAR", "Year(s)"

    visit = models.OneToOneField(
        Visit, on_delete=models.CASCADE, related_name="prescription", null=True, blank=True,
    )
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="prescriptions")
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="prescriptions_issued"
    )

    #: Superseded by ClinicalNote.prescription_note, which is what the print
    #: view now shows. Kept on the model, off the form, so older prescriptions
    #: that already carry advice text do not lose it.
    advice = models.TextField(blank=True, help_text="Diet, activity and general advice.")
    investigations_advised = models.TextField(
        blank=True, help_text="Tests to be done before the next visit."
    )
    follow_up_number = models.PositiveSmallIntegerField(null=True, blank=True)
    follow_up_unit = models.CharField(max_length=10, choices=FollowUpUnit.choices, blank=True)
    follow_up_notes = models.CharField(max_length=300, blank=True)

    #: Set when the doctor finalises the prescription. Until then it is a
    #: draft the receptionist must not print.
    generated_at = models.DateTimeField(null=True, blank=True)
    printed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["patient", "-created_at"])]

    def __str__(self):
        return f"Prescription — {self.patient.patient_id} on {self.created_at:%d %b %Y}"

    @property
    def is_generated(self):
        return self.generated_at is not None

    @property
    def tentative_follow_up_date(self):
        """
        The printed follow-up date, reckoned from the day the prescription was
        written — not from whenever it happens to be reprinted — since a
        reprint weeks later should not silently push the follow-up window out.

        ``None`` when no follow-up was set, so the print sheet can leave the
        whole section out rather than printing an empty date.
        """
        if not self.follow_up_number or not self.follow_up_unit:
            return None
        return add_months_or_years(
            self.created_at.date(), self.follow_up_number, self.follow_up_unit
        )

    def generate(self):
        """Finalise the prescription and release it to the receptionist."""
        if self.generated_at is None:
            self.generated_at = timezone.now()
            self.save(update_fields=["generated_at", "updated_at"])
        return self


class PrescriptionItem(models.Model):
    """One medication line on a prescription."""

    prescription = models.ForeignKey(
        Prescription, on_delete=models.CASCADE, related_name="items"
    )
    drug_name = models.CharField(max_length=200)
    strength = models.CharField(max_length=50, blank=True, help_text="e.g. 50 mcg, 500 mg")
    dosage = models.CharField(max_length=100, blank=True, help_text="e.g. 1 tablet")
    frequency = models.CharField(max_length=100, blank=True, help_text="e.g. Once daily before breakfast")
    duration = models.CharField(max_length=100, blank=True, help_text="e.g. 3 months")
    instructions = models.CharField(max_length=300, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.drug_name} {self.strength}".strip()

    @property
    def summary_line(self):
        parts = [p for p in (self.drug_name, self.strength, self.dosage, self.frequency, self.duration) if p]
        return " — ".join(parts)
