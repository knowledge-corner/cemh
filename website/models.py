"""
What the public page collects.

Exactly one thing: a request to be rung back. There is deliberately no patient
portal — nobody books themselves in, and nothing about a patient's record is
online — so this is the whole of the public site's write access to the system.

It is a table rather than an email because an email to a shared inbox is a
request nobody owns. A row has a state, so reception can see what is
outstanding and what has been dealt with, and a request cannot quietly go
unanswered because somebody archived a message.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone


class CallbackStatus(models.TextChoices):
    NEW = "NEW", "To call"
    DONE = "DONE", "Called back"
    IGNORED = "IGNORED", "No action needed"


class CallbackRequest(models.Model):
    """Somebody on the public page asking the clinic to telephone them."""

    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)

    #: Free text rather than a foreign key. Whoever fills this in is not yet a
    #: patient and may name a doctor who has left, or none at all.
    preferred_doctor = models.CharField(max_length=120, blank=True)
    concern = models.TextField(blank=True)

    status = models.CharField(
        max_length=10, choices=CallbackStatus.choices,
        default=CallbackStatus.NEW, db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="callbacks_handled",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "callback request"

    def __str__(self):
        return f"{self.name} · {self.phone} ({self.get_status_display()})"

    @property
    def is_outstanding(self):
        return self.status == CallbackStatus.NEW

    def close(self, status, by_user=None):
        """Mark this request dealt with, recording who and when."""
        self.status = status
        self.handled_at = timezone.now()
        self.handled_by = by_user
        self.save(update_fields=["status", "handled_at", "handled_by"])
        return self
