import uuid
from datetime import timedelta

from django.db import migrations
from django.utils import timezone

#: How far ahead an indefinite weekly pattern is materialized into dated rows.
#: There is no such thing as "forever" once every row names an actual date, so
#: a generous but bounded horizon stands in for it — a clinic still running the
#: same week a year from now adds a fresh recurring booking before this one
#: runs out, exactly as the new form already expects every booking to have an
#: end date.
HORIZON_DAYS = 365


def materialize(apps, schema_editor):
    DoctorSchedule = apps.get_model("appointments", "DoctorSchedule")
    ScheduleOverride = apps.get_model("appointments", "ScheduleOverride")

    today = timezone.localdate()
    horizon = today + timedelta(days=HORIZON_DAYS)

    # An override already wins over the weekly pattern for its date under the
    # app's own effective-schedule rule, so a date that already has one must
    # not also get a materialized weekly row alongside it.
    overridden_dates = set(
        ScheduleOverride.objects.values_list("doctor_id", "date")
    )

    for sitting in DoctorSchedule.objects.filter(is_active=True):
        series_id = uuid.uuid4()
        day = today
        while day.weekday() != sitting.weekday:
            day += timedelta(days=1)
        while day <= horizon:
            if (sitting.doctor_id, day) not in overridden_dates:
                ScheduleOverride.objects.create(
                    doctor_id=sitting.doctor_id,
                    date=day,
                    start_time=sitting.start_time,
                    end_time=sitting.end_time,
                    slot_minutes=sitting.slot_minutes,
                    cabin_id=sitting.cabin_id,
                    series_id=series_id,
                    note="Carried over from a weekly pattern",
                )
            day += timedelta(days=7)


class Migration(migrations.Migration):
    """
    One-time cutover: every indefinite weekly DoctorSchedule row becomes
    concrete dated ScheduleOverride rows for the year ahead, tagged as one
    series. Run once, when the two-table design retires in favour of one
    table where every row names a date — see 0008 and 0009.
    """

    dependencies = [
        ("appointments", "0006_scheduleoverride_series_id"),
    ]

    operations = [
        migrations.RunPython(materialize, migrations.RunPython.noop),
    ]
