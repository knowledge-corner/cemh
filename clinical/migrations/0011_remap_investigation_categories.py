# Re-maps the old 7-category list to the master test catalogue's own 8
# categories (see clinical/models.py InvestigationCategory) so no existing
# row is left pointing at a retired value.
#
# The mapping is evidence-based, not guessed — checked against
# clinical/data/lab_tests.tsv: glucose, HbA1c, insulin, cholesterol, calcium
# and vitamin D tests are filed under "Clinical Chemistry" there, and thyroid
# and hormone tests under "Endocrinology".
from django.db import migrations

_OLD_TO_NEW = {
    "THYROID": "ENDOCRINOLOGY",
    "HORMONE": "ENDOCRINOLOGY",
    "DIABETES": "CLINICAL_CHEMISTRY",
    "LIPID": "CLINICAL_CHEMISTRY",
    "BONE": "CLINICAL_CHEMISTRY",
    "IMAGING": "OTHER",
    # OTHER already matches the new list — nothing to do.
}


def remap_forward(apps, schema_editor):
    Investigation = apps.get_model("clinical", "Investigation")
    for old, new in _OLD_TO_NEW.items():
        Investigation.objects.filter(category=old).update(category=new)


def remap_backward(apps, schema_editor):
    # Not reversible one-to-one — several old categories now share one new
    # category (HORMONE and THYROID both became ENDOCRINOLOGY). Rather than
    # guess which is which, a reversal leaves rows on the new values; the old
    # choices no longer exist to render them against anyway.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("clinical", "0010_alter_investigation_category"),
    ]

    operations = [
        migrations.RunPython(remap_forward, remap_backward),
    ]
