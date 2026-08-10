from django.db import migrations


class Migration(migrations.Migration):
    """
    ScheduleOverride was never really "an override" once nothing was left to
    override — it is the one and only shape a schedule entry takes now. This
    just gives the table its real name; 0008 already cleared the way.
    """

    dependencies = [
        ("appointments", "0008_remove_old_doctorschedule"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="ScheduleOverride",
            new_name="DoctorSchedule",
        ),
    ]
