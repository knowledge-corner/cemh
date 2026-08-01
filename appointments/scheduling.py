"""
Working out which appointment slots are free.

Slots are derived rather than stored, so there is no table of empty rows to keep
in step with reality. What a day looks like is decided by the most specific rule
that applies, in this order:

  1. **Clinic holiday** — nobody works, so there are no slots at all.
  2. **Schedule override** — this doctor's hours for this one date.
  3. **Weekly schedule** — this doctor's ordinary week.
  4. **Clinic default** — the consulting hours in ``config/clinic.py``, used
     when a doctor has no schedule of their own. A single-doctor clinic never
     has to fill in any of the tables above.

Leave is then subtracted, and finally any slot already held by an active visit.

The database still has the final say: the exclusion constraint on ``Visit``
rejects an overlapping booking even if two people are offered the same slot at
the same instant and both accept. This module makes that race rare; the
constraint makes it harmless.
"""

from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from .models import ClinicHoliday, DoctorLeave, DoctorSchedule, ScheduleOverride, Visit


def _parse_time(value):
    hour, _, minute = value.partition(":")
    return time(int(hour), int(minute or 0))


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

    Usable without a doctor so the booking form can rule a date out before a
    doctor has been chosen.
    """
    if is_holiday(day):
        return False
    if doctor is None:
        return day.weekday() in settings.CLINIC.WORKING_DAYS

    if ScheduleOverride.objects.filter(doctor=doctor, date=day).exists():
        return True
    if DoctorSchedule.objects.filter(doctor=doctor, is_active=True).exists():
        return DoctorSchedule.objects.filter(
            doctor=doctor, weekday=day.weekday(), is_active=True
        ).exists()
    return day.weekday() in settings.CLINIC.WORKING_DAYS


def booking_window():
    """First and last date an appointment may be booked for."""
    today = timezone.localdate()
    return today, today + timedelta(days=settings.CLINIC.BOOKING_HORIZON_DAYS)


def sittings_for(day, doctor=None):
    """
    The stretches of time worked on ``day``, as ``(start, end, slot_minutes)``.

    A doctor with a morning and an evening clinic returns two.
    """
    if is_holiday(day):
        return []

    if doctor is not None:
        overrides = ScheduleOverride.objects.filter(doctor=doctor, date=day)
        if overrides.exists():
            return [
                (o.start_time, o.end_time, o.slot_minutes or default_slot_minutes())
                for o in overrides.order_by("start_time")
            ]

        if DoctorSchedule.objects.filter(doctor=doctor, is_active=True).exists():
            rows = DoctorSchedule.objects.filter(
                doctor=doctor, weekday=day.weekday(), is_active=True
            ).order_by("start_time")
            return [
                (r.start_time, r.end_time, r.slot_minutes or default_slot_minutes())
                for r in rows
            ]

    if day.weekday() not in settings.CLINIC.WORKING_DAYS:
        return []
    return [(
        _parse_time(settings.CLINIC.CONSULTING_START),
        _parse_time(settings.CLINIC.CONSULTING_END),
        default_slot_minutes(),
    )]


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


def leave_for(doctor, day):
    return list(DoctorLeave.objects.filter(doctor=doctor, date=day))


def available_slots(doctor, day, *, include_past=False):
    """
    Free slots for one doctor on one day.

    Past slots are dropped unless ``include_past`` is set. The booking form
    deliberately does not set it: a booking taken for a time that has already
    gone is a mis-key, not an intention.
    """
    slots = day_slots(day, doctor)
    if not slots:
        return []

    taken = Visit.objects.filter(
        doctor=doctor, scheduled_start__date=day,
    ).active().values_list("scheduled_start", "scheduled_end")

    away = leave_for(doctor, day)
    now = timezone.now()

    free = []
    for start, end in slots:
        if not include_past and start <= now:
            continue
        if any(start < busy_end and busy_start < end for busy_start, busy_end in taken):
            continue
        if any(absence.covers(start, end) for absence in away):
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
