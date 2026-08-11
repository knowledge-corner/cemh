"""
Working out which appointment slots are free.

Slots are derived rather than stored, so there is no table of empty rows to keep
in step with reality. What a day looks like is decided by the most specific rule
that applies, in this order:

  1. **Clinic holiday** — nobody works, so there are no slots at all.
  2. **Schedule entry** — this doctor's hours for this one date. There is no
     other tier below this one: a doctor with no entry for a date simply has
     no slots on it, rather than falling back to any clinic-wide notion of
     "normal hours". Availability is read off the calendar, not assumed.

Any slot already held by an active visit is then subtracted.

The database still has the final say: the exclusion constraint on ``Visit``
rejects an overlapping booking even if two people are offered the same slot at
the same instant and both accept. This module makes that race rare; the
constraint makes it harmless.
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from .models import ClinicHoliday, DoctorSchedule, Visit


def _aware(day, at):
    return timezone.make_aware(
        datetime.combine(day, at), timezone.get_current_timezone()
    )


def default_slot_minutes():
    return settings.CLINIC.SLOT_MINUTES


def slot_length():
    return timedelta(minutes=default_slot_minutes())


def is_holiday(day):
    return ClinicHoliday.objects.filter(date=day).exists()


def is_working_day(day, doctor=None):
    """
    Does the clinic run on ``day`` — and, if a doctor is named, do they work it?

    Without a doctor, only a holiday closes the clinic — there is no other
    clinic-wide notion of "days it runs" any more. With one, the answer comes
    straight off the calendar: a doctor works a date only if they have a
    schedule entry for it, never because of what day of the week it is.
    """
    if is_holiday(day):
        return False
    if doctor is None:
        return True
    return DoctorSchedule.objects.filter(doctor=doctor, date=day).exists()


def booking_window():
    """First and last date an appointment may be booked for."""
    today = timezone.localdate()
    return today, today + timedelta(days=settings.CLINIC.BOOKING_HORIZON_DAYS)


def sittings_for(day, doctor=None):
    """
    The stretches of time worked on ``day``, as ``(start, end, slot_minutes)``.

    A doctor with a morning and an evening clinic returns two. Nothing at all
    if they have no schedule entry for this date — there is no clinic-default
    stretch to fall back to.
    """
    if is_holiday(day) or doctor is None:
        return []

    entries = DoctorSchedule.objects.filter(doctor=doctor, date=day)
    return [
        (e.start_time, e.end_time, e.slot_minutes or default_slot_minutes())
        for e in entries.order_by("start_time")
    ]


def day_slots(day, doctor=None):
    """Every slot that could run on ``day``, free or not."""
    slots = []
    for start_at, end_at, minutes in sittings_for(day, doctor):
        length = timedelta(minutes=minutes)
        cursor = _aware(day, start_at)
        finish = _aware(day, end_at)
        while cursor + length <= finish:
            slots.append((cursor, cursor + length))
            cursor += length
    return sorted(slots)


#: Why a slot cannot be booked. ``None`` means it can.
SLOT_FREE = "free"
SLOT_BOOKED = "booked"
SLOT_PAST = "past"


def slot_grid(doctor, day):
    """
    Every slot the doctor works that day, each with why it can or cannot be
    taken.

    Separate from :func:`available_slots`, which answers "what may be booked"
    and is what validation asks. This answers "what should the receptionist
    see", and the difference is the point: a slot that is already taken used to
    vanish from the form, so a 10:30 gap between 10:15 and 10:45 looked
    identical to a doctor who simply does not work at 10:30. Showing it as
    booked is the difference between "no" and "not any more".

    Booked wins over past when a slot is both. That a patient was seen is the
    more useful fact; neither can be booked now anyway.
    """
    slots = day_slots(day, doctor)
    if not slots:
        return []

    taken = Visit.objects.filter(
        doctor=doctor, scheduled_start__date=day,
    ).active().values_list("scheduled_start", "scheduled_end")

    now = timezone.now()

    grid = []
    for start, end in slots:
        if any(start < busy_end and busy_start < end for busy_start, busy_end in taken):
            state = SLOT_BOOKED
        elif start <= now:
            state = SLOT_PAST
        else:
            state = SLOT_FREE
        grid.append({"start": start, "end": end, "state": state})
    return grid


def available_slots(doctor, day, *, include_past=False):
    """
    Free slots for one doctor on one day.

    Past slots are dropped unless ``include_past`` is set. Booking a time that
    has already gone is a mis-key, not an intention.
    """
    return [
        (row["start"], row["end"])
        for row in slot_grid(doctor, day)
        if row["state"] == SLOT_FREE
        or (include_past and row["state"] == SLOT_PAST)
    ]


def next_available(doctor, *, days=14):
    """The soonest free slot, looking a fortnight ahead by default."""
    day = timezone.localdate()
    for _ in range(days):
        slots = available_slots(doctor, day)
        if slots:
            return slots[0]
        day += timedelta(days=1)
    return None


def next_free_moment(doctor, *, after=None):
    """
    The soonest a walk-in for ``doctor`` can be marked seen.

    Checked only against the doctor's other active visits, never against a
    schedule entry — KAN task #22's walk-in is reception seeing the doctor
    actually standing there right now, not a question the calendar gets a
    veto over. Reception manages the doctor's real timing by hand; this
    exists only to stop two walk-ins landing on the exact same moment and
    tripping the database's no-double-booking constraint.
    """
    cursor = after or timezone.now()
    length = slot_length()
    busy = list(Visit.objects.filter(doctor=doctor).active().values_list(
        "scheduled_start", "scheduled_end"
    ))
    while any(cursor < end and start < cursor + length for start, end in busy):
        cursor += length
    return cursor
