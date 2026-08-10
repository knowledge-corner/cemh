"""
The availability calendar (KAN-22).

What a doctor works on a given date is not stored anywhere separate — it is
worked out from the same layered rules that :mod:`appointments.scheduling`
uses to produce bookable slots:

  1. **Clinic holiday** — nobody works.
  2. **Schedule entry** — this doctor's hours for this one date. There is no
     other tier below this: a doctor with no entry for a date simply is not
     drawn on it, rather than falling back to any clinic-wide default hours.

This module expands those rules into dated **entries** so a month or a day can
be drawn, and it is deliberately the *only* expansion in the system. Conflict
detection and cabin allocation both ask it the same question the calendar
asks — "who is actually in which cabin at this time on this date".
"""

from calendar import Calendar
from dataclasses import dataclass
from datetime import date as date_cls, time

from django.utils import timezone

from .models import Cabin, ClinicHoliday, DoctorSchedule


@dataclass(frozen=True)
class Entry:
    """One doctor in one cabin for one stretch of one date."""

    date: date_cls
    doctor: object
    cabin: object              # Cabin or None — see day_columns()
    start: time
    end: time
    pk: int
    note: str = ""
    series_id: object = None   # groups the rows one recurring booking created

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
    asks the same tables thirty-odd times and a conflict check on a recurring
    booking asks them another dozen.
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

        self._entries = {}
        for row in DoctorSchedule.objects.filter(
            doctor_id__in=doctor_ids, date__range=(start, end)
        ).select_related("doctor", "cabin").order_by("start_time"):
            self._entries.setdefault((row.doctor_id, row.date), []).append(row)

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
        rows = self._entries.get((doctor.pk, day), [])
        return [
            Entry(day, doctor, row.cabin, row.start_time, row.end_time,
                  row.pk, row.note, row.series_id)
            for row in rows
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


def find_conflicts(*, doctor, cabin, start, end, dates, exclude_pk=None, doctors=None):
    """
    Every clash a proposed set of hours would cause across ``dates``.

    Two rules, both about a person or a room being in one place at a time:

    * **FR-17** — two doctors cannot occupy the same cabin at overlapping times.
    * **FR-18** — one doctor cannot be in two cabins at once.

    Pass ``cabin=None`` to check only for a doctor already busy (FR-18) without
    naming a room — what cabin allocation uses before it has picked one.
    ``exclude_pk`` drops the row being edited, which otherwise clashes with
    itself.
    """
    if end <= start or not dates:
        return []

    from accounts.models import Role, User

    if doctors is None:
        doctors = User.objects.filter(role=Role.DOCTOR, is_active=True)
    schedule = Schedule(doctors, start=min(dates), end=max(dates))

    conflicts = []
    for day in dates:
        for entry in schedule.entries_on(day):
            if entry.pk == exclude_pk:
                continue
            if not entry.overlaps(start, end):
                continue

            if entry.doctor.pk == doctor.pk:
                conflicts.append(Conflict(day, entry, "doctor"))
            elif cabin is not None and entry.cabin is not None \
                    and entry.cabin.pk == cabin.pk:
                conflicts.append(Conflict(day, entry, "cabin"))

    return conflicts


# ── Dynamic cabin allocation ──────────────────────────────────────────────────

def allocate_cabins(*, doctor, dates, start, end, exclude_pk=None):
    """
    Which cabin each of ``dates`` should get, without anybody choosing one.

    Tries every active cabin, in the same order the cabin dropdown always
    listed them, and prefers **one cabin the whole booking can share** — a
    doctor's month of Monday/Wednesday/Friday clinics scattered across three
    different rooms would be a room number reception has to look up each
    time, not a helpful bit of automation. Only when no single cabin is free
    for every date does each date get whatever is free for it alone, and a
    date with nothing free at all is reported rather than guessed at.

    Returns ``(by_date, unavailable)``: a ``{date: Cabin}`` mapping for every
    date that got one, and the sorted list of dates that did not.
    """
    cabins = list(Cabin.objects.filter(is_active=True).order_by("name"))
    if not cabins:
        return {}, sorted(dates)

    def free_for(cabin, day):
        clashes = find_conflicts(
            doctor=doctor, cabin=cabin, start=start, end=end,
            dates=[day], exclude_pk=exclude_pk,
        )
        return not any(c.reason == "cabin" for c in clashes)

    for cabin in cabins:
        if all(free_for(cabin, day) for day in dates):
            return {day: cabin for day in dates}, []

    by_date = {}
    unavailable = []
    for day in dates:
        chosen = next((c for c in cabins if free_for(c, day)), None)
        if chosen is None:
            unavailable.append(day)
        else:
            by_date[day] = chosen
    return by_date, sorted(unavailable)


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
    """
    weeks = []
    for week in Calendar(firstweekday=0).monthdatescalendar(anchor.year, anchor.month):
        row = []
        for day in week:
            entries = schedule.entries_on(day)
            row.append({
                "date": day,
                "in_month": day.month == anchor.month,
                "is_today": day == timezone.localdate(),
                "holiday": schedule.holidays.get(day),
                "entries": entries[:per_day],
                "more": max(0, len(entries) - per_day),
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
