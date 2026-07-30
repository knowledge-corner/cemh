"""
Working out which appointment slots are free.

Slots are derived from the clinic's consulting hours rather than stored, so
there is no table of empty rows to keep in step with reality. A slot is offered
when no active visit already overlaps it.

The database still has the final say: the exclusion constraint on ``Visit``
rejects an overlapping booking even if two people are offered the same slot at
the same instant and both accept it. This module makes that race rare; the
constraint makes it harmless.
"""

from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from .models import Visit


def _parse_time(value):
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute or 0))


def is_working_day(day):
    return day.weekday() in settings.CLINIC.WORKING_DAYS


def slot_length():
    return timedelta(minutes=settings.CLINIC.SLOT_MINUTES)


def booking_window():
    """First and last date a patient may book."""
    today = timezone.localdate()
    return today, today + timedelta(days=settings.CLINIC.BOOKING_HORIZON_DAYS)


def day_slots(day):
    """Every slot the clinic could run on ``day``, free or not."""
    if not is_working_day(day):
        return []

    tz = timezone.get_current_timezone()
    start = timezone.make_aware(
        datetime.combine(day, _parse_time(settings.CLINIC.CONSULTING_START)), tz
    )
    end = timezone.make_aware(
        datetime.combine(day, _parse_time(settings.CLINIC.CONSULTING_END)), tz
    )

    length = slot_length()
    slots = []
    cursor = start
    while cursor + length <= end:
        slots.append((cursor, cursor + length))
        cursor += length
    return slots


def available_slots(doctor, day, *, include_past=False):
    """
    Free slots for one doctor on one day.

    Past slots are dropped unless ``include_past`` is set — a receptionist
    recording a walk-in that already happened is the one case where they are
    wanted.
    """
    slots = day_slots(day)
    if not slots:
        return []

    taken = Visit.objects.filter(
        doctor=doctor,
        scheduled_start__date=day,
    ).active().values_list("scheduled_start", "scheduled_end")

    now = timezone.now()
    free = []
    for start, end in slots:
        if not include_past and start <= now:
            continue
        if any(start < busy_end and busy_start < end for busy_start, busy_end in taken):
            continue
        free.append((start, end))
    return free


def next_available(doctor, *, days=14):
    """The soonest free slot, looking a fortnight ahead by default."""
    day = timezone.localdate()
    for _ in range(days):
        slots = available_slots(doctor, day)
        if slots:
            return slots[0]
        day += timedelta(days=1)
    return None
