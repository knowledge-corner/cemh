"""
The downloadable template and the importer that reads it back in.

Mirrors appointments/tests for schedules_csv.py in spirit: valid rows import
even when others in the same file fail, and nothing lands as VALIDATED
except by an explicit column in the file saying so.
"""

import io
from decimal import Decimal

from django.test import TestCase

from clinical import lab_reference_csv
from clinical.models import LabReferenceRange, LabTest, ReferenceSex, ReferenceStatus


def as_file(text):
    return io.BytesIO(text.encode("utf-8"))


class TestTemplateCsv(TestCase):
    def test_it_lists_every_active_test(self):
        content = lab_reference_csv.template_csv()
        lines = content.strip().splitlines()
        # header + help row + one row per test
        self.assertEqual(len(lines), 2 + LabTest.objects.filter(is_active=True).count())

    def test_it_can_be_narrowed_to_one_category(self):
        content = lab_reference_csv.template_csv(category="Endocrinology")
        lines = content.strip().splitlines()
        self.assertEqual(
            len(lines), 2 + LabTest.objects.filter(category="Endocrinology").count(),
        )
        self.assertIn("TSH", content)
        self.assertNotIn("Hemoglobin", content)

    def test_every_row_carries_its_own_test_code(self):
        content = lab_reference_csv.template_csv(category="Endocrinology")
        self.assertIn("LAB0213,TSH", content)


class LabReferenceCsvTestCase(TestCase):
    def setUp(self):
        self.tsh = LabTest.objects.get(code="LAB0213")
        self.header = ",".join(lab_reference_csv.COLUMNS)

    def row(self, **overrides):
        values = {
            "test_code": self.tsh.code, "test_name": self.tsh.name,
            "sex": "ANY", "age_min": "", "age_max": "", "age_unit": "years",
            "pregnancy_status": "", "fasting_status": "",
            "low": "0.4", "high": "4.0", "unit": "mIU/L",
            "source": "test source", "source_year": "2023",
            "notes": "", "status": "VALIDATED",
        }
        values.update(overrides)
        return ",".join(values[c] for c in lab_reference_csv.COLUMNS)


class TestParsingValidRows(LabReferenceCsvTestCase):
    def test_a_well_formed_row_imports(self):
        result = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row()}\n"))
        self.assertTrue(result.can_import)
        self.assertEqual(len(result.planned), 1)
        self.assertEqual(result.planned[0].lab_test, self.tsh)

    def test_missing_status_defaults_to_review_required_not_validated(self):
        result = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row(status='')}\n"))
        self.assertTrue(result.can_import)
        self.assertEqual(result.planned[0].fields["status"], ReferenceStatus.REVIEW_REQUIRED)

    def test_status_is_case_and_spacing_tolerant(self):
        result = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row(status='review required')}\n"))
        self.assertEqual(result.planned[0].fields["status"], ReferenceStatus.REVIEW_REQUIRED)

    def test_a_range_with_only_an_upper_bound_is_allowed(self):
        result = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row(low='')}\n"))
        self.assertTrue(result.can_import)
        self.assertIsNone(result.planned[0].fields["low"])

    def test_commit_writes_the_row(self):
        result = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row()}\n"))
        created, updated = lab_reference_csv.commit(result)
        self.assertEqual((created, updated), (1, 0))
        range_ = LabReferenceRange.objects.get()
        self.assertEqual(range_.lab_test, self.tsh)
        self.assertEqual(range_.low, Decimal("0.4"))
        self.assertEqual(range_.status, ReferenceStatus.VALIDATED)

    def test_reuploading_the_same_band_updates_rather_than_duplicates(self):
        first = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row()}\n"))
        lab_reference_csv.commit(first)
        second = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row(high='5.0')}\n"))
        created, updated = lab_reference_csv.commit(second)
        self.assertEqual((created, updated), (0, 1))
        self.assertEqual(LabReferenceRange.objects.count(), 1)
        self.assertEqual(LabReferenceRange.objects.get().high, Decimal("5.0"))

    def test_a_different_sex_band_is_a_separate_row_not_an_update(self):
        first = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row(sex='MALE')}\n"))
        lab_reference_csv.commit(first)
        second = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row(sex='FEMALE')}\n"))
        created, _updated = lab_reference_csv.commit(second)
        self.assertEqual(created, 1)
        self.assertEqual(LabReferenceRange.objects.count(), 2)


class TestParsingRejectsBadRows(LabReferenceCsvTestCase):
    def test_missing_columns_is_fatal(self):
        result = lab_reference_csv.parse(as_file("test_code,test_name\nLAB0213,TSH\n"))
        self.assertTrue(result.fatal)
        self.assertFalse(result.can_import)

    def test_an_unknown_test_code_is_skipped_not_fatal(self):
        result = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row(test_code='LAB9999')}\n"))
        self.assertEqual(len(result.problems), 1)
        self.assertFalse(result.planned)

    def test_an_invalid_sex_is_skipped(self):
        result = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row(sex='OTHER')}\n"))
        self.assertEqual(len(result.problems), 1)
        self.assertIn("sex", result.problems[0].message)

    def test_neither_low_nor_high_is_skipped(self):
        result = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row(low='', high='')}\n"))
        self.assertEqual(len(result.problems), 1)

    def test_high_below_low_is_skipped(self):
        result = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row(low='10', high='1')}\n"))
        self.assertEqual(len(result.problems), 1)

    def test_missing_unit_is_skipped(self):
        result = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row(unit='')}\n"))
        self.assertEqual(len(result.problems), 1)

    def test_missing_source_is_skipped(self):
        result = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row(source='')}\n"))
        self.assertEqual(len(result.problems), 1)

    def test_an_unrecognised_status_is_skipped_not_silently_defaulted(self):
        result = lab_reference_csv.parse(as_file(f"{self.header}\n{self.row(status='DEFINITELY_TRUE')}\n"))
        self.assertEqual(len(result.problems), 1)
        self.assertFalse(result.planned)

    def test_a_good_row_still_imports_alongside_a_bad_one(self):
        bad = self.row(test_code="LAB9999")
        good = self.row()
        result = lab_reference_csv.parse(as_file(f"{self.header}\n{bad}\n{good}\n"))
        self.assertEqual(len(result.planned), 1)
        self.assertEqual(len(result.problems), 1)
        self.assertTrue(result.can_import)
