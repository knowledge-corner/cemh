"""Creates the Postgres sequence that allocates receipt numbers.

Same reasoning as the patient-ID sequence: receipt numbers are financial
records and must never collide.
"""

from django.db import migrations

from billing.models import RECEIPT_SEQUENCE


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(
            sql=f"CREATE SEQUENCE IF NOT EXISTS {RECEIPT_SEQUENCE} START WITH 1 INCREMENT BY 1;",
            reverse_sql=f"DROP SEQUENCE IF EXISTS {RECEIPT_SEQUENCE};",
        ),
    ]
