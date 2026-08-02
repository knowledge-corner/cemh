"""
Login, role routing and the doctor's dashboard.

The access-control tests matter most: they prove restrictions live in the view
layer, not merely in whether a template draws a link.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import VisitStatus
from audit.models import AccessLog, AuditAction

from .factories import (
    make_adult_patient, make_doctor, make_history, make_measurement, make_patient,
    make_receptionist, make_user, make_visit, today_at,
)
from .test_growth_reference import _clinic_with

PASSWORD = "testpass12345"


class TestLogin(TestCase):
    def setUp(self):
        self.doctor = make_doctor()

    def test_login_page_renders(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign in")

    def test_valid_credentials_sign_the_user_in(self):
        response = self.client.post(
            reverse("login"),
            {"username": self.doctor.username, "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 302)

    def test_invalid_credentials_are_rejected(self):
        response = self.client.post(
            reverse("login"), {"username": self.doctor.username, "password": "wrong"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_successful_login_is_recorded_in_the_audit_trail(self):
        self.client.post(
            reverse("login"), {"username": self.doctor.username, "password": PASSWORD}
        )
        self.assertTrue(AccessLog.objects.filter(action=AuditAction.LOGIN).exists())

    def test_failed_login_is_recorded_without_the_password(self):
        self.client.post(
            reverse("login"), {"username": self.doctor.username, "password": "hunter2"}
        )
        entry = AccessLog.objects.get(action=AuditAction.LOGIN_FAILED)
        self.assertEqual(entry.username, self.doctor.username)
        self.assertNotIn("hunter2", entry.description)


class TestRoleRouting(TestCase):
    def test_doctor_lands_on_the_doctor_home(self):
        self.client.force_login(make_doctor())
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("doctor_home"))

    def test_receptionist_lands_on_the_reception_home(self):
        self.client.force_login(make_receptionist())
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("reception_home"))

    def test_patient_account_lands_on_the_public_page(self):
        # There is no patient portal, so a patient-role account has nothing to
        # sign in for and is sent to the clinic's public page.
        self.client.force_login(make_user())
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("website_home"))

    def test_anonymous_visitor_is_sent_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class TestDoctorAccessControl(TestCase):
    """Role checks must be enforced by the view, not by hiding links."""

    def setUp(self):
        self.doctor = make_doctor()
        self.patient = make_patient()

    def test_receptionist_is_forbidden_from_the_doctor_home(self):
        self.client.force_login(make_receptionist())
        self.assertEqual(self.client.get(reverse("doctor_home")).status_code, 403)

    def test_receptionist_is_forbidden_from_a_patient_chart(self):
        self.client.force_login(make_receptionist())
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[self.patient.patient_id])
        )
        self.assertEqual(response.status_code, 403)

    def test_patient_role_is_forbidden_from_a_patient_chart(self):
        self.client.force_login(make_user())
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[self.patient.patient_id])
        )
        self.assertEqual(response.status_code, 403)

    def test_anonymous_visitor_is_redirected_not_shown_the_chart(self):
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[self.patient.patient_id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_doctor_may_open_the_chart(self):
        self.client.force_login(self.doctor)
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[self.patient.patient_id])
        )
        self.assertEqual(response.status_code, 200)


class TestDoctorHome(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.client.force_login(self.doctor)

    def test_queue_shows_a_patient_arrived_today(self):
        patient = make_patient()
        # A fixed hour, not an offset: half an hour from 23:51 is tomorrow,
        # and the doctor's queue is today's.
        visit = make_visit(patient, self.doctor, start=today_at(10))
        visit.transition_to(VisitStatus.CONFIRMED, by_user=self.doctor)
        visit.transition_to(VisitStatus.ARRIVED, by_user=self.doctor)

        response = self.client.get(reverse("doctor_home"))
        self.assertContains(response, patient.patient_id)

    def test_another_doctors_patient_is_not_in_my_queue(self):
        other = make_doctor(username="dr2", email="dr2@example.in")
        patient = make_patient()
        make_visit(patient, other, start=today_at(10))

        response = self.client.get(reverse("doctor_home"))
        self.assertNotContains(response, patient.patient_id)

    def test_searching_a_uhid_opens_that_chart(self):
        patient = make_patient()
        response = self.client.get(reverse("doctor_home"), {"patient_id": patient.patient_id})
        self.assertRedirects(
            response, reverse("doctor_patient_dashboard", args=[patient.patient_id])
        )

    def test_uhid_search_is_case_insensitive(self):
        patient = make_patient()
        response = self.client.get(
            reverse("doctor_home"), {"patient_id": patient.patient_id.lower()}
        )
        self.assertEqual(response.status_code, 302)

    def test_searching_a_mobile_number_opens_that_chart(self):
        patient = make_patient(phone="9820098765")
        response = self.client.get(reverse("doctor_home"), {"patient_id": "9820098765"})
        self.assertRedirects(
            response, reverse("doctor_patient_dashboard", args=[patient.patient_id])
        )

    def test_unknown_uhid_reports_an_error_rather_than_failing(self):
        response = self.client.get(reverse("doctor_home"), {"patient_id": "CEMH-99-99999"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No patient found")


class TestPatientDashboard(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.client.force_login(self.doctor)
        self.patient = make_patient()
        make_history(self.patient, allergies="Sulfa drugs — rash")

    def test_dashboard_shows_identity_and_uhid(self):
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[self.patient.patient_id])
        )
        self.assertContains(response, self.patient.full_name)
        self.assertContains(response, self.patient.patient_id)

    def test_allergies_are_surfaced_on_the_sidebar(self):
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[self.patient.patient_id])
        )
        self.assertContains(response, "Sulfa drugs")

    def test_unknown_patient_returns_404(self):
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=["CEMH-99-00000"])
        )
        self.assertEqual(response.status_code, 404)

    def test_opening_a_chart_is_written_to_the_audit_trail(self):
        self.client.get(reverse("doctor_patient_dashboard", args=[self.patient.patient_id]))
        entry = AccessLog.objects.get(action=AuditAction.VIEW)
        self.assertEqual(entry.patient_id_ref, self.patient.patient_id)
        self.assertEqual(entry.username, self.doctor.username)

    def test_paediatric_patient_is_offered_the_growth_tab(self):
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[self.patient.patient_id])
        )
        self.assertContains(response, "Growth Chart")

    def test_adult_patient_is_not_offered_the_growth_tab(self):
        adult = make_adult_patient()
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[adult.patient_id])
        )
        self.assertNotContains(response, "Growth Chart")


class TestDashboardTabs(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.client.force_login(self.doctor)
        self.patient = make_patient()

    def _tab(self, name, patient=None):
        return self.client.get(
            reverse("doctor_patient_tab", args=[(patient or self.patient).patient_id, name])
        )

    def test_every_tab_renders(self):
        for name in ("summary", "notes", "investigations", "growth", "prescriptions"):
            self.assertEqual(self._tab(name).status_code, 200, f"{name} tab failed")

    def test_unknown_tab_returns_404(self):
        self.assertEqual(self._tab("nonsense").status_code, 404)

    def test_growth_tab_is_unavailable_for_an_adult(self):
        self.assertEqual(self._tab("growth", patient=make_adult_patient()).status_code, 404)

    def test_growth_tab_plots_a_recorded_measurement(self):
        make_measurement(self.patient, height_cm=Decimal("123.1"), weight_kg=Decimal("23.6"))
        response = self._tab("growth")
        self.assertContains(response, "123.1")
        self.assertContains(response, "centile")

    def test_growth_tab_without_measurements_shows_an_empty_state(self):
        self.assertContains(self._tab("growth"), "No measurements recorded")

    def test_tab_requires_the_doctor_role(self):
        self.client.force_login(make_receptionist())
        self.assertEqual(self._tab("summary").status_code, 403)


class TestGrowthTabShowsWhichReferenceAnswered(TestCase):
    """
    The chart must say what kind of number it is showing. A band read off the
    IAP tables and an exact centile computed from WHO's LMS parameters look
    alike on screen unless the page distinguishes them.
    """

    def setUp(self):
        self.doctor = make_doctor()
        self.client.force_login(self.doctor)
        # Nine years old, so charted against IAP.
        self.patient = make_patient()

    def _growth(self, patient=None):
        return self.client.get(
            reverse("doctor_patient_tab",
                    args=[(patient or self.patient).patient_id, "growth"])
        )

    def test_a_school_age_child_is_shown_a_band_and_an_sds(self):
        make_measurement(self.patient, height_cm=Decimal("128.4"),
                         weight_kg=Decimal("26.0"))
        response = self._growth()
        self.assertContains(response, "centile")
        self.assertContains(response, "SDS")
        self.assertContains(response, "IAP")

    def test_the_bmi_status_is_shown_with_its_cutoff(self):
        # 1.28 m and 45 kg is a BMI of 27.5, well past the 27-equivalent line
        # for a nine-year-old boy.
        make_measurement(self.patient, height_cm=Decimal("128.0"),
                         weight_kg=Decimal("45.0"))
        response = self._growth()
        self.assertContains(response, "BMI status")
        self.assertContains(response, "Obesity")

    def test_a_child_below_the_third_centile_gets_a_labelled_companion(self):
        make_measurement(self.patient, height_cm=Decimal("105.0"),
                         weight_kg=Decimal("18.0"))
        response = self._growth()
        self.assertContains(response, "below the 3rd centile")
        # No IAP number exists off the printed scale, so another reference is
        # named rather than the gap being left silent.
        self.assertContains(response, "Off the printed scale")

    def test_an_under_five_still_shows_an_exact_centile_and_z_score(self):
        toddler = make_patient(
            first_name="Ira", phone="9820055555",
            date_of_birth=timezone.localdate() - timedelta(days=int(3 * 365.25)),
        )
        make_measurement(toddler, height_cm=Decimal("95.0"), weight_kg=Decimal("14.0"))
        response = self._growth(toddler)
        self.assertContains(response, "z = ")
        self.assertContains(response, "WHO")

    def test_no_bmi_verdict_for_a_clinic_charting_against_who(self):
        make_measurement(self.patient, height_cm=Decimal("128.0"),
                         weight_kg=Decimal("45.0"))
        with self.settings(CLINIC=_clinic_with(GROWTH_REFERENCE="WHO")):
            response = self._growth()
        self.assertNotContains(response, "BMI status")
