"""
The IAP 2015 centile references.

These are a different kind of reference from WHO and CDC — published curves
rather than LMS parameters — and the point of most of what follows is that the
application says so, and does not quietly claim more precision than the paper
supports.

Expected values are read out of the installed tables rather than written in by
hand, so a table that disagreed with the paper would surface here rather than
being silently baked into the assertions too.
"""

import json

from django.test import SimpleTestCase

from growth import centiles
from growth import reference as ref

BOYS_5Y, GIRLS_10Y = 60, 120


def _row(stem, month):
    """One published row, straight from the installed file."""
    path = ref.REFERENCE_DIR / "iap" / f"{stem}.json"
    with path.open() as fh:
        return next(r for r in json.load(fh) if r["Month"] == month)


class TestTablesAreInstalled(SimpleTestCase):
    def test_all_six_tables_are_present(self):
        for indicator in ("lhfa", "wfa", "bmifa"):
            for sex in ("boys", "girls"):
                path = ref.REFERENCE_DIR / "iap" / f"{indicator}_{sex}_5_18.json"
                self.assertTrue(path.exists(), f"{path.name} is missing")

    def test_each_table_covers_five_to_eighteen_years(self):
        rows, kind = ref._load_table("iap", "lhfa_boys_5_18")
        self.assertEqual(kind, ref.CENTILES)
        self.assertEqual(len(rows), 27)
        self.assertEqual((rows[0]["month"], rows[-1]["month"]), (60.0, 216.0))


class TestAgainstThePublishedCentiles(SimpleTestCase):
    """A value sitting exactly on a printed curve must be reported as on it."""

    def test_each_printed_height_centile_scores_itself(self):
        row = _row("lhfa_boys_5_18", 60)
        for column, percentile in centiles.PERCENTILE_COLUMNS.items():
            if column not in row:
                continue
            result = ref.assess(ref.HEIGHT_FOR_AGE, "M", BOYS_5Y, row[column])
            self.assertEqual(result["centile"], float(percentile), msg=column)
            self.assertEqual(result["band"], (percentile, percentile), msg=column)

    def test_the_median_scores_an_sds_of_zero(self):
        row = _row("wfa_girls_5_18", GIRLS_10Y)
        result = ref.assess(ref.WEIGHT_FOR_AGE, "F", GIRLS_10Y, row["P50"])
        self.assertEqual(result["sds"], 0.0)
        self.assertEqual(result["band_label"], "50th centile")

    def test_a_value_between_two_curves_is_reported_as_between_them(self):
        row = _row("lhfa_boys_5_18", 60)
        midway = (row["P25"] + row["P50"]) / 2
        result = ref.assess(ref.HEIGHT_FOR_AGE, "M", BOYS_5Y, midway)
        self.assertEqual(result["band"], (25, 50))
        self.assertEqual(result["band_label"], "25th–50th centile")
        self.assertLess(25, result["centile"])
        self.assertLess(result["centile"], 50)

    def test_the_source_and_kind_are_always_reported(self):
        result = ref.assess(ref.HEIGHT_FOR_AGE, "M", BOYS_5Y, 110.0)
        self.assertEqual(result["source"], "IAP")
        self.assertEqual(result["kind"], ref.CENTILES)


class TestNoExactZScoreIsInvented(SimpleTestCase):
    """
    IAP publishes no LMS, so no exact z-score exists. The keys that would hold
    one stay empty rather than being filled with something fitted.
    """

    def test_z_and_percentile_are_empty_for_a_published_reference(self):
        result = ref.assess(ref.HEIGHT_FOR_AGE, "M", BOYS_5Y, 110.0)
        self.assertIsNone(result["z"])
        self.assertIsNone(result["percentile"])

    def test_asking_for_a_z_score_directly_is_refused(self):
        with self.assertRaises(ref.NotComputable):
            ref.z_score(ref.HEIGHT_FOR_AGE, "M", BOYS_5Y, 110.0)

    def test_asking_for_a_value_at_a_z_score_is_refused(self):
        with self.assertRaises(ref.NotComputable):
            ref.value_for_z(ref.HEIGHT_FOR_AGE, "M", BOYS_5Y, 0.0)

    def test_the_published_sd_column_is_not_used_for_scoring(self):
        # The SD printed alongside the centiles is the sample SD, not a
        # parameter of the skewed distribution the centiles describe. Using it
        # would put a child on the printed 97th centile at +2.2 to +3.4 SDS
        # instead of +1.88. Confirm the SDS follows the centiles, not the SD.
        row = _row("wfa_girls_5_18", BOYS_5Y)
        result = ref.assess(ref.WEIGHT_FOR_AGE, "F", BOYS_5Y, row["P97"])
        self.assertAlmostEqual(result["sds"], 1.88, delta=0.01)
        from_sd_column = (row["P97"] - row["P50"]) / row["SD"]
        self.assertGreater(from_sd_column, 3.0)  # what we are declining to report


class TestOffTheScale(SimpleTestCase):
    """
    Below the 3rd centile there is no second curve to interpolate against, and
    that is exactly where short stature is diagnosed. The band is still reported;
    the number is not invented; an LMS reference supplies one alongside.
    """

    def setUp(self):
        row = _row("lhfa_boys_5_18", 60)
        self.below = row["P3"] - 3
        self.above = row["P97"] + 3

    def test_a_child_below_the_third_centile_is_named_as_such(self):
        result = ref.assess(ref.HEIGHT_FOR_AGE, "M", BOYS_5Y, self.below)
        self.assertEqual(result["off_scale"], "below")
        self.assertEqual(result["band_label"], "below the 3rd centile")

    def test_no_centile_or_sds_is_invented_off_the_scale(self):
        result = ref.assess(ref.HEIGHT_FOR_AGE, "M", BOYS_5Y, self.below)
        self.assertIsNone(result["centile"])
        self.assertIsNone(result["sds"])

    def test_a_companion_z_score_is_supplied_and_labelled(self):
        result = ref.assess(ref.HEIGHT_FOR_AGE, "M", BOYS_5Y, self.below)
        self.assertIsNotNone(result["companion"])
        self.assertEqual(result["companion"]["source"], "CDC")
        self.assertLess(result["companion"]["z"], -2)

    def test_a_child_above_the_ninety_seventh_is_named_as_such(self):
        result = ref.assess(ref.HEIGHT_FOR_AGE, "M", BOYS_5Y, self.above)
        self.assertEqual(result["off_scale"], "above")
        self.assertEqual(result["band_label"], "above the 97th centile")

    def test_a_child_on_the_scale_gets_no_companion(self):
        result = ref.assess(ref.HEIGHT_FOR_AGE, "M", BOYS_5Y, 110.0)
        self.assertIsNone(result["companion"])


class TestTheBmiEqColumnsAreNotCentiles(SimpleTestCase):
    """
    23-Eq and 27-Eq mark adult-equivalent overweight and obesity. Treating them
    as the 90th and 97th centiles would misclassify children in both directions.
    """

    def test_eq_columns_are_absent_from_the_percentile_map(self):
        self.assertNotIn("Eq23", centiles.PERCENTILE_COLUMNS)
        self.assertNotIn("Eq27", centiles.PERCENTILE_COLUMNS)

    def test_a_bmi_above_the_eq_lines_is_not_called_a_high_centile(self):
        row = _row("bmifa_boys_5_18", GIRLS_10Y)
        result = ref.assess(ref.BMI_FOR_AGE, "M", GIRLS_10Y, row["Eq27"] + 1)
        # The printed BMI centiles stop at the 50th, so anything above it is
        # off the scale rather than being placed against a cut-off line.
        self.assertEqual(result["off_scale"], "above")
        self.assertEqual(result["band_label"], "above the 50th centile")

    def test_bmi_centiles_below_the_median_still_band_normally(self):
        row = _row("bmifa_girls_5_18", GIRLS_10Y)
        midway = (row["P10"] + row["P25"]) / 2
        result = ref.assess(ref.BMI_FOR_AGE, "F", GIRLS_10Y, midway)
        self.assertEqual(result["band"], (10, 25))


class TestCurvesComeFromThePaper(SimpleTestCase):
    def test_curves_are_the_printed_values_not_computed_ones(self):
        curves = ref.reference_curves(ref.HEIGHT_FOR_AGE, "M", 60, 72, step=6.0)
        row = _row("lhfa_boys_5_18", 60)
        at_five = {p: series[0]["value"] for p, series in curves.items()}
        for column, percentile in centiles.PERCENTILE_COLUMNS.items():
            if column in row and percentile in at_five:
                self.assertEqual(at_five[percentile], row[column], msg=column)

    def test_bmi_cutoff_lines_are_returned_separately_and_labelled(self):
        lines = ref.cutoff_curves(ref.BMI_FOR_AGE, "M", 60, 72, step=6.0)
        self.assertEqual(sorted(lines), ["Eq23", "Eq27"])
        self.assertIn("adult BMI 23", lines["Eq23"]["label"])
        self.assertIn("adult BMI 27", lines["Eq27"]["label"])

    def test_an_lms_chart_has_no_cutoff_lines(self):
        self.assertEqual(ref.cutoff_curves(ref.BMI_FOR_AGE, "M", 12, 24), {})

    def test_a_chart_spanning_five_years_uses_both_references(self):
        # A chart from 4 to 6 years crosses from WHO to IAP. Both halves must be
        # drawn — the curve must not stop at the boundary.
        curves = ref.reference_curves(ref.HEIGHT_FOR_AGE, "M", 48, 72, step=3.0)
        months = [point["month"] for point in curves[50]]
        self.assertLess(min(months), 60)
        self.assertGreater(max(months), 60)
