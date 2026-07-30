"""
Growth reference maths.

These assertions are checked against the SD columns published alongside the LMS
parameters in the reference tables themselves — so this suite proves the code
reproduces the official values rather than merely being self-consistent.
"""

import json

from django.test import SimpleTestCase

from growth import reference as ref


def _published_row(directory, stem, index=0):
    path = ref.REFERENCE_DIR / directory / f"{stem}.json"
    with path.open() as fh:
        return json.load(fh)[index]


class TestAgainstPublishedValues(SimpleTestCase):
    """The LMS conversion must reproduce WHO's own printed SD columns."""

    def test_who_height_for_age_matches_published_sd_columns(self):
        row = _published_row("who", "lhfa_boys_0_5")

        for z, column in [(-3, "SD3neg"), (-2, "SD2neg"), (-1, "SD1neg"),
                          (0, "SD0"), (1, "SD1"), (2, "SD2"), (3, "SD3")]:
            computed = ref.value_for_z(ref.HEIGHT_FOR_AGE, "M", 0, z)
            published = float(row[column])
            self.assertAlmostEqual(
                computed, published, delta=0.05,
                msg=f"z={z} should reproduce the published {column} value",
            )

    def test_who_weight_for_age_matches_published_sd_columns(self):
        row = _published_row("who", "wfa_girls_0_5", index=12)
        month = float(row["Month"])

        for z, column in [(-2, "SD2neg"), (0, "SD0"), (2, "SD2")]:
            computed = ref.value_for_z(ref.WEIGHT_FOR_AGE, "F", month, z)
            self.assertAlmostEqual(computed, float(row[column]), delta=0.05)


class TestZScoreAndPercentile(SimpleTestCase):
    def test_median_value_scores_zero(self):
        row = _published_row("who", "lhfa_boys_0_5")
        z = ref.z_score(ref.HEIGHT_FOR_AGE, "M", 0, float(row["M"]))
        self.assertAlmostEqual(z, 0.0, places=6)

    def test_z_and_value_are_inverses(self):
        original = 105.0
        z = ref.z_score(ref.HEIGHT_FOR_AGE, "M", 48, original)
        self.assertAlmostEqual(
            ref.value_for_z(ref.HEIGHT_FOR_AGE, "M", 48, z), original, places=6
        )

    def test_percentile_conversion_matches_known_normal_values(self):
        self.assertAlmostEqual(ref.z_to_percentile(0), 50.0, places=6)
        self.assertAlmostEqual(ref.z_to_percentile(-1.96), 2.5, places=1)
        self.assertAlmostEqual(ref.z_to_percentile(1.96), 97.5, places=1)

    def test_percentile_round_trips_through_z(self):
        for percentile in ref.CHART_PERCENTILES:
            z = ref.z_for_percentile(percentile)
            self.assertAlmostEqual(ref.percentile_for_z(z), percentile, places=1)

    def test_percentile_outside_zero_to_hundred_is_rejected(self):
        for bad in (0, 100, -5, 150):
            with self.assertRaises(ValueError):
                ref.z_for_percentile(bad)


class TestAssess(SimpleTestCase):
    def test_assess_returns_z_percentile_and_source(self):
        result = ref.assess(ref.HEIGHT_FOR_AGE, "M", 0, 49.9)
        self.assertEqual(result["source"], "WHO")
        self.assertAlmostEqual(result["z"], 0.0, delta=0.05)
        self.assertAlmostEqual(result["percentile"], 50.0, delta=2)

    def test_school_age_child_falls_to_the_cdc_table(self):
        result = ref.assess(ref.HEIGHT_FOR_AGE, "F", 144, 150.0)
        self.assertEqual(result["source"], "CDC")

    def test_age_beyond_published_data_returns_nothing(self):
        # Silence is correct here — a wrong percentile is worse than no chart.
        self.assertIsNone(ref.assess(ref.HEIGHT_FOR_AGE, "M", 300, 170))

    def test_missing_value_returns_nothing(self):
        self.assertIsNone(ref.assess(ref.HEIGHT_FOR_AGE, "M", 24, None))

    def test_unknown_sex_returns_nothing_rather_than_guessing(self):
        # References are published separately for boys and girls; there is no
        # defensible way to chart "other" against either.
        self.assertIsNone(ref.assess(ref.HEIGHT_FOR_AGE, "O", 24, 87.0))

    def test_short_child_scores_below_third_centile(self):
        tall = ref.assess(ref.HEIGHT_FOR_AGE, "M", 60, 120.0)
        short = ref.assess(ref.HEIGHT_FOR_AGE, "M", 60, 98.0)
        self.assertLess(short["percentile"], 3)
        self.assertGreater(tall["percentile"], short["percentile"])


class TestReferenceCurves(SimpleTestCase):
    def test_curves_are_returned_for_each_charted_percentile(self):
        curves = ref.reference_curves(ref.HEIGHT_FOR_AGE, "M", 0, 24)
        self.assertEqual(sorted(curves), sorted(ref.CHART_PERCENTILES))
        for series in curves.values():
            self.assertTrue(series)

    def test_curves_increase_with_percentile(self):
        curves = ref.reference_curves(ref.HEIGHT_FOR_AGE, "F", 12, 24)
        at_twelve = {p: series[0]["value"] for p, series in curves.items()}
        ordered = [at_twelve[p] for p in sorted(at_twelve)]
        self.assertEqual(ordered, sorted(ordered), "P3 must sit below P97")

    def test_curves_stop_where_the_data_stops(self):
        # Head circumference is published only to 5 years; the curve must not
        # be extrapolated past that.
        curves = ref.reference_curves(ref.HEAD_CIRCUMFERENCE_FOR_AGE, "M", 0, 120)
        highest = max(point["month"] for point in curves[50])
        self.assertLessEqual(highest, 60)
