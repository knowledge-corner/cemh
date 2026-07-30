"""
Prescriptions.

When the doctor finishes a consultation they generate the prescription. That
act is what hands the patient over to the receptionist — who prints it, takes
the fee and issues a receipt — so ``generated_at`` is a workflow signal, not
just a timestamp.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from appointments.models import Visit
from patients.models import Patient


class Prescription(models.Model):
    """The medication and advice issued at one visit."""

    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="prescription")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="prescriptions")
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="prescriptions_issued"
    )

    advice = models.TextField(blank=True, help_text="Diet, activity and general advice.")
    investigations_advised = models.TextField(
        blank=True, help_text="Tests to be done before the next visit."
    )
    follow_up_date = models.DateField(null=True, blank=True)
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
