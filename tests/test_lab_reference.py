"""
Lab test master list, reference ranges, and the normal/abnormal flag they
can drive on an investigation.

The one rule under everything here: nothing is ever flagged, converted or
suggested unless a real, VALIDATED reference range exists for it. Most of
this file is checking that rule holds under the various ways it could
quietly stop being true — no range, an unvalidated range, a unit nobody
told the system how to convert.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import VisitStatus
from clinical import lab_reference
from clinical.lab_reference import Evaluation, evaluate_value
from clinical.models import (
    Investigation, LabReferenceRange, LabTest, LabUnitConversion, ReferenceSex,
    ReferenceStatus,
)

from .factories import make_doctor, make_patient, make_receptionist, make_visit


def make_range(lab_test, **kwargs):
    defaults = dict(
        sex=ReferenceSex.ANY, low=Decimal("1"), high=Decimal("10"), unit="U",
        source="test fixture", status=ReferenceStatus.VALIDATED,
    )
    defaults.update(kwargs)
    return LabReferenceRange.objects.create(lab_test=lab_test, **defaults)


class TestTheMasterListLoaded(TestCase):
    def test_all_five_hundred_tests_loaded(self):
        self.assertEqual(LabTest.objects.count(), 500)

    def test_a_familiar_test_is_present(self):
        tsh = LabTest.objects.get(code="LAB0213")
        self.assertEqual(tsh.name, "TSH")
        self.assertEqual(tsh.category, "Endocrinology")

    def test_codes_are_unique(self):
        self.assertEqual(
            LabTest.objects.count(), LabTest.objects.values("code").distinct().count(),
        )

    def test_no_reference_ranges_were_seeded(self):
        # The whole point — see clinical.models.LabTest's docstring and the
        # seed data's own README. Nothing here is invented.
        self.assertEqual(LabReferenceRange.objects.count(), 0)


class TestBestMatchingRange(TestCase):
    def setUp(self):
        self.test = LabTest.objects.get(code="LAB0213")

    def test_an_any_range_matches_everyone(self):
        r = make_range(self.test, sex=ReferenceSex.ANY)
        self.assertEqual(lab_reference.best_matching_range(self.test, sex="M", age_years=30), r)
        self.assertEqual(lab_reference.best_matching_range(self.test, sex="F", age_years=5), r)

    def test_a_sex_specific_range_beats_any_for_that_sex(self):
        make_range(self.test, sex=ReferenceSex.ANY, low=Decimal("1"), high=Decimal("10"))
        specific = make_range(self.test, sex=ReferenceSex.FEMALE, low=Decimal("2"), high=Decimal("8"))
        self.assertEqual(
            lab_reference.best_matching_range(self.test, sex="F", age_years=30), specific,
        )

    def test_a_sex_specific_range_does_not_match_the_other_sex(self):
        make_range(self.test, sex=ReferenceSex.MALE)
        self.assertIsNone(lab_reference.best_matching_range(self.test, sex="F", age_years=30))

    def test_an_age_band_is_respected(self):
        child = make_range(self.test, age_min=Decimal("0"), age_max=Decimal("12"))
        adult = make_range(self.test, age_min=Decimal("13"), age_max=Decimal("120"))
        self.assertEqual(lab_reference.best_matching_range(self.test, age_years=5), child)
        self.assertEqual(lab_reference.best_matching_range(self.test, age_years=40), adult)

    def test_outside_every_age_band_matches_nothing(self):
        make_range(self.test, age_min=Decimal("0"), age_max=Decimal("12"))
        self.assertIsNone(lab_reference.best_matching_range(self.test, age_years=40))

    def test_only_validated_ranges_are_ever_matched(self):
        make_range(self.test, status=ReferenceStatus.REVIEW_REQUIRED)
        make_range(self.test, status=ReferenceStatus.REFERENCE_REQUIRED)
        make_range(self.test, status=ReferenceStatus.DEPRECATED)
        self.assertIsNone(lab_reference.best_matching_range(self.test, sex="M", age_years=30))

    def test_other_gender_options_fall_back_to_any_only(self):
        # Patient sex codes "O" (Other) and "N" (Prefer not to say) don't map
        # to a ReferenceSex — they should only ever match an ANY-scoped range,
        # never accidentally a MALE or FEMALE one.
        make_range(self.test, sex=ReferenceSex.MALE)
        self.assertIsNone(lab_reference.best_matching_range(self.test, sex="O", age_years=30))
        any_range = make_range(self.test, sex=ReferenceSex.ANY)
        self.assertEqual(lab_reference.best_matching_range(self.test, sex="O", age_years=30), any_range)


class TestConvertValue(TestCase):
    def setUp(self):
        self.test = LabTest.objects.get(code="LAB0213")

    def test_same_unit_is_a_no_op(self):
        self.assertEqual(lab_reference.convert_value(Decimal("5"), "mIU/L", "mIU/L"), Decimal("5"))

    def test_no_conversion_on_file_returns_none(self):
        self.assertIsNone(lab_reference.convert_value(Decimal("5"), "mg/dL", "mmol/L", lab_test=self.test))

    def test_a_test_specific_conversion_is_used(self):
        LabUnitConversion.objects.create(
            lab_test=self.test, from_unit="ng/mL", to_unit="ng/L", multiplier=Decimal("1000"),
        )
        self.assertEqual(
            lab_reference.convert_value(Decimal("2"), "ng/mL", "ng/L", lab_test=self.test),
            Decimal("2000"),
        )

    def test_a_generic_conversion_is_used_when_no_test_specific_one_exists(self):
        LabUnitConversion.objects.create(
            lab_test=None, from_unit="g/L", to_unit="mg/dL", multiplier=Decimal("100"),
        )
        self.assertEqual(
            lab_reference.convert_value(Decimal("1"), "g/L", "mg/dL", lab_test=self.test),
            Decimal("100"),
        )

    def test_a_test_specific_conversion_is_preferred_over_a_generic_one(self):
        LabUnitConversion.objects.create(
            lab_test=None, from_unit="x", to_unit="y", multiplier=Decimal("1"),
        )
        LabUnitConversion.objects.create(
            lab_test=self.test, from_unit="x", to_unit="y", multiplier=Decimal("2"),
        )
        self.assertEqual(
            lab_reference.convert_value(Decimal("10"), "x", "y", lab_test=self.test),
            Decimal("20"),
        )


class TestEvaluateValue(TestCase):
    def setUp(self):
        self.test = LabTest.objects.get(code="LAB0213")

    def test_no_lab_test_says_nothing(self):
        result = evaluate_value(None, "5", "U", sex="M", age_years=30)
        self.assertEqual(result, Evaluation())

    def test_no_reference_range_on_file_notes_it(self):
        result = evaluate_value(self.test, "5", "U", sex="M", age_years=30)
        self.assertIsNone(result.reference_range)
        self.assertIn("No validated reference range", result.note)

    def test_no_value_yet_still_returns_the_range(self):
        r = make_range(self.test, low=Decimal("1"), high=Decimal("10"), unit="U")
        result = evaluate_value(self.test, None, "", sex="M", age_years=30)
        self.assertEqual(result.reference_range, r)
        self.assertIsNone(result.is_abnormal)

    def test_a_value_inside_the_range_is_not_abnormal(self):
        make_range(self.test, low=Decimal("1"), high=Decimal("10"), unit="U")
        result = evaluate_value(self.test, "5", "U", sex="M", age_years=30)
        self.assertFalse(result.is_abnormal)

    def test_a_value_above_the_range_is_abnormal(self):
        make_range(self.test, low=Decimal("1"), high=Decimal("10"), unit="U")
        result = evaluate_value(self.test, "15", "U", sex="M", age_years=30)
        self.assertTrue(result.is_abnormal)

    def test_a_value_below_the_range_is_abnormal(self):
        make_range(self.test, low=Decimal("1"), high=Decimal("10"), unit="U")
        result = evaluate_value(self.test, "0.5", "U", sex="M", age_years=30)
        self.assertTrue(result.is_abnormal)

    def test_a_range_with_only_a_lower_bound(self):
        make_range(self.test, low=Decimal("1"), high=None, unit="U")
        self.assertFalse(evaluate_value(self.test, "100", "U", sex="M", age_years=30).is_abnormal)
        self.assertTrue(evaluate_value(self.test, "0.1", "U", sex="M", age_years=30).is_abnormal)

    def test_mismatched_unit_with_no_conversion_says_so_and_does_not_guess(self):
        make_range(self.test, low=Decimal("1"), high=Decimal("10"), unit="mIU/L")
        result = evaluate_value(self.test, "5", "ng/mL", sex="M", age_years=30)
        self.assertIsNone(result.is_abnormal)
        self.assertIn("no conversion", result.note)

    def test_mismatched_unit_with_a_conversion_is_converted_before_comparing(self):
        make_range(self.test, low=Decimal("1000"), high=Decimal("10000"), unit="ng/L")
        LabUnitConversion.objects.create(
            lab_test=self.test, from_unit="ng/mL", to_unit="ng/L", multiplier=Decimal("1000"),
        )
        # 5 ng/mL -> 5000 ng/L, inside 1000-10000
        result = evaluate_value(self.test, "5", "ng/mL", sex="M", age_years=30)
        self.assertFalse(result.is_abnormal)

    def test_a_review_required_range_is_never_used_to_flag(self):
        make_range(self.test, status=ReferenceStatus.REVIEW_REQUIRED)
        result = evaluate_value(self.test, "999", "U", sex="M", age_years=30)
        self.assertIsNone(result.reference_range)
        self.assertIn("No validated reference range", result.note)


class TestTheLabTestSearchEndpoint(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()

    def search(self, query):
        return self.client.get(reverse("doctor_lab_test_search"), {"q": query})

    def test_a_doctor_can_search_by_name(self):
        self.client.force_login(self.doctor)
        response = self.search("TSH")
        self.assertContains(response, "TSH")

    def test_a_doctor_can_search_by_code_prefix(self):
        self.client.force_login(self.doctor)
        response = self.search("LAB0213")
        self.assertContains(response, "TSH")

    def test_a_short_query_returns_nothing(self):
        self.client.force_login(self.doctor)
        response = self.search("t")
        self.assertNotContains(response, "picklist__item")

    def test_a_receptionist_is_refused(self):
        self.client.force_login(self.receptionist)
        self.assertEqual(self.search("TSH").status_code, 403)


class TestTheEvaluateEndpoint(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.test = LabTest.objects.get(code="LAB0213")
        self.patient = make_patient(sex="F", date_of_birth=timezone.localdate() - timedelta(days=30 * 365))

    def evaluate_url(self):
        return reverse("doctor_lab_evaluate", args=[self.patient.patient_id])

    def test_uses_the_patients_own_age_and_sex(self):
        self.client.force_login(self.doctor)
        make_range(self.test, sex=ReferenceSex.FEMALE, low=Decimal("1"), high=Decimal("10"), unit="U")
        make_range(self.test, sex=ReferenceSex.MALE, low=Decimal("100"), high=Decimal("200"), unit="U")
        response = self.client.get(self.evaluate_url(), {
            "lab_test": self.test.pk, "value_numeric": "5", "unit": "U",
        })
        self.assertContains(response, "Within range")

    def test_a_receptionist_is_refused(self):
        self.client.force_login(self.receptionist)
        response = self.client.get(self.evaluate_url(), {"lab_test": self.test.pk})
        self.assertEqual(response.status_code, 403)

    def test_an_unknown_patient_404s(self):
        self.client.force_login(self.doctor)
        url = reverse("doctor_lab_evaluate", args=["CEMH-NO-SUCH"])
        self.assertEqual(self.client.get(url).status_code, 404)


class TestTheInvestigationFormIntegration(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.client.force_login(self.doctor)
        self.patient = make_patient()
        visit = make_visit(self.patient, self.doctor, start=timezone.now())
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.IN_CABIN):
            visit.transition_to(status, by_user=self.doctor)
        self.test = LabTest.objects.get(code="LAB0213")

    def add_url(self):
        return reverse("doctor_add_record", args=[self.patient.patient_id, "investigation"])

    def test_the_test_name_field_is_wired_to_the_search_endpoint(self):
        response = self.client.get(self.add_url())
        self.assertContains(response, reverse("doctor_lab_test_search"))

    def test_the_value_field_is_wired_to_this_patients_own_evaluate_url(self):
        response = self.client.get(self.add_url())
        self.assertContains(response, reverse("doctor_lab_evaluate", args=[self.patient.patient_id]))

    def test_saving_with_a_picked_test_links_it(self):
        self.client.post(self.add_url(), {
            "lab_test": self.test.pk, "test_name": "TSH", "category": "OTHER",
            "performed_on": timezone.localdate().isoformat(),
            "value": "2.1", "value_numeric": "2.1", "unit": "mIU/L",
            "reference_range": "", "notes": "", "lab_name": "",
        })
        investigation = Investigation.objects.get()
        self.assertEqual(investigation.lab_test, self.test)

    def test_saving_with_no_matching_test_still_works(self):
        # The "doesn't have to match the master list" path — free text, no
        # lab_test at all, exactly as investigations worked before this.
        self.client.post(self.add_url(), {
            "test_name": "A test not on any master list", "category": "OTHER",
            "performed_on": timezone.localdate().isoformat(),
            "value": "Negative", "unit": "", "reference_range": "", "notes": "", "lab_name": "",
        })
        investigation = Investigation.objects.get()
        self.assertIsNone(investigation.lab_test)
        self.assertEqual(investigation.value, "Negative")
