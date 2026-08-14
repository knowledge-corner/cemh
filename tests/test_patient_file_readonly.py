"""
"Open" is a glance, from the queue or from search; "Send in" and "Start
consultation" are what unlock the clinical record proper.

The chart used to be entirely read-only until the visit reached IN_CABIN.
That is no longer true for most of it — a patient's own details, problem
list, investigation results, growth measurements and reference letters are
reference data a doctor may reasonably need to correct at any time, sent in
or not. Only the clinical record of *this* consultation — clinical notes and
prescriptions — stays locked to the IN_CABIN window: the edit buttons for
those are hidden, and the save endpoints refuse the write even if hit
directly, right up until "Send in" or "Start consultation" moves the visit
to IN_CABIN, and locks again once the consultation ends.
"""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import VisitStatus
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

    def add_note_url(self):
        return reverse("doctor_add_record", args=[self.patient.patient_id, "note"])

    def _diagnosis_payload(self, **overrides):
        payload = {
            "description": "Thyroid disorders in children",
            "status": Diagnosis.Status.ACTIVE,
            "diagnosed_on": timezone.localdate().isoformat(),
            "icd10_code": "", "notes": "", "resolved_on": "",
        }
        payload.update(overrides)
        return payload

    def _note_payload(self, **overrides):
        payload = {"clinical_notes": "Should not save", "prescription_note": ""}
        payload.update(overrides)
        return payload

    def _arrived_visit(self):
        visit = make_visit(self.patient, self.doctor, start=timezone.now())
        visit.transition_to(VisitStatus.CONFIRMED, by_user=self.doctor)
        visit.transition_to(VisitStatus.ARRIVED, by_user=self.doctor)
        return visit

    def _sent_in_visit(self):
        visit = self._arrived_visit()
        visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        return visit


class TestOpeningAnArrivedPatientLocksNotesAndPrescriptionsOnly(ReadOnlyTestCase):
    def test_the_dashboard_shows_the_locked_notice(self):
        self._arrived_visit()
        response = self.client.get(self.dashboard_url())
        self.assertContains(response, "Clinical notes and prescriptions are locked")

    def test_the_note_edit_button_is_not_rendered(self):
        self._arrived_visit()
        response = self.client.get(
            reverse("doctor_patient_tab", args=[self.patient.patient_id, "notes"])
        )
        self.assertNotContains(response, "btn-edit")

    def test_the_add_note_endpoint_refuses_the_form(self):
        self._arrived_visit()
        response = self.client.get(self.add_note_url())
        self.assertEqual(response.status_code, 403)

    def test_the_add_note_endpoint_refuses_a_direct_save(self):
        self._arrived_visit()
        response = self.client.post(self.add_note_url(), self._note_payload())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.patient.notes.exists())

    def test_the_summary_tab_is_still_editable(self):
        # The whole point of the change: an ARRIVED-but-not-sent-in patient
        # is not the same as a read-only one any more.
        self._arrived_visit()
        response = self.client.get(self.dashboard_url())
        self.assertContains(response, "btn-edit")

    def test_a_diagnosis_can_still_be_saved(self):
        self._arrived_visit()
        response = self.client.post(self.add_diagnosis_url(), self._diagnosis_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Diagnosis.objects.get().patient, self.patient)


class TestSendingInUnlocksClinicalNotesAndPrescriptions(ReadOnlyTestCase):
    def test_the_dashboard_has_no_locked_notice(self):
        self._sent_in_visit()
        response = self.client.get(self.dashboard_url())
        self.assertNotContains(response, "Clinical notes and prescriptions are locked")

    def test_the_note_edit_button_is_rendered(self):
        self._sent_in_visit()
        response = self.client.get(
            reverse("doctor_patient_tab", args=[self.patient.patient_id, "notes"])
        )
        self.assertContains(response, "btn-edit")

    def test_a_note_can_be_saved(self):
        self._sent_in_visit()
        response = self.client.post(self.add_note_url(), self._note_payload(
            clinical_notes="Growth tracking well", prescription_note="",
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.patient.notes.get().patient, self.patient)

    def test_a_diagnosis_can_be_saved(self):
        self._sent_in_visit()
        response = self.client.post(self.add_diagnosis_url(), self._diagnosis_payload())
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
        self.assertNotContains(dashboard, "Clinical notes and prescriptions are locked")


class TestAPatientWithNoActiveVisitCanStillHaveTheSummaryEdited(ReadOnlyTestCase):
    """
    A doctor reaching a chart through search, with no visit open at all,
    sees the same locked notice for clinical notes and prescriptions as
    "Open" from the queue — only "Send in" and "Start consultation" ever
    unlock those. Everything else on the chart is still editable regardless.
    """

    def test_the_dashboard_shows_the_locked_notice(self):
        response = self.client.get(self.dashboard_url())
        self.assertContains(response, "Clinical notes and prescriptions are locked")

    def test_a_note_cannot_be_saved(self):
        response = self.client.post(self.add_note_url(), self._note_payload())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.patient.notes.exists())

    def test_a_diagnosis_can_still_be_saved(self):
        response = self.client.post(self.add_diagnosis_url(), self._diagnosis_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Diagnosis.objects.get().patient, self.patient)

    def test_the_start_consultation_button_is_offered_instead(self):
        response = self.client.get(self.dashboard_url())
        self.assertContains(response, "Start consultation")
