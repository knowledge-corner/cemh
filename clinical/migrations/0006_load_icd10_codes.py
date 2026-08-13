"""
Load the WHO ICD-10 code list from ``clinical/data/icd10_codes.tsv``.

The TSV is a compact ``code<TAB>description`` extract of every "category"
level class in the WHO's published ClaML XML (2019 revision, 11,243 codes) —
chapters and blocks (pure groupings, never diagnosed against directly) are
left out, since only category-level codes are ever attached to a patient.
"""

import csv
from pathlib import Path

from django.db import migrations

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "icd10_codes.tsv"
BATCH_SIZE = 2000


def load_codes(apps, schema_editor):
    ICD10Code = apps.get_model("clinical", "ICD10Code")
    if ICD10Code.objects.exists():
        # Idempotent: a re-run (or a second migrate in an already-seeded
        # environment) should not attempt to insert duplicates.
        return

    with DATA_FILE.open(encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        batch = []
        for code, description in reader:
            batch.append(ICD10Code(code=code, description=description))
            if len(batch) >= BATCH_SIZE:
                ICD10Code.objects.bulk_create(batch)
                batch = []
        if batch:
            ICD10Code.objects.bulk_create(batch)


def unload_codes(apps, schema_editor):
    ICD10Code = apps.get_model("clinical", "ICD10Code")
    ICD10Code.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("clinical", "0005_icd10code"),
    ]

    operations = [
        migrations.RunPython(load_codes, unload_codes),
    ]
