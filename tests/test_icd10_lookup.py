"""
ICD-10 code lookup for the diagnosis form.

Covers that the reference table loaded correctly from the WHO data, that the
search endpoint behind the diagnosis form's autocomplete matches sensibly and
is doctor-only, and that the form still saves a diagnosis with no code at all
— picking a suggestion is help, never a requirement.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import VisitStatus
from clinical.models import Diagnosis, ICD10Code

from .factories import make_doctor, make_patient, make_receptionist, make_visit


class TestTheCodesLoaded(TestCase):
    """Sanity checks on the data migration, not the view."""

    def test_the_full_list_loaded(self):
        # 11,243 category-level codes in the bundled 2019 WHO extract.
        self.assertEqual(ICD10Code.objects.count(), 11243)

    def test_a_familiar_code_is_present_with_its_description(self):
        code = ICD10Code.objects.get(code="E11")
        self.assertEqual(code.description, "Type 2 diabetes mellitus")

    def test_codes_are_unique(self):
        self.assertEqual(
            ICD10Code.objects.count(),
            ICD10Code.objects.values("code").distinct().count(),
        )


class ICD10SearchTestCase(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()

    def search(self, query):
        return self.client.get(reverse("doctor_icd10_search"), {"q": query})


class TestTheSearchEndpoint(ICD10SearchTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.doctor)

    def test_a_short_query_returns_nothing(self):
        response = self.search("e")
        self.assertNotContains(response, "picklist__item")

    def test_matching_by_code_prefix(self):
        response = self.search("E11")
        self.assertContains(response, "Type 2 diabetes mellitus")

    def test_matching_by_description_is_case_insensitive(self):
        response = self.search("HYPOTHYROIDISM")
        self.assertContains(response, "E03")

    def test_results_are_capped_at_ten(self):
        # "E1" alone matches far more than ten codes (E10-E16 and their
        # subcategories) — the point of the cap.
        response = self.search("E1")
        self.assertEqual(response.content.count(b'class="picklist__item"'), 10)

    def test_no_match_says_so_rather_than_nothing(self):
        response = self.search("zzzznotarealcondition")
        self.assertContains(response, "No ICD-10 match")

    def test_reads_the_htmx_param_name_the_widget_actually_sends(self):
        # The diagnosis form's description input isn't a dedicated search
        # box — htmx sends the query under the field's own name,
        # "description", not "q". See DiagnosisForm's description widget.
        response = self.client.get(
            reverse("doctor_icd10_search"), {"description": "E11"}
        )
        self.assertContains(response, "Type 2 diabetes mellitus")


class TestOnlyADoctorCanSearch(ICD10SearchTestCase):
    def test_a_receptionist_is_refused(self):
        self.client.force_login(self.receptionist)
        self.assertEqual(self.search("E11").status_code, 403)

    def test_an_anonymous_request_is_refused(self):
        self.assertNotEqual(self.search("E11").status_code, 200)


class TestTheDiagnosisFormCarriesTheAutocomplete(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.client.force_login(self.doctor)
        self.patient = make_patient()
        visit = make_visit(self.patient, self.doctor, start=timezone.now())
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.IN_CABIN):
            visit.transition_to(status, by_user=self.doctor)

    def add_url(self, kind):
        return reverse("doctor_add_record", args=[self.patient.patient_id, kind])

    def test_the_description_field_is_wired_to_the_search_endpoint(self):
        response = self.client.get(self.add_url("diagnosis"))
        self.assertContains(response, reverse("doctor_icd10_search"))
        self.assertContains(response, 'id="icd10-suggestions"')

    def test_other_record_types_do_not_carry_the_suggestions_box(self):
        # icd10-suggestions is specific to the diagnosis form — the generic
        # modal template only adds it when kind == "diagnosis".
        response = self.client.get(self.add_url("investigation"))
        self.assertNotContains(response, "icd10-suggestions")

    def test_a_diagnosis_saves_with_a_selected_code(self):
        self.client.post(self.add_url("diagnosis"), {
            "description": "Type 2 diabetes mellitus", "icd10_code": "E11",
            "status": Diagnosis.Status.ACTIVE,
            "diagnosed_on": timezone.localdate().isoformat(),
            "notes": "", "resolved_on": "",
        })
        diagnosis = Diagnosis.objects.get()
        self.assertEqual(diagnosis.icd10_code, "E11")

    def test_a_diagnosis_still_saves_with_no_code_at_all(self):
        # The "add new" path: a description that matched nothing (or that
        # the doctor just didn't pick a suggestion for) is not an error.
        self.client.post(self.add_url("diagnosis"), {
            "description": "A condition not in the WHO list",
            "icd10_code": "",
            "status": Diagnosis.Status.ACTIVE,
            "diagnosed_on": timezone.localdate().isoformat(),
            "notes": "", "resolved_on": "",
        })
        diagnosis = Diagnosis.objects.get()
        self.assertEqual(diagnosis.icd10_code, "")
        self.assertEqual(diagnosis.description, "A condition not in the WHO list")
