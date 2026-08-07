"""
The doctor's Analytics tab: a filtered CSV download and a dashboard built
from the same filters, for a doctor researching her own patient population by
condition, age, gender, doctor or specialisation.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import DoctorProfile, Specialisation
from audit.models import AccessLog, AuditAction
from clinical.models import Diagnosis
from patients.models import Patient, Sex

from .factories import make_doctor, make_patient, make_receptionist, make_visit, today_at


class AnalyticsTestCase(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.client.force_login(self.doctor)

    def _dashboard(self, **params):
        return self.client.get(reverse("doctor_analytics"), {"tab": "dashboard", **params})

    def _download(self, **params):
        return self.client.get(reverse("doctor_analytics"), {"tab": "download", **params})

    def _export(self, **params):
        return self.client.get(reverse("doctor_analytics_export"), params)


class TestAccessToAnalytics(AnalyticsTestCase):
    def test_a_doctor_can_open_it(self):
        self.assertEqual(self._dashboard().status_code, 200)

    def test_a_receptionist_cannot(self):
        self.client.force_login(self.receptionist)
        self.assertEqual(self._dashboard().status_code, 403)

    def test_a_receptionist_cannot_export_either(self):
        self.client.force_login(self.receptionist)
        self.assertEqual(self._export().status_code, 403)

    def test_the_nav_bar_offers_analytics(self):
        response = self.client.get(reverse("doctor_home"))
        self.assertContains(response, reverse("doctor_analytics"))

    def test_an_unrecognised_tab_falls_back_to_the_dashboard(self):
        response = self.client.get(reverse("doctor_analytics"), {"tab": "nonsense"})
        self.assertContains(response, "Patients matching filters")


class TestTheUnfilteredView(AnalyticsTestCase):
    def setUp(self):
        super().setUp()
        make_patient(first_name="Aarav", phone="9820000101")
        make_patient(first_name="Bina", phone="9820000102", sex=Sex.FEMALE)

    def test_dashboard_counts_every_patient_with_no_filters(self):
        response = self._dashboard()
        self.assertEqual(response.context["patient_count"], 2)
        self.assertEqual(response.context["total_patients"], 2)

    def test_download_counts_every_patient_with_no_filters(self):
        response = self._download()
        self.assertEqual(response.context["download_count"], 2)


class TestGenderAndAgeFilters(AnalyticsTestCase):
    def setUp(self):
        super().setUp()
        self.child = make_patient(
            first_name="ChildOne", phone="9820000201", sex=Sex.MALE,
            date_of_birth=timezone.localdate() - timedelta(days=int(8 * 365.25)),
        )
        self.adult = make_patient(
            first_name="AdultOne", phone="9820000202", sex=Sex.FEMALE,
            date_of_birth=timezone.localdate() - timedelta(days=int(45 * 365.25)),
        )

    def test_filtering_by_gender(self):
        response = self._dashboard(sex=Sex.FEMALE)
        self.assertEqual(response.context["patient_count"], 1)

    def test_filtering_by_minimum_age_excludes_the_child(self):
        response = self._dashboard(age_min=18)
        self.assertEqual(response.context["patient_count"], 1)

    def test_filtering_by_maximum_age_excludes_the_adult(self):
        response = self._dashboard(age_max=17)
        self.assertEqual(response.context["patient_count"], 1)

    def test_an_age_band_can_exclude_everybody(self):
        response = self._dashboard(age_min=20, age_max=30)
        self.assertEqual(response.context["patient_count"], 0)


class TestConditionAndStatusFilters(AnalyticsTestCase):
    def setUp(self):
        super().setUp()
        self.thyroid_patient = make_patient(first_name="Thy", phone="9820000301")
        Diagnosis.objects.create(
            patient=self.thyroid_patient, description="Hypothyroidism",
            status=Diagnosis.Status.ACTIVE,
        )
        self.mixed_patient = make_patient(first_name="Mix", phone="9820000302")
        Diagnosis.objects.create(
            patient=self.mixed_patient, description="Hypothyroidism",
            status=Diagnosis.Status.ACTIVE,
        )
        Diagnosis.objects.create(
            patient=self.mixed_patient, description="Diabetes",
            status=Diagnosis.Status.RESOLVED,
        )
        self.unrelated_patient = make_patient(first_name="Un", phone="9820000303")
        Diagnosis.objects.create(
            patient=self.unrelated_patient, description="Migraine",
            status=Diagnosis.Status.ACTIVE,
        )

    def test_matching_by_condition_text_is_case_insensitive(self):
        response = self._dashboard(condition="THYROID")
        self.assertEqual(response.context["patient_count"], 2)

    def test_combining_condition_and_status_matches_the_same_diagnosis(self):
        # mixed_patient's Hypothyroidism is ACTIVE and their Diabetes is
        # RESOLVED — no single diagnosis is both "thyroid" and RESOLVED, so
        # this must not match, even though each half matches something.
        response = self._dashboard(condition="thyroid", diagnosis_status=Diagnosis.Status.RESOLVED)
        self.assertEqual(response.context["patient_count"], 0)

    def test_combining_condition_and_status_when_they_do_agree(self):
        response = self._dashboard(condition="thyroid", diagnosis_status=Diagnosis.Status.ACTIVE)
        self.assertEqual(response.context["patient_count"], 2)

    def test_top_conditions_are_counted_across_the_filtered_set(self):
        response = self._dashboard()
        labels = {row["label"]: row["count"] for row in response.context["top_conditions"]}
        self.assertEqual(labels["Hypothyroidism"], 2)
        self.assertEqual(labels["Migraine"], 1)


class TestDoctorAndSpecialisationFilters(AnalyticsTestCase):
    def setUp(self):
        super().setUp()
        self.endo = Specialisation.objects.create(name="Test Endocrine Research")
        self.cardio = Specialisation.objects.create(name="Test Cardiology Research")
        DoctorProfile.objects.create(user=self.doctor, specialisation=self.endo)

        self.other_doctor = make_doctor(username="drother", email="other@example.in")
        DoctorProfile.objects.create(user=self.other_doctor, specialisation=self.cardio)

        self.my_patient = make_patient(first_name="Mine", phone="9820000401")
        make_visit(self.my_patient, self.doctor, start=today_at(10))

        self.their_patient = make_patient(first_name="Theirs", phone="9820000402")
        make_visit(self.their_patient, self.other_doctor, start=today_at(11))

    def test_filtering_by_doctor(self):
        response = self._dashboard(doctor=self.doctor.pk)
        self.assertEqual(response.context["patient_count"], 1)

    def test_filtering_by_specialisation(self):
        response = self._dashboard(specialisation=self.cardio.pk)
        self.assertEqual(response.context["patient_count"], 1)
        self.assertEqual(list(response.context["visit_outcomes"])[0]["count"], 1)


class TestDateRangeFilter(AnalyticsTestCase):
    def setUp(self):
        super().setUp()
        self.recent_patient = make_patient(first_name="Recent", phone="9820000501")
        make_visit(self.recent_patient, self.doctor, start=today_at(9))

        self.old_patient = make_patient(first_name="Old", phone="9820000502")
        make_visit(
            self.old_patient, self.doctor,
            start=timezone.now() - timedelta(days=400),
        )

    def test_a_date_range_excludes_visits_outside_it(self):
        today = timezone.localdate()
        response = self._dashboard(
            date_from=(today - timedelta(days=1)).isoformat(),
            date_to=(today + timedelta(days=1)).isoformat(),
        )
        self.assertEqual(response.context["patient_count"], 1)


class TestVisitOutcomeBreakdown(AnalyticsTestCase):
    def test_visit_statuses_are_counted(self):
        patient = make_patient(first_name="Stat", phone="9820000601")
        visit = make_visit(patient, self.doctor, start=today_at(9))
        visit.transition_to("CONFIRMED")
        visit.transition_to("ARRIVED")

        response = self._dashboard()
        outcomes = {row["label"]: row["count"] for row in response.context["visit_outcomes"]}
        self.assertEqual(outcomes.get("Arrived"), 1)


class TestTheCsvExport(AnalyticsTestCase):
    def setUp(self):
        super().setUp()
        self.patient = make_patient(first_name="Downloadable", phone="9820000701")
        Diagnosis.objects.create(
            patient=self.patient, description="Hypothyroidism",
            status=Diagnosis.Status.ACTIVE,
        )
        make_patient(first_name="ExcludedByFilter", phone="9820000702")

    def test_it_downloads_a_csv(self):
        response = self._export()
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])

    def test_only_the_filtered_patients_are_rows(self):
        response = self._export(condition="thyroid")
        body = response.content.decode()
        self.assertIn("Downloadable", body)
        self.assertNotIn("ExcludedByFilter", body)

    def test_the_header_row_names_the_columns(self):
        response = self._export()
        first_line = response.content.decode().splitlines()[0]
        self.assertIn("Gender", first_line)
        self.assertIn("Active diagnoses", first_line)

    def test_exporting_is_audited(self):
        self._export()
        entry = AccessLog.objects.filter(action=AuditAction.PRINT).get()
        self.assertEqual(entry.username, self.doctor.username)
