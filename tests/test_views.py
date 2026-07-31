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
    make_receptionist, make_user, make_visit,
)

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
        visit = make_visit(patient, self.doctor, start=timezone.now() + timedelta(minutes=30))
        visit.transition_to(VisitStatus.CONFIRMED, by_user=self.doctor)
        visit.transition_to(VisitStatus.ARRIVED, by_user=self.doctor)

        response = self.client.get(reverse("doctor_home"))
        self.assertContains(response, patient.patient_id)

    def test_another_doctors_patient_is_not_in_my_queue(self):
        other = make_doctor(username="dr2", email="dr2@example.in")
        patient = make_patient()
        make_visit(patient, other, start=timezone.now() + timedelta(minutes=30))

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
