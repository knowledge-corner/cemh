"""
Load the lab test master list from ``clinical/data/lab_tests.tsv``.

The TSV is ``code<TAB>name<TAB>category`` for 500 common individual lab
tests — a seed catalogue of *names only*. It ships with every clinical
column (reference ranges, units, LOINC codes) intentionally blank; those
live in LabReferenceRange instead, entered by a clinician, never guessed.
"""

import csv
from pathlib import Path

from django.db import migrations

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "lab_tests.tsv"
BATCH_SIZE = 500


def load_tests(apps, schema_editor):
    LabTest = apps.get_model("clinical", "LabTest")
    if LabTest.objects.exists():
        return

    with DATA_FILE.open(encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        batch = []
        for code, name, category in reader:
            batch.append(LabTest(code=code, name=name, category=category))
            if len(batch) >= BATCH_SIZE:
                LabTest.objects.bulk_create(batch)
                batch = []
        if batch:
            LabTest.objects.bulk_create(batch)


def unload_tests(apps, schema_editor):
    LabTest = apps.get_model("clinical", "LabTest")
    LabTest.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("clinical", "0007_labtest_investigation_lab_test_labreferencerange_and_more"),
    ]

    operations = [
        migrations.RunPython(load_tests, unload_tests),
    ]
