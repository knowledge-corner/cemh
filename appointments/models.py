"""
Slots, visits and the visit lifecycle.

A ``Visit`` is the object the whole clinic day revolves around. It starts when
someone books — the patient online, or the receptionist over the phone — and
moves through a fixed sequence of states as the patient is confirmed, arrives,
is called into the cabin, is seen, is billed and leaves.

Every state change is recorded, so "when did this patient actually arrive" and
"how long did they wait" are answerable, and so there is a trail of who moved
what.
"""

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Func, Q
from django.utils import timezone

from patients.models import Patient


class TsTzRange(Func):
    """Builds a Postgres ``tstzrange`` from the scheduled start and end."""

    function = "TSTZRANGE"
    output_field = models.DateTimeField()


class VisitStatus(models.TextChoices):
    BOOKED = "BOOKED", "Booked"
    CONFIRMED = "CONFIRMED", "Confirmed"
    ARRIVED = "ARRIVED", "Arrived"
    IN_CABIN = "IN_CABIN", "In cabin"
    CONSULTED = "CONSULTED", "Consulted"
    BILLED = "BILLED", "Billed"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"
    NO_SHOW = "NO_SHOW", "No show"


#: The only state changes the clinic workflow permits.
#:
#:   BOOKED → CONFIRMED → ARRIVED → IN_CABIN → CONSULTED → BILLED → COMPLETED
#:                ↓          ↓
#:            CANCELLED   NO_SHOW
#:
#: Enforced in :meth:`Visit.transition_to`. Assigning ``visit.status``
#: directly bypasses this and must not be done outside migrations.
ALLOWED_TRANSITIONS = {
    VisitStatus.BOOKED: {VisitStatus.CONFIRMED, VisitStatus.CANCELLED},
    VisitStatus.CONFIRMED: {VisitStatus.ARRIVED, VisitStatus.CANCELLED, VisitStatus.NO_SHOW},
    VisitStatus.ARRIVED: {VisitStatus.IN_CABIN, VisitStatus.CANCELLED},
    VisitStatus.IN_CABIN: {VisitStatus.CONSULTED},
    VisitStatus.CONSULTED: {VisitStatus.BILLED},
    VisitStatus.BILLED: {VisitStatus.COMPLETED},
    VisitStatus.COMPLETED: set(),
    VisitStatus.CANCELLED: set(),
    VisitStatus.NO_SHOW: set(),
}

#: Statuses that no longer occupy a slot, so they are excluded from the
#: double-booking constraint and from the day's queue.
#:
#: Ordered, not a set: this list goes into a database constraint, and a set's
#: iteration order varies between runs, which makes ``makemigrations`` detect a
#: change on every invocation.
INACTIVE_STATUSES = (VisitStatus.CANCELLED, VisitStatus.NO_SHOW, VisitStatus.COMPLETED)


class InvalidTransition(ValidationError):
    """Raised when code attempts a state change the workflow does not allow."""


class VisitQuerySet(models.QuerySet):
    def for_date(self, day=None):
        day = day or timezone.localdate()
        return self.filter(scheduled_start__date=day)

    def active(self):
        """Visits still in play — excludes cancelled, no-show and completed."""
        return self.exclude(status__in=INACTIVE_STATUSES)

    def waiting_room(self):
        """Patients physically present and not yet with the doctor."""
        return self.filter(status=VisitStatus.ARRIVED)

    def with_related(self):
        return self.select_related("patient", "doctor", "doctor__doctor_profile")


class Visit(models.Model):
    """One patient's appointment with one doctor on one day."""

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="visits")
    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="visits_as_doctor",
        limit_choices_to={"role": "DOCTOR"},
    )

    scheduled_start = models.DateTimeField()
    scheduled_end = models.DateTimeField()

    status = models.CharField(
        max_length=20,
        choices=VisitStatus.choices,
        default=VisitStatus.BOOKED,
        db_index=True,
    )

    reason = models.CharField(
        max_length=300, blank=True, help_text="Why the patient is coming in."
    )
    is_follow_up = models.BooleanField(default=False)

    booked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visits_booked",
        help_text="Receptionist who took the booking; empty if the patient booked online.",
    )

    # Timestamps captured as the patient moves through the clinic. These drive
    # waiting-time reporting, so they are recorded at the moment of transition
    # rather than derived afterwards.
    arrived_at = models.DateTimeField(null=True, blank=True)
    entered_cabin_at = models.DateTimeField(null=True, blank=True)
    consulted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = VisitQuerySet.as_manager()

    class Meta:
        ordering = ["scheduled_start"]
        indexes = [
            models.Index(fields=["scheduled_start", "status"]),
            models.Index(fields=["patient", "-scheduled_start"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(scheduled_end__gt=F("scheduled_start")),
                name="visit_end_after_start",
            ),
            # Two patients cannot hold the same doctor at the same time. Doing
            # this in the database rather than in a view means a double-booking
            # is impossible even under concurrent requests — the case a
            # check-then-insert in Python always eventually loses.
            ExclusionConstraint(
                name="visit_no_double_booking",
                expressions=[
                    (TsTzRange("scheduled_start", "scheduled_end", RangeBoundary()),
                     RangeOperators.OVERLAPS),
                    ("doctor", RangeOperators.EQUAL),
                ],
                condition=~Q(status__in=list(INACTIVE_STATUSES)),  # noqa: E501 — order is fixed above
            ),
        ]

    def __str__(self):
        return f"{self.patient.patient_id} @ {timezone.localtime(self.scheduled_start):%d %b %Y %H:%M}"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def can_transition_to(self, new_status):
        return new_status in ALLOWED_TRANSITIONS.get(self.status, set())

    @property
    def available_transitions(self):
        return sorted(ALLOWED_TRANSITIONS.get(self.status, set()))

    def transition_to(self, new_status, by_user=None, note=""):
        """
        Move this visit to ``new_status``, recording who did it and when.

        The single supported way to change a visit's status. Raises
        :class:`InvalidTransition` if the workflow does not allow the move, so
        an out-of-order click at reception fails loudly instead of corrupting
        the day's queue.
        """
        new_status = VisitStatus(new_status)

        if new_status == self.status:
            return self

        if not self.can_transition_to(new_status):
            raise InvalidTransition(
                f"A visit that is {self.get_status_display().lower()} cannot become "
                f"{VisitStatus(new_status).label.lower()}."
            )

        previous = self.status
        now = timezone.now()

        stamp_field = {
            VisitStatus.ARRIVED: "arrived_at",
            VisitStatus.IN_CABIN: "entered_cabin_at",
            VisitStatus.CONSULTED: "consulted_at",
            VisitStatus.COMPLETED: "completed_at",
        }.get(new_status)

        self.status = new_status
        updated = ["status", "updated_at"]
        if stamp_field:
            setattr(self, stamp_field, now)
            updated.append(stamp_field)

        self.save(update_fields=updated)

        VisitStatusEvent.objects.create(
            visit=self,
            from_status=previous,
            to_status=new_status,
            changed_by=by_user,
            note=note,
        )
        return self

    # ── Derived values ────────────────────────────────────────────────────────

    @property
    def is_active(self):
        return self.status not in INACTIVE_STATUSES

    @property
    def waiting_minutes(self):
        """How long the patient sat in the waiting room, once known."""
        if not self.arrived_at:
            return None
        end = self.entered_cabin_at or timezone.now()
        return int((end - self.arrived_at).total_seconds() // 60)

    @property
    def consultation_minutes(self):
        if not (self.entered_cabin_at and self.consulted_at):
            return None
        return int((self.consulted_at - self.entered_cabin_at).total_seconds() // 60)


class VisitStatusEvent(models.Model):
    """
    Append-only record of one status change.

    Written by :meth:`Visit.transition_to` and never edited. Gives the clinic a
    truthful timeline of each visit and satisfies the "who moved this patient"
    question without a separate audit lookup.
    """

    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="status_events")
    from_status = models.CharField(max_length=20, choices=VisitStatus.choices)
    to_status = models.CharField(max_length=20, choices=VisitStatus.choices)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visit_status_changes",
    )
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "visit status event"

    def __str__(self):
        return f"{self.visit_id}: {self.from_status} → {self.to_status}"
