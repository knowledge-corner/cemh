"""
Append-only audit trail.

Patient records are confidential. This table answers "who looked at this
patient's file, and when" — a question the clinic may have to answer to a
patient, and one that text logs cannot answer reliably.

Rows are written and never modified. The admin registration deliberately
refuses add, change and delete, and application code has no update path.
"""

from django.conf import settings
from django.db import models


class AuditAction(models.TextChoices):
    VIEW = "VIEW", "Viewed"
    CREATE = "CREATE", "Created"
    UPDATE = "UPDATE", "Updated"
    DELETE = "DELETE", "Deleted"
    PRINT = "PRINT", "Printed"
    LOGIN = "LOGIN", "Logged in"
    LOGIN_FAILED = "LOGIN_FAILED", "Failed login"
    LOGOUT = "LOGOUT", "Logged out"


class AccessLog(models.Model):
    """One recorded action against a record."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    # Kept as plain text so the trail survives the user row being deleted.
    username = models.CharField(max_length=150, blank=True)
    user_role = models.CharField(max_length=20, blank=True)

    action = models.CharField(max_length=20, choices=AuditAction.choices)

    # What was touched. Recorded as strings rather than a generic foreign key
    # so a deleted record still leaves a legible trail.
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=50, blank=True)

    # Denormalised so "show me everything anyone did to this patient" is one
    # indexed query, with no joins across deleted rows.
    patient_id_ref = models.CharField(
        "patient ID", max_length=20, blank=True, db_index=True
    )

    description = models.CharField(max_length=300, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    path = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "access log entry"
        verbose_name_plural = "access log"
        indexes = [
            models.Index(fields=["patient_id_ref", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        who = self.username or "anonymous"
        return f"{who} {self.get_action_display().lower()} {self.object_type} at {self.created_at:%d %b %Y %H:%M}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("Audit log entries are append-only and cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit log entries cannot be deleted.")
