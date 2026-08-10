from django.db import migrations


class Migration(migrations.Migration):
    """
    Drops the "doctor away" feature entirely, table included — a deliberate
    removal, not a refactor. Nothing else references this model any more; see
    the accompanying code changes to appointments/scheduling.py,
    appointments/calendar.py, appointments/admin.py, portal/forms.py and
    portal/views_calendar.py.
    """

    dependencies = [
        ("appointments", "0009_rename_scheduleoverride_to_doctorschedule"),
    ]

    operations = [
        migrations.DeleteModel(
            name="DoctorLeave",
        ),
    ]
