import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Prepares ScheduleOverride to become the one and only schedule table.

    series_id is the new field the "Add event" form uses to group the rows one
    recurring booking creates. The related_name changes just catch the model up
    to what it is about to be called — nothing here touches actual columns
    beyond adding series_id, so it is cheap and instant on a real database.
    """

    dependencies = [
        ("appointments", "0005_visit_is_walk_in"),
    ]

    operations = [
        migrations.AddField(
            model_name="scheduleoverride",
            name="series_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            model_name="scheduleoverride",
            name="doctor",
            field=models.ForeignKey(
                limit_choices_to={"role": "DOCTOR"},
                on_delete=django.db.models.deletion.CASCADE,
                related_name="schedule_entries",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="scheduleoverride",
            name="cabin",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="schedule_entries",
                to="appointments.cabin",
            ),
        ),
        migrations.AlterField(
            model_name="scheduleoverride",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="schedule_entries_created",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
