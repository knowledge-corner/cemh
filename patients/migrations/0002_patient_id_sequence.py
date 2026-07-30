"""
Creates the Postgres sequence that allocates patient IDs.

A sequence rather than ``MAX(id) + 1`` because two receptionists registering
patients at the same instant would otherwise both read the same maximum and
produce a duplicate patient ID. ``nextval`` is atomic and never returns the
same value twice, even under concurrency and even inside a transaction that
later rolls back.
"""

from django.db import migrations

from patients.models import PATIENT_ID_SEQUENCE


class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=f"CREATE SEQUENCE IF NOT EXISTS {PATIENT_ID_SEQUENCE} START WITH 1 INCREMENT BY 1;",
            reverse_sql=f"DROP SEQUENCE IF EXISTS {PATIENT_ID_SEQUENCE};",
        ),
    ]
