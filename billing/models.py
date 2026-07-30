"""
Fees, payments and receipts.

The doctor records the consultation fee when finishing the consultation; the
receptionist collects it and issues a receipt. Screens for this arrive in a
later phase — the models exist now so the visit lifecycle has somewhere to land
and nothing has to be retro-fitted.
"""

from decimal import Decimal

from django.conf import settings
from django.db import connection, models
from django.utils import timezone

from appointments.models import Visit
from patients.models import Patient

RECEIPT_SEQUENCE = "receipt_number_seq"


def allocate_receipt_number():
    """
    Next receipt number, e.g. ``R-26-00042``.

    Uses a Postgres sequence for the same reason patient IDs do: receipt
    numbers must never collide, and a ``MAX() + 1`` lookup races.
    """
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT nextval('{RECEIPT_SEQUENCE}')")
        serial = cursor.fetchone()[0]
    year = timezone.localdate().strftime("%y")
    return f"R-{year}-{serial:05d}"


class PaymentMethod(models.TextChoices):
    CASH = "CASH", "Cash"
    UPI = "UPI", "UPI"
    CARD = "CARD", "Card"
    BANK_TRANSFER = "BANK_TRANSFER", "Bank transfer"
    OTHER = "OTHER", "Other"


class Charge(models.Model):
    """
    What the patient owes for a visit.

    Entered by the doctor at the end of the consultation, then settled by the
    receptionist.
    """

    visit = models.OneToOneField(Visit, on_delete=models.CASCADE, related_name="charge")
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="charges")

    consultation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    procedure_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    notes = models.CharField(max_length=300, blank=True)

    set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="charges_set",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Charge — {self.patient.patient_id}: ₹{self.total}"

    @property
    def total(self):
        return self.consultation_fee + self.procedure_fee - self.discount

    @property
    def amount_paid(self):
        return sum((p.amount for p in self.payments.all()), Decimal("0.00"))

    @property
    def balance(self):
        return self.total - self.amount_paid

    @property
    def is_settled(self):
        return self.balance <= 0


class Payment(models.Model):
    """Money actually received, recorded by the receptionist."""

    charge = models.ForeignKey(Charge, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    reference = models.CharField(max_length=100, blank=True, help_text="UPI reference or card approval code.")
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="payments_received",
    )
    received_at = models.DateTimeField(default=timezone.now)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"₹{self.amount} via {self.get_method_display()}"


class Receipt(models.Model):
    """Issued once payment is taken. Numbers are immutable."""

    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="receipt")
    receipt_number = models.CharField(max_length=20, unique=True, editable=False, db_index=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    printed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return self.receipt_number

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            self.receipt_number = allocate_receipt_number()
        super().save(*args, **kwargs)
