"""
"Open" is a glance; "Send in" is what unlocks the file.

A patient waiting on the queue (ARRIVED, not yet sent in) has a chart that is
read-only when the doctor opens it — the edit buttons are hidden, and the
save endpoints refuse the write even if hit directly. The moment "Send in"
moves the visit to IN_CABIN, the very same URL becomes fully editable again.
"""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import Visit, VisitStatus
from clinical.models import Diagnosis

from .factories import make_doctor, make_patient, make_visit


class ReadOnlyTestCase(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.client.force_login(self.doctor)
        self.patient = make_patient()

    def dashboard_url(self):
        return reverse("doctor_patient_dashboard", args=[self.patient.patient_id])

    def add_diagnosis_url(self):
        return reverse("doctor_add_record", args=[self.patient.patient_id, "diagnosis"])

    def _arrived_visit(self):
        visit = make_visit(self.patient, self.doctor, start=timezone.now())
        visit.transition_to(VisitStatus.CONFIRMED, by_user=self.doctor)
        visit.transition_to(VisitStatus.ARRIVED, by_user=self.doctor)
        return visit

    def _sent_in_visit(self):
        visit = self._arrived_visit()
        visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        return visit


class TestOpeningAnArrivedPatientIsReadOnly(ReadOnlyTestCase):
    def test_the_dashboard_shows_a_read_only_notice(self):
        self._arrived_visit()
        response = self.client.get(self.dashboard_url())
        self.assertContains(response, "Read-only")

    def test_edit_buttons_are_not_rendered(self):
        self._arrived_visit()
        response = self.client.get(self.dashboard_url())
        self.assertNotContains(response, "btn-edit")

    def test_the_add_record_endpoint_refuses_the_form(self):
        self._arrived_visit()
        response = self.client.get(self.add_diagnosis_url())
        self.assertEqual(response.status_code, 403)

    def test_the_add_record_endpoint_refuses_a_direct_save(self):
        self._arrived_visit()
        response = self.client.post(self.add_diagnosis_url(), {
            "description": "Should not save",
            "status": Diagnosis.Status.ACTIVE,
            "diagnosed_on": timezone.localdate().isoformat(),
            "icd10_code": "", "notes": "", "resolved_on": "",
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Diagnosis.objects.exists())


class TestSendingInUnlocksTheFile(ReadOnlyTestCase):
    def test_the_dashboard_has_no_read_only_notice(self):
        self._sent_in_visit()
        response = self.client.get(self.dashboard_url())
        self.assertNotContains(response, "Read-only")

    def test_edit_buttons_are_rendered(self):
        self._sent_in_visit()
        response = self.client.get(self.dashboard_url())
        self.assertContains(response, "btn-edit")

    def test_a_record_can_be_saved(self):
        self._sent_in_visit()
        response = self.client.post(self.add_diagnosis_url(), {
            "description": "Thyroid disorders in children",
            "status": Diagnosis.Status.ACTIVE,
            "diagnosed_on": timezone.localdate().isoformat(),
            "icd10_code": "", "notes": "", "resolved_on": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Diagnosis.objects.get().patient, self.patient)

    def test_the_send_in_button_actually_reaches_this_state(self):
        # End to end: the same POST the queue's "Send in" button issues.
        visit = self._arrived_visit()
        response = self.client.post(
            reverse("doctor_send_for_patient", args=[visit.pk])
        )
        self.assertRedirects(response, self.dashboard_url())
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.IN_CABIN)
        dashboard = self.client.get(self.dashboard_url())
        self.assertNotContains(dashboard, "Read-only")


class TestAPatientWithNoActiveVisitIsUnaffected(ReadOnlyTestCase):
    """
    A doctor reaching a chart through search, not the queue, must not lose
    the ability to write in it — this feature only locks the file for a
    patient who is specifically waiting, not sent in yet.
    """

    def test_the_dashboard_has_no_read_only_notice(self):
        response = self.client.get(self.dashboard_url())
        self.assertNotContains(response, "Read-only")

    def test_a_record_can_still_be_saved(self):
        response = self.client.post(self.add_diagnosis_url(), {
            "description": "Thyroid disorders in children",
            "status": Diagnosis.Status.ACTIVE,
            "diagnosed_on": timezone.localdate().isoformat(),
            "icd10_code": "", "notes": "", "resolved_on": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Diagnosis.objects.count(), 1)
