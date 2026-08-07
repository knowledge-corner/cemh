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

from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Func, Q
from django.db.models.functions import Lower
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
#: BOOKED reaches ARRIVED directly as well as through CONFIRMED. The board no
#: longer keeps a separate "to confirm" stage — a booking and a confirmed
#: booking sit together in one Appointments column — so the receptionist marks
#: the patient arrived from whichever state the booking is in, without a
#: telephone step nobody performs any more.
#:
#: CONFIRMED is kept rather than removed: visits already carry it, backward
#: movement out of the waiting room lands on it, and a clinic that decides to
#: start ringing patients again should not need a migration to do it.
#:
#: Enforced in :meth:`Visit.transition_to`. Assigning ``visit.status``
#: directly bypasses this and must not be done outside migrations.
ALLOWED_TRANSITIONS = {
    VisitStatus.BOOKED: {
        VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.CANCELLED,
    },
    VisitStatus.CONFIRMED: {VisitStatus.ARRIVED, VisitStatus.CANCELLED, VisitStatus.NO_SHOW},
    VisitStatus.ARRIVED: {VisitStatus.IN_CABIN, VisitStatus.CANCELLED},
    VisitStatus.IN_CABIN: {VisitStatus.CONSULTED},
    VisitStatus.CONSULTED: {VisitStatus.BILLED},
    VisitStatus.BILLED: {VisitStatus.COMPLETED},
    VisitStatus.COMPLETED: set(),
    VisitStatus.CANCELLED: set(),
    VisitStatus.NO_SHOW: set(),
}

#: Corrections — one step back, and only while the visit is still open (KAN-9).
#:
#: Kept separate from ALLOWED_TRANSITIONS rather than merged into it. Forward is
#: the clinic day happening; backward is somebody fixing a mis-click, and the two
#: want different rules, different permissions and a different note in the trail.
#: Merging them would also make "what happens next" unreadable.
#:
#: Nothing goes back out of CONSULTED. That is the point at which the doctor has
#: finished and the record locks; a correction after that is a clinical
#: amendment, not a queue fix.
BACKWARD_TRANSITIONS = {
    VisitStatus.CONFIRMED: VisitStatus.BOOKED,
    VisitStatus.ARRIVED: VisitStatus.CONFIRMED,
    VisitStatus.IN_CABIN: VisitStatus.ARRIVED,
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

    def unfinished_before(self, day=None):
        """
        Visits from before ``day`` that were never closed off.

        A patient left showing as in the cabin overnight is not a record of
        anything — it is a queue somebody forgot to clear, and it makes today's
        board lie. This is what the end-of-day sweep works from.
        """
        day = day or timezone.localdate()
        return (
            self.filter(scheduled_start__date__lt=day)
            .active()
            .with_related()
            .order_by("scheduled_start")
        )


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
    #: Booked with no prior appointment, standing at the desk — see
    #: BookingForm's walk-in option. Recorded rather than inferred (e.g. from
    #: status or timing), so a walk-in is still identifiable after it has
    #: moved through the board, appeared in the doctor's queue, or been swept
    #: into a day sheet.
    is_walk_in = models.BooleanField(default=False)

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

        # A doctor sees one patient at a time. Sending a second person in while
        # somebody is still in the cabin is always a mis-click, and the damage
        # is real: the consultation actually happening loses its place. Refused
        # here rather than in the view, so it holds wherever the move comes from.
        if new_status == VisitStatus.IN_CABIN:
            # Scoped to this visit's own day. A doctor sees one patient at a
            # time, but a visit left open from a previous day is a queue nobody
            # closed, not a consultation in progress — and letting it block
            # today's clinic would turn a tidying-up problem into a stoppage.
            # The end-of-day sweep is what deals with those.
            occupied = (
                Visit.objects.filter(
                    doctor=self.doctor,
                    status=VisitStatus.IN_CABIN,
                    scheduled_start__date=timezone.localtime(self.scheduled_start).date(),
                )
                .exclude(pk=self.pk)
                .select_related("patient")
                .first()
            )
            if occupied is not None:
                raise InvalidTransition(
                    f"{occupied.patient.full_name} is already in "
                    f"{self.doctor.display_name}'s cabin. Finish that consultation first."
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

    # ── Corrections and the edit lock (KAN-9) ────────────────────────────────

    #: The point after which a visit is a clinical record rather than a queue
    #: entry. Editing stops here, not at Settled: the doctor has finished, and
    #: what was recorded is what happened.
    LOCKED_STATUSES = (
        VisitStatus.CONSULTED, VisitStatus.BILLED, VisitStatus.COMPLETED,
    )

    @property
    def is_locked(self):
        """Has the doctor finished, making this a record rather than a booking?"""
        return self.status in self.LOCKED_STATUSES

    @property
    def previous_status(self):
        """The stage this visit can be put back to, or ``None``."""
        if self.is_locked:
            return None
        return BACKWARD_TRANSITIONS.get(self.status)

    def move_back(self, by_user=None, note=""):
        """
        Put this visit back one stage — a correction, not part of the workflow.

        Raises :class:`InvalidTransition` when there is nowhere to go back to,
        which covers both the first stage and anything the doctor has finished
        with. Moving out of the cabin frees it for the next patient, which falls
        out of the status change rather than needing its own step.
        """
        target = self.previous_status
        if target is None:
            raise InvalidTransition(
                f"A visit that is {self.get_status_display().lower()} cannot be "
                f"moved back."
                if self.is_locked else
                f"{self.get_status_display()} is the first stage; there is "
                f"nothing before it."
            )

        previous = self.status
        self.status = target
        updated = ["status", "updated_at"]

        # Give up the stamp for the stage being left (KAN-34). Without this a
        # visit put back out of the cabin keeps entered_cabin_at, so the
        # waiting timer freezes at however long the mis-click lasted — and on a
        # second round trip arrived_at is re-stamped *later* than that stale
        # cabin time, which is where the reported "-5 min" came from.
        #
        # It is also simply true: the visit is not in that stage any more, and
        # a time recording when it entered a stage it has left is a fact about
        # nothing.
        forget = {
            VisitStatus.ARRIVED: "arrived_at",
            VisitStatus.IN_CABIN: "entered_cabin_at",
            VisitStatus.CONSULTED: "consulted_at",
            VisitStatus.COMPLETED: "completed_at",
        }.get(previous)
        if forget:
            setattr(self, forget, None)
            updated.append(forget)

        self.save(update_fields=updated)

        VisitStatusEvent.objects.create(
            visit=self,
            from_status=previous,
            to_status=target,
            changed_by=by_user,
            note=f"Moved back to {VisitStatus(target).label}"
                 + (f" — {note}" if note else ""),
        )
        return self

    @property
    def confirmation(self):
        """
        Who confirmed this booking by telephone, and when.

        Read from the status trail rather than stored again on the visit: the
        trail already answers it and a second copy is a second thing to get out
        of step. Reads from ``status_events`` in memory when the caller has
        prefetched them, which the board does.
        """
        for event in self.status_events.all():
            if event.to_status == VisitStatus.CONFIRMED:
                return event
        return None

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


# ── Doctor availability ───────────────────────────────────────────────────────
#
# Slots used to come purely from the clinic-wide consulting hours in
# config/clinic.py. That is fine for one doctor working fixed hours, but a real
# clinic has doctors with different days, split morning and evening sittings,
# public holidays and leave. The three models below layer over that default,
# most specific first:
#
#   1. DoctorLeave      — this doctor is away, all day or for part of it
#   2. ScheduleOverride — this doctor works different hours on this one date
#   3. DoctorSchedule   — this doctor's ordinary week
#   4. ClinicHoliday    — the clinic is shut, for everyone
#
# Nothing here stores individual slots. Slots stay derived, so there is still no
# table of empty rows to keep in step with reality.


WEEKDAYS = [
    (0, "Monday"), (1, "Tuesday"), (2, "Wednesday"), (3, "Thursday"),
    (4, "Friday"), (5, "Saturday"), (6, "Sunday"),
]


class Cabin(models.Model):
    """
    A consulting room (KAN-22).

    Clinic-wide rather than belonging to a doctor: the whole point of the daily
    view is that a room is a shared resource two doctors can be double-booked
    into, which is only a question worth asking if the room exists once.

    Retired rather than deleted, for the same reason as a specialisation — a
    cabin that has been used is part of the clinic's history, and tidying a
    dropdown must not rewrite it. The foreign keys below are PROTECT, so the
    database refuses the alternative anyway.
    """

    name = models.CharField(max_length=60)
    is_active = models.BooleanField(
        default=True,
        help_text="Unticked, this stays on existing entries but is not offered "
                  "for new ones.",
    )
    note = models.CharField(max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="cabins_added",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            # Case-insensitive, so "Cabin 1" and "cabin 1" cannot both exist and
            # leave reception guessing which column a doctor is actually in.
            models.UniqueConstraint(
                Lower("name"), name="cabin_name_unique_ci",
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name = " ".join((self.name or "").split())
        super().save(*args, **kwargs)


class ClinicHoliday(models.Model):
    """A day the clinic is closed to everyone."""

    date = models.DateField(unique=True)
    name = models.CharField(max_length=120, help_text="Diwali, Republic Day, and so on.")
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date"]
        verbose_name = "clinic holiday"

    def __str__(self):
        return f"{self.name} ({self.date:%d %b %Y})"


class DoctorSchedule(models.Model):
    """
    One sitting in a doctor's ordinary week.

    A doctor with a morning and an evening clinic on the same day has two rows
    for that weekday. A doctor with no rows at all falls back to the clinic-wide
    consulting hours, so this table is optional until somebody needs it.
    """

    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="schedule_days",
        limit_choices_to={"role": "DOCTOR"},
    )
    weekday = models.PositiveSmallIntegerField(choices=WEEKDAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()
    slot_minutes = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="Leave empty to use the clinic's standard slot length.",
    )
    #: Nullable because the clinic ran without cabins before KAN-22, and the
    #: rows already entered are still true — they simply do not say which room.
    cabin = models.ForeignKey(
        Cabin, on_delete=models.PROTECT, null=True, blank=True,
        related_name="weekly_sittings",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["doctor", "weekday", "start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "weekday", "start_time"],
                name="one_sitting_per_start_time",
            ),
            models.CheckConstraint(
                condition=Q(end_time__gt=F("start_time")),
                name="schedule_end_after_start",
            ),
        ]

    def __str__(self):
        where = f" · {self.cabin.name}" if self.cabin_id else ""
        return (f"{self.doctor.display_name} · {self.get_weekday_display()} "
                f"{self.start_time:%H:%M}–{self.end_time:%H:%M}{where}")


class ScheduleOverride(models.Model):
    """
    Different hours for one doctor on one date.

    Used when a doctor runs an extra evening clinic, or starts late. To mark
    absence use :class:`DoctorLeave` instead — an override with no hours would
    be an ambiguous way of saying the same thing.
    """

    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="schedule_overrides",
        limit_choices_to={"role": "DOCTOR"},
    )
    date = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    slot_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    cabin = models.ForeignKey(
        Cabin, on_delete=models.PROTECT, null=True, blank=True,
        related_name="one_off_sittings",
    )
    note = models.CharField(max_length=200, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="schedule_overrides_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["doctor", "date", "start_time"],
                name="one_override_per_start_time",
            ),
            models.CheckConstraint(
                condition=Q(end_time__gt=F("start_time")),
                name="override_end_after_start",
            ),
        ]

    def __str__(self):
        where = f" · {self.cabin.name}" if self.cabin_id else ""
        return (f"{self.doctor.display_name} · {self.date:%d %b} "
                f"{self.start_time:%H:%M}–{self.end_time:%H:%M}{where}")


class DoctorLeave(models.Model):
    """
    A doctor is away — the whole day, or a stretch of it.

    Booking against leave is prevented, but leave taken *after* patients have
    already been confirmed is the case that actually matters: those patients
    have to be rung and moved. :meth:`affected_visits` is what the receptionist
    is shown so that call list is never missed.
    """

    doctor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="leave",
        limit_choices_to={"role": "DOCTOR"},
    )
    date = models.DateField(db_index=True)
    # Empty times mean the whole day.
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    reason = models.CharField(max_length=200, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="leave_recorded",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "doctor leave"
        verbose_name_plural = "doctor leave"
        indexes = [models.Index(fields=["doctor", "date"])]

    def __str__(self):
        span = "all day" if self.whole_day else f"{self.start_time:%H:%M}–{self.end_time:%H:%M}"
        return f"{self.doctor.display_name} away {self.date:%d %b %Y} ({span})"

    def clean(self):
        if bool(self.start_time) != bool(self.end_time):
            raise ValidationError(
                "Give both a start and an end time, or neither for a whole day."
            )
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError("The end time must be after the start time.")

    @property
    def whole_day(self):
        return self.start_time is None or self.end_time is None

    def covers(self, start, end):
        """Does this leave overlap the window ``start``–``end`` (aware datetimes)?"""
        local_start = timezone.localtime(start)
        local_end = timezone.localtime(end)
        if local_start.date() != self.date and local_end.date() != self.date:
            return False
        if self.whole_day:
            return True
        return local_start.time() < self.end_time and self.start_time < local_end.time()

    def affected_visits(self):
        """
        Bookings this leave strands — the patients who must be rung.

        Cancelled and completed visits are excluded: there is nobody left to
        ring about those.
        """
        candidates = (
            Visit.objects.filter(doctor=self.doctor, scheduled_start__date=self.date)
            .active()
            .select_related("patient", "doctor")
            .order_by("scheduled_start")
        )
        return [v for v in candidates if self.covers(v.scheduled_start, v.scheduled_end)]


class DaySignOff(models.Model):
    """
    The receptionist's declaration that a clinic day is finished (KAN-48, KAN-49).

    One row per date, and its existence is the whole point: KAN-49 asks for an
    alert "only if the previous day's sign-off wasn't sent", which needs
    somewhere to record that it was. Deriving it from the visits does not work —
    a day on which nobody was billed and a day nobody closed look identical from
    the visit table, and only one of them is a problem.

    The counts are stored rather than recomputed. They are what was true when
    the day was signed off, and a correction made afterwards should not silently
    rewrite a figure somebody has already sent to the accountant.
    """

    date = models.DateField(unique=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="day_sign_offs",
    )
    sent_to = models.CharField(max_length=320, blank=True)

    billed_count = models.PositiveIntegerField(default=0)
    cancelled_count = models.PositiveIntegerField(default=0)
    no_show_count = models.PositiveIntegerField(default=0)
    collected = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    #: Set when the report could not be emailed. The day is still signed off —
    #: refusing to close a clinic day because a mail server was down would
    #: block the next morning's work over something nobody at the desk can fix.
    delivery_error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "day sign-off"

    def __str__(self):
        return f"Sign-off for {self.date:%d %b %Y}"
