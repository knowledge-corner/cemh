"""
Overweight and obesity in a 5- to 18-year-old.

The cut-offs are the IAP paper's adult-equivalent BMI 23 and 27 lines, which are
age- and sex-specific and sit well below the adult numbers for a young child —
the 23-equivalent line is 15.7 kg/m² at five years. Applying the adult 25 and 30
to a child would call almost every obese ten-year-old normal, so the tests below
check the boundaries themselves rather than only the middle of each band.
"""

import json

from django.test import SimpleTestCase

from growth import bmi
from growth import reference as ref

from .test_growth_reference import _clinic_with

TEN_YEARS = 120


def _row(sex, month=TEN_YEARS):
    path = ref.REFERENCE_DIR / "iap" / f"bmifa_{sex}_5_18.json"
    with path.open() as fh:
        return next(r for r in json.load(fh) if r["Month"] == month)


class TestTheFourBands(SimpleTestCase):
    def setUp(self):
        self.row = _row("boys")

    def _status(self, value):
        return bmi.assess("M", TEN_YEARS, value)["status"]

    def test_below_the_third_centile_is_thinness(self):
        self.assertEqual(self._status(self.row["P3"] - 0.5), bmi.THINNESS)

    def test_between_the_third_centile_and_the_23_line_is_normal(self):
        self.assertEqual(self._status(self.row["P50"]), bmi.NORMAL)

    def test_at_the_23_line_is_overweight(self):
        self.assertEqual(self._status(self.row["Eq23"]), bmi.OVERWEIGHT)

    def test_just_below_the_23_line_is_still_normal(self):
        self.assertEqual(self._status(self.row["Eq23"] - 0.1), bmi.NORMAL)

    def test_at_the_27_line_is_obesity(self):
        self.assertEqual(self._status(self.row["Eq27"]), bmi.OBESITY)

    def test_just_below_the_27_line_is_overweight(self):
        self.assertEqual(self._status(self.row["Eq27"] - 0.1), bmi.OVERWEIGHT)

    def test_exactly_on_the_third_centile_is_normal_not_thin(self):
        self.assertEqual(self._status(self.row["P3"]), bmi.NORMAL)


class TestTheCutoffsAreNotTheAdultOnes(SimpleTestCase):
    def test_a_ten_year_old_at_bmi_21_is_obese_not_normal(self):
        # Adult cut-offs would call this normal; the IAP 27-equivalent line for
        # a ten-year-old boy is 20.5.
        self.assertEqual(bmi.assess("M", TEN_YEARS, 21.0)["status"], bmi.OBESITY)

    def test_the_cutoffs_rise_with_age(self):
        young = bmi.assess("M", 60, 16.0)["cutoffs"]
        older = bmi.assess("M", 216, 16.0)["cutoffs"]
        self.assertLess(young["overweight"], older["overweight"])
        self.assertLess(young["obesity"], older["obesity"])

    def test_the_lines_converge_on_the_adult_values_at_eighteen(self):
        cutoffs = bmi.assess("M", 216, 20.0)["cutoffs"]
        self.assertAlmostEqual(cutoffs["overweight"], 23.0, delta=0.6)
        self.assertAlmostEqual(cutoffs["obesity"], 27.0, delta=0.6)

    def test_boys_and_girls_are_judged_separately(self):
        self.assertNotEqual(
            bmi.assess("M", TEN_YEARS, 16.0)["cutoffs"],
            bmi.assess("F", TEN_YEARS, 16.0)["cutoffs"],
        )


class TestNoVerdictWhenTheCutoffsAreUnavailable(SimpleTestCase):
    """
    A clinic charting against WHO or CDC has no adult-equivalent lines. It must
    get no verdict at all rather than one derived from the wrong cut-offs.
    """

    def test_an_under_five_gets_no_verdict(self):
        self.assertIsNone(bmi.assess("M", 36, 16.0))

    def test_a_who_configured_clinic_gets_no_verdict(self):
        with self.settings(CLINIC=_clinic_with(GROWTH_REFERENCE="WHO")):
            self.assertIsNone(bmi.assess("M", TEN_YEARS, 16.0))

    def test_a_cdc_configured_clinic_gets_no_verdict(self):
        with self.settings(CLINIC=_clinic_with(GROWTH_REFERENCE="CDC")):
            self.assertIsNone(bmi.assess("M", TEN_YEARS, 16.0))

    def test_a_missing_bmi_gets_no_verdict(self):
        self.assertIsNone(bmi.assess("M", TEN_YEARS, None))

    def test_an_age_beyond_the_tables_gets_no_verdict(self):
        self.assertIsNone(bmi.assess("M", 300, 22.0))

    def test_a_row_without_the_eq_columns_gets_no_verdict(self):
        self.assertIsNone(bmi.status({"P3": 12.0, "P50": 16.0}, 16.0))


class TestTheVerdictExplainsItself(SimpleTestCase):
    def test_every_status_carries_a_note_and_its_cutoffs(self):
        result = bmi.assess("F", TEN_YEARS, 22.0)
        self.assertEqual(result["status"], bmi.OBESITY)
        self.assertIn("27", result["note"])
        self.assertEqual(
            sorted(result["cutoffs"]), ["obesity", "overweight", "thinness"]
        )
