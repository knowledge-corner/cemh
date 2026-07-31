"""
Growth reference maths.

These assertions are checked against the SD columns published alongside the LMS
parameters in the reference tables themselves — so this suite proves the code
reproduces the official values rather than merely being self-consistent.
"""

import json
from unittest import mock

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

    def test_school_age_child_is_charted_against_iap(self):
        # The clinic is configured for IAP, whose tables cover 5–18 years.
        result = ref.assess(ref.HEIGHT_FOR_AGE, "F", 144, 150.0)
        self.assertEqual(result["source"], "IAP")
        self.assertEqual(result["kind"], ref.CENTILES)

    def test_child_beyond_iaps_range_falls_to_cdc_and_says_so(self):
        # IAP stops at 18 years. A 19-year-old must still be charted, against
        # CDC, and the result must name CDC rather than implying IAP.
        result = ref.assess(ref.HEIGHT_FOR_AGE, "F", 228, 160.0)
        self.assertEqual(result["source"], "CDC")
        self.assertEqual(result["kind"], ref.LMS)

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
        # Under five, so this is WHO and the percentile is computed exactly.
        tall = ref.assess(ref.HEIGHT_FOR_AGE, "M", 48, 115.0)
        short = ref.assess(ref.HEIGHT_FOR_AGE, "M", 48, 91.0)
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


class TestStandardSelection(SimpleTestCase):
    """
    The GROWTH_REFERENCE setting must actually choose the reference used. It
    previously did not — the band map was hard-coded while the interface
    displayed the setting, which implied a choice that was not wired up.
    """

    def test_who_standard_uses_who_then_cdc(self):
        sources = [band[0] for band in ref._tables_for(ref.HEIGHT_FOR_AGE, "WHO")]
        self.assertEqual(sources, ["who", "cdc"])

    def test_iap_standard_leads_with_iap_then_who_then_cdc(self):
        # IAP leads so that a child of exactly 5.0 years — an age both the WHO
        # and the IAP band contain — is charted against IAP, which is where the
        # IAP charts are meant to begin.
        sources = [band[0] for band in ref._tables_for(ref.HEIGHT_FOR_AGE, "IAP")]
        self.assertEqual(sources, ["iap", "who", "cdc"])

    def test_the_five_year_boundary_belongs_to_iap(self):
        just_under = ref.assess(ref.HEIGHT_FOR_AGE, "M", 59, 108.0)
        exactly_five = ref.assess(ref.HEIGHT_FOR_AGE, "M", 60, 108.0)
        self.assertEqual(just_under["source"], "WHO")
        self.assertEqual(exactly_five["source"], "IAP")

    def test_cdc_standard_leads_with_cdc(self):
        sources = [band[0] for band in ref._tables_for(ref.HEIGHT_FOR_AGE, "CDC")]
        self.assertEqual(sources[0], "cdc")

    def test_under_fives_use_who_whichever_standard_is_chosen(self):
        # Every standard on offer recommends WHO below five; only what happens
        # above five differs.
        for standard in ("WHO", "IAP"):
            with self.settings(CLINIC=_clinic_with(GROWTH_REFERENCE=standard)):
                result = ref.assess(ref.HEIGHT_FOR_AGE, "M", 24, 87.0)
            self.assertEqual(result["source"], "WHO", msg=f"standard={standard}")

    def test_choosing_cdc_charts_an_under_five_against_cdc(self):
        # Proves the setting really does change the answer, not just the order
        # of a list nobody consults.
        with self.settings(CLINIC=_clinic_with(GROWTH_REFERENCE="CDC")):
            result = ref.assess(ref.HEIGHT_FOR_AGE, "M", 36, 95.0)
        self.assertEqual(result["source"], "CDC")

    def test_an_unknown_standard_falls_back_to_the_default(self):
        with self.settings(CLINIC=_clinic_with(GROWTH_REFERENCE="NONSENSE")):
            self.assertEqual(ref.active_standard(), ref.DEFAULT_STANDARD)

    def test_a_standard_whose_tables_are_missing_falls_back_visibly(self):
        # Were the IAP files absent, a school-age child must still be charted —
        # against CDC — and must say CDC, rather than silently pretending to be
        # IAP or refusing to chart at all.
        absent = {ref.HEIGHT_FOR_AGE: [("iap", "lhfa_{sex}_not_installed", 60, 216)]}
        with mock.patch.dict(ref._BANDS, {"iap": absent}), \
                self.settings(CLINIC=_clinic_with(GROWTH_REFERENCE="IAP")):
            result = ref.assess(ref.HEIGHT_FOR_AGE, "F", 144, 150.0)
        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "CDC")
        self.assertNotEqual(result["source"], "IAP")


def _clinic_with(**overrides):
    """A stand-in clinic config module with selected values overridden."""
    import types

    from django.conf import settings as django_settings

    clone = types.SimpleNamespace(**vars(django_settings.CLINIC))
    for key, value in overrides.items():
        setattr(clone, key, value)
    return clone
