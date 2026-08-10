from django.db import migrations


class Migration(migrations.Migration):
    """
    The indefinite weekly pattern is gone — 0007 already turned every active
    row into dated ScheduleOverride rows, so nothing here is losing data that
    0009 doesn't already carry forward under its new name.
    """

    dependencies = [
        ("appointments", "0007_materialize_weekly_schedules"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="doctorschedule",
            name="one_sitting_per_start_time",
        ),
        migrations.RemoveConstraint(
            model_name="doctorschedule",
            name="schedule_end_after_start",
        ),
        migrations.DeleteModel(
            name="DoctorSchedule",
        ),
    ]
