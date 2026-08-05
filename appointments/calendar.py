"""
The availability calendar (KAN-22).

What a doctor works on a given date is not stored anywhere — it is worked out
from the same layered rules that :mod:`appointments.scheduling` uses to produce
bookable slots:

  1. **Clinic holiday** — nobody works.
  2. **Schedule override** — this doctor's hours for this one date, replacing
     their ordinary week entirely.
  3. **Weekly schedule** — this doctor's ordinary week.
  4. **Clinic default** — the consulting hours in ``config/clinic.py``, for a
     doctor who has no rows of their own.

This module expands those rules into dated **entries** so a month or a day can
be drawn, and it is deliberately the *only* expansion in the system. Conflict
detection asks it the same question the calendar asks — "who is actually in
which cabin at this time on this date" — because a conflict check that reasons
about the raw rows instead gets the override-replaces-weekly rule subtly wrong
and lets a real double-booking through.

Rule 4 produces entries with no cabin. That is not an oversight: those hours are
genuinely bookable today, and drawing them into a cabin column would be
inventing an occupancy nobody recorded. They appear under "No cabin set", which
is also the honest answer to why a doctor with no rows still has free slots.
"""

from calendar import Calendar
from dataclasses import dataclass
from datetime import date as date_cls, time, timedelta

from django.conf import settings
from django.utils import timezone

from .models import (
    Cabin, ClinicHoliday, DoctorLeave, DoctorSchedule, ScheduleOverride,
)

#: How far ahead a weekly pattern is checked for clashes with one-off hours.
#: The booking horizon, because beyond it nothing can be booked and a clash
#: there is not yet a problem anybody can act on.
CONFLICT_HORIZON_DAYS = 90

#: Sources an entry can come from, most specific first.
SOURCE_OVERRIDE = "override"
SOURCE_WEEKLY = "weekly"
SOURCE_DEFAULT = "clinic-default"


def _parse_time(value):
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute or 0))


@dataclass(frozen=True)
class Entry:
    """One doctor in one cabin for one stretch of one date."""

    date: date_cls
    doctor: object
    cabin: object              # Cabin or None
    start: time
    end: time
    source: str
    pk: int | None             # the row this came from; None for clinic default
    note: str = ""
    away: bool = False         # covered by leave — recorded, but not happening

    @property
    def is_recurring(self):
        return self.source == SOURCE_WEEKLY

    @property
    def is_editable(self):
        """Clinic-default hours are not a row, so there is nothing to edit."""
        return self.pk is not None

    @property
    def kind(self):
        """What kind of thing to delete, matching the URL's ``kind`` segment."""
        return "schedule" if self.source == SOURCE_WEEKLY else "override"

    @property
    def label(self):
        return f"{self.start:%H:%M}–{self.end:%H:%M}"

    @property
    def cabin_name(self):
        return self.cabin.name if self.cabin is not None else "No cabin set"

    def overlaps(self, start, end):
        """
        Does this entry clash with the window ``start``–``end``?

        Touching boundaries do not clash: a doctor finishing at 12:00 and
        another starting at 12:00 is how a cabin is shared, not a mistake
        (KAN-22 T-17).
        """
        return self.start < end and start < self.end


class Schedule:
    """
    Every availability rule, loaded once, answering "what happens on this date".

    Built once per request rather than queried per day, because a month view
    asks the same four tables thirty-odd times and a conflict check on a weekly
    pattern asks them another thirteen.
    """

    def __init__(self, doctors, *, start, end):
        self.doctors = list(doctors)
        self.start = start
        self.end = end
        doctor_ids = [d.pk for d in self.doctors]

        self.holidays = {
            holiday.date: holiday
            for holiday in ClinicHoliday.objects.filter(date__range=(start, end))
        }

        self._overrides = {}
        for row in ScheduleOverride.objects.filter(
            doctor_id__in=doctor_ids, date__range=(start, end)
        ).select_related("doctor", "cabin").order_by("start_time"):
            self._overrides.setdefault((row.doctor_id, row.date), []).append(row)

        self._weekly = {}
        for row in DoctorSchedule.objects.filter(
            doctor_id__in=doctor_ids, is_active=True
        ).select_related("doctor", "cabin").order_by("start_time"):
            self._weekly.setdefault((row.doctor_id, row.weekday), []).append(row)

        #: A doctor with no weekly rows at all falls back to clinic hours. One
        #: with rows on Mondays only does *not* work Tuesdays — the absence is
        #: the statement.
        self._has_weekly = {
            doctor_id for (doctor_id, _weekday) in self._weekly
        }

        self.leave = {}
        for row in DoctorLeave.objects.filter(
            doctor_id__in=doctor_ids, date__range=(start, end)
        ).select_related("doctor"):
            self.leave.setdefault(row.date, []).append(row)

        self._by_id = {d.pk: d for d in self.doctors}

    # ── Expansion ────────────────────────────────────────────────────────────

    def is_holiday(self, day):
        return day in self.holidays

    def entries_on(self, day):
        """Every doctor's effective working hours for ``day``."""
        if self.is_holiday(day):
            return []

        entries = []
        for doctor in self.doctors:
            entries.extend(self._entries_for(doctor, day))
        return sorted(entries, key=lambda e: (e.start, e.cabin_name, str(e.doctor)))

    def _entries_for(self, doctor, day):
        absences = [
            row for row in self.leave.get(day, []) if row.doctor_id == doctor.pk
        ]

        def away(start, end):
            for row in absences:
                if row.whole_day:
                    return True
                if start < row.end_time and row.start_time < end:
                    return True
            return False

        overrides = self._overrides.get((doctor.pk, day), [])
        if overrides:
            return [
                Entry(day, doctor, row.cabin, row.start_time, row.end_time,
                      SOURCE_OVERRIDE, row.pk, row.note, away(row.start_time, row.end_time))
                for row in overrides
            ]

        if doctor.pk in self._has_weekly:
            return [
                Entry(day, doctor, row.cabin, row.start_time, row.end_time,
                      SOURCE_WEEKLY, row.pk, "", away(row.start_time, row.end_time))
                for row in self._weekly.get((doctor.pk, day.weekday()), [])
            ]

        if day.weekday() not in settings.CLINIC.WORKING_DAYS:
            return []
        start = _parse_time(settings.CLINIC.CONSULTING_START)
        end = _parse_time(settings.CLINIC.CONSULTING_END)
        return [
            Entry(day, doctor, None, start, end, SOURCE_DEFAULT, None, "",
                  away(start, end))
        ]


# ── Conflict detection (FR-17, FR-18) ────────────────────────────────────────

@dataclass(frozen=True)
class Conflict:
    day: date_cls
    entry: Entry
    reason: str                # "cabin" or "doctor"

    def __str__(self):
        if self.reason == "cabin":
            return (
                f"{self.entry.cabin_name} is already taken by "
                f"{self.entry.doctor.display_name} on {self.day:%a %d %b}, "
                f"{self.entry.label}."
            )
        return (
            f"{self.entry.doctor.display_name} is already in "
            f"{self.entry.cabin_name} on {self.day:%a %d %b}, {self.entry.label}."
        )


def _dates_to_check(*, date=None, weekday=None, horizon=CONFLICT_HORIZON_DAYS):
    """
    The dates a proposed entry would actually occupy.

    A one-off entry occupies one date. A weekly pattern occupies every matching
    weekday, which is unbounded — so it is checked across the booking horizon,
    beyond which nothing can be booked and a clash is not yet anybody's problem.
    """
    if date is not None:
        return [date]

    today = timezone.localdate()
    days = []
    for offset in range(horizon + 1):
        day = today + timedelta(days=offset)
        if day.weekday() == weekday:
            days.append(day)
    return days


def find_conflicts(*, kind, doctor, cabin, start, end, date=None, weekday=None,
                   exclude_pk=None, doctors=None):
    """
    Every clash a proposed set of working hours would cause.

    Two rules, both about a person or a room being in one place at a time:

    * **FR-17** — two doctors cannot occupy the same cabin at overlapping times.
    * **FR-18** — one doctor cannot be in two cabins at once.

    Checked against the *effective* schedule rather than the raw rows, because
    the two differ in ways that decide the answer:

    * An override replaces that doctor's ordinary week for its date. So a new
      override does not clash with the weekly rows it is about to supersede —
      and a new weekly row does not apply at all on a date the doctor already
      has an override for, so it cannot take a cabin there either.
    * Clinic-default hours are a fallback for a doctor with no rows, not a
      commitment anybody made. They never count as occupying anything, or the
      very first row entered for a doctor would clash with their own default.

    ``exclude_pk`` drops the row being edited, which otherwise clashes with
    itself.
    """
    if end <= start:
        return []

    days = _dates_to_check(date=date, weekday=weekday)
    if not days:
        return []

    from accounts.models import Role, User

    if doctors is None:
        doctors = User.objects.filter(role=Role.DOCTOR, is_active=True)
    schedule = Schedule(doctors, start=min(days), end=max(days))

    proposed_source = SOURCE_WEEKLY if kind == "schedule" else SOURCE_OVERRIDE

    conflicts = []
    for day in days:
        mine = [e for e in schedule.entries_on(day) if e.doctor.pk == doctor.pk]

        # A weekly row simply does not exist on a date this doctor has an
        # override for, so nothing it might have clashed with is its problem.
        if proposed_source == SOURCE_WEEKLY and any(
            e.source == SOURCE_OVERRIDE for e in mine
        ):
            continue

        for entry in schedule.entries_on(day):
            if entry.pk == exclude_pk and entry.source == proposed_source:
                continue
            if entry.source == SOURCE_DEFAULT:
                continue
            if proposed_source == SOURCE_OVERRIDE \
                    and entry.source == SOURCE_WEEKLY \
                    and entry.doctor.pk == doctor.pk:
                # About to be superseded by the override being added.
                continue
            if not entry.overlaps(start, end):
                continue

            # Leave is deliberately *not* treated as freeing the room. Leave is
            # temporary and the recorded hours outlive it, so a cabin handed to
            # somebody else for a day the doctor happens to be away becomes a
            # clash the moment that leave is cancelled. FR-15's answer to
            # "somebody else needs this room" is to delete the entry, not to
            # record an absence and let the room be taken twice.
            if entry.doctor.pk == doctor.pk:
                conflicts.append(Conflict(day, entry, "doctor"))
            elif cabin is not None and entry.cabin is not None \
                    and entry.cabin.pk == cabin.pk:
                conflicts.append(Conflict(day, entry, "cabin"))

    return conflicts


# ── Drawing a month ──────────────────────────────────────────────────────────

def month_range(anchor):
    """First and last date shown by a month grid containing ``anchor``."""
    grid = Calendar(firstweekday=0).monthdatescalendar(anchor.year, anchor.month)
    return grid[0][0], grid[-1][-1]


def month_weeks(anchor, schedule, *, per_day=3):
    """
    A month as rows of seven days, each carrying the entries that fall on it.

    ``per_day`` caps what is drawn; the remainder is reported as a count so a
    busy month stays legible rather than growing an unreadable cell (KAN-22 T-8).

    Clinic-default hours are counted, not drawn. Every doctor without schedule
    rows of their own falls back to them *every working day*, so drawing them as
    entries filled every cell in the month with identical chips and buried the
    handful of real sittings underneath — which is the one thing the month view
    exists to show. Dropping them silently would be worse: those hours are
    genuinely bookable, and a month claiming nobody works would disagree with
    the booking form. So they get one summary line per day, and the day view
    lists them properly under "No cabin set".
    """
    weeks = []
    for week in Calendar(firstweekday=0).monthdatescalendar(anchor.year, anchor.month):
        row = []
        for day in week:
            entries = schedule.entries_on(day)
            recorded = [e for e in entries if e.source != SOURCE_DEFAULT]
            on_default = [e for e in entries if e.source == SOURCE_DEFAULT]
            row.append({
                "date": day,
                "in_month": day.month == anchor.month,
                "is_today": day == timezone.localdate(),
                "holiday": schedule.holidays.get(day),
                "leave": schedule.leave.get(day, []),
                "entries": recorded[:per_day],
                "more": max(0, len(recorded) - per_day),
                "on_default": len(on_default),
            })
        weeks.append(row)
    return weeks


def day_columns(day, schedule, cabins):
    """
    A single date, as one column per cabin plus one for hours with no cabin.

    Cabin-oriented because the question the daily view exists to answer is
    "which room is free", and that cannot be read off a list ordered by doctor.
    """
    entries = schedule.entries_on(day)
    columns = [
        {"cabin": cabin,
         "name": cabin.name,
         "entries": [e for e in entries if e.cabin is not None and e.cabin.pk == cabin.pk]}
        for cabin in cabins
    ]
    unassigned = [e for e in entries if e.cabin is None]
    if unassigned:
        columns.append({"cabin": None, "name": "No cabin set", "entries": unassigned})
    return columns


def active_cabins():
    return list(Cabin.objects.filter(is_active=True))
