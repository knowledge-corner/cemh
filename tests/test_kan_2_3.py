"""
KAN-2 — Today's Clinic stage board with six stages and doctor filter
KAN-3 — To Confirm to Confirmed via receptionist call confirmation

Written against the acceptance criteria in the two tickets. Much of both stories
already existed: the six columns, the day scoping, the count badges and the
Confirm action were built earlier. These cover what the tickets asked for that
was not there — the doctor filter, doctor-role visibility, the confirmation
stamp on the card, and the two edge cases the tickets call out by name.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import VisitStatus

from .factories import (
    later_today, make_doctor, make_patient, make_receptionist, make_visit,
)

#: The six stages, in the order KAN-2 FR-1 fixes them.
STAGES = [
    "To confirm", "Confirmed", "In the waiting room",
    "With the doctor", "Ready to bill", "Settled",
]


class TestTheBoardShape(TestCase):
    """KAN-2 AC-1, AC-4, AC-6 and FR-1, FR-2."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.doctor = make_doctor()

    def test_six_stages_appear_in_the_defined_order(self):
        body = self.client.get(reverse("reception_home")).content.decode()
        positions = [body.index(stage) for stage in STAGES]
        self.assertEqual(positions, sorted(positions), "stages are out of order")

    def test_the_filter_defaults_to_all_doctors(self):
        response = self.client.get(reverse("reception_home"))
        self.assertIsNone(response.context["chosen_doctor"])
        self.assertContains(response, "All doctors")

    def test_an_empty_stage_still_renders_its_column(self):
        # AC-4: a column must never collapse or disappear.
        body = self.client.get(reverse("reception_home")).content.decode()
        for stage in STAGES:
            self.assertIn(stage, body)

    def test_yesterdays_bookings_do_not_appear(self):
        patient = make_patient()
        make_visit(patient, self.doctor, start=timezone.now() - timedelta(days=1))
        response = self.client.get(reverse("reception_home"))
        self.assertEqual(response.context["total"], 0)

    def test_each_column_carries_a_count(self):
        make_visit(make_patient(), self.doctor, start=later_today())
        columns = {c["key"]: c["count"] for c in
                   self.client.get(reverse("reception_home")).context["columns"]}
        self.assertEqual(columns["to_confirm"], 1)
        self.assertEqual(columns["waiting"], 0)

    def test_a_card_names_the_patient_the_time_and_the_doctor(self):
        # FR-6.
        patient = make_patient()
        make_visit(patient, self.doctor, start=later_today())
        response = self.client.get(reverse("reception_home"))
        self.assertContains(response, patient.full_name)
        self.assertContains(response, self.doctor.display_name)
        self.assertContains(response, patient.patient_id)


class TestTheDoctorFilter(TestCase):
    """KAN-2 AC-2, AC-3 and FR-3, FR-4, FR-5."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.mine = make_doctor(username="drone", email="one@example.in")
        self.theirs = make_doctor(username="drtwo", email="two@example.in")

        self.my_patient = make_patient(first_name="Mine", phone="9820011111")
        self.their_patient = make_patient(first_name="Theirs", phone="9820022222")
        make_visit(self.my_patient, self.mine, start=later_today(1))
        make_visit(self.their_patient, self.theirs, start=later_today(2))

    def _board(self, doctor=None):
        params = {"doctor": doctor.pk} if doctor else {}
        return self.client.get(reverse("reception_home"), params)

    def test_all_doctors_are_offered_in_the_filter(self):
        response = self._board()
        self.assertContains(response, self.mine.display_name)
        self.assertContains(response, self.theirs.display_name)

    def test_choosing_a_doctor_narrows_every_column(self):
        response = self._board(self.mine)
        self.assertContains(response, self.my_patient.patient_id)
        self.assertNotContains(response, self.their_patient.patient_id)

    def test_the_counts_follow_the_filter(self):
        columns = {c["key"]: c["count"] for c in self._board(self.mine).context["columns"]}
        self.assertEqual(columns["to_confirm"], 1)

    def test_resetting_to_all_shows_everybody_again(self):
        response = self._board()
        self.assertContains(response, self.my_patient.patient_id)
        self.assertContains(response, self.their_patient.patient_id)

    def test_a_doctor_with_no_bookings_leaves_the_columns_empty_not_missing(self):
        idle = make_doctor(username="drthree", email="three@example.in")
        response = self._board(idle)
        self.assertEqual(response.context["total"], 0)
        for stage in STAGES:
            self.assertContains(response, stage)

    def test_an_unknown_doctor_id_falls_back_to_all(self):
        response = self.client.get(reverse("reception_home"), {"doctor": "99999"})
        self.assertIsNone(response.context["chosen_doctor"])
        self.assertContains(response, self.their_patient.patient_id)

    def test_the_filter_is_not_remembered_between_loads(self):
        # FR-3. A filter left on from yesterday is how a receptionist becomes
        # certain the clinic is empty while somebody sits in the waiting room.
        self._board(self.mine)
        self.assertIsNone(self._board().context["chosen_doctor"])

    def test_the_refresh_carries_the_filter(self):
        # Otherwise the board silently widens back to all doctors after 30s.
        response = self._board(self.mine)
        self.assertContains(response, f"doctor={self.mine.pk}")

    def test_the_polled_fragment_honours_the_filter_too(self):
        response = self.client.get(reverse("reception_board"), {"doctor": self.mine.pk})
        self.assertContains(response, self.my_patient.patient_id)
        self.assertNotContains(response, self.their_patient.patient_id)


class TestDoctorsCanReadTheBoard(TestCase):
    """KAN-2 AC-5 and FR-7 — and the line that still has to hold."""

    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.patient = make_patient()
        self.visit = make_visit(self.patient, self.doctor, start=later_today())
        self.visit.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
        self.visit.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)

    def test_a_doctor_sees_the_waiting_room_column(self):
        self.client.force_login(self.doctor)
        response = self.client.get(reverse("reception_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "In the waiting room")
        self.assertContains(response, self.patient.patient_id)

    def test_a_doctor_is_offered_no_stage_buttons(self):
        self.client.force_login(self.doctor)
        response = self.client.get(reverse("reception_home"))
        self.assertFalse(response.context["can_work_queue"])
        self.assertNotContains(response, "All bookings")

    def test_a_receptionist_is(self):
        # "Send to cabin" moved to the doctor under KAN-4 FR-3, so the marker
        # here is a stage action reception still owns.
        self.client.force_login(self.receptionist)
        response = self.client.get(reverse("reception_home"))
        self.assertTrue(response.context["can_work_queue"])
        self.assertContains(response, "All bookings")

    def test_a_doctor_cannot_move_a_visit_even_by_posting_directly(self):
        self.client.force_login(self.doctor)
        response = self.client.post(
            reverse("reception_move_visit", args=[self.visit.pk, VisitStatus.IN_CABIN]),
            headers={"HX-Request": "true"},
        )
        self.assertEqual(response.status_code, 403)


class TestConfirmingByTelephone(TestCase):
    """KAN-3 AC-1 to AC-4, and the edge cases the ticket names."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.doctor = make_doctor()
        self.patient = make_patient(phone="9820012345")
        self.visit = make_visit(self.patient, self.doctor, start=later_today())

    def _confirm(self):
        return self.client.post(
            reverse("reception_move_visit", args=[self.visit.pk, VisitStatus.CONFIRMED]),
            headers={"HX-Request": "true"},
        )

    def test_confirming_moves_the_booking_across(self):
        self._confirm()
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.CONFIRMED)

    def test_the_counts_move_with_it(self):
        self._confirm()
        columns = {c["key"]: c["count"] for c in
                   self.client.get(reverse("reception_home")).context["columns"]}
        self.assertEqual(columns["to_confirm"], 0)
        self.assertEqual(columns["confirmed"], 1)

    def test_the_number_to_ring_is_on_the_card_before_confirming(self):
        # AC-4 — visible without opening the booking.
        response = self.client.get(reverse("reception_home"))
        self.assertContains(response, f"tel:{self.patient.contact_phone}")

    def test_who_confirmed_and_when_is_recorded(self):
        self._confirm()
        self.visit.refresh_from_db()
        event = self.visit.confirmation
        self.assertIsNotNone(event)
        self.assertEqual(event.changed_by, self.receptionist)

    def test_who_confirmed_and_when_is_shown_on_the_card(self):
        # AC-2 — recorded is not the same as visible.
        self._confirm()
        response = self.client.get(reverse("reception_home"))
        self.assertContains(response, "Confirmed")
        self.assertContains(response, self.receptionist.display_name)

    def test_a_confirmed_booking_offers_no_second_confirm(self):
        # AC-3.
        self._confirm()
        response = self.client.get(reverse("reception_home"))
        self.assertNotContains(response, "Confirmed by phone")

    def test_confirming_twice_reports_it_plainly_rather_than_failing(self):
        # Two receptionists both did the right thing; one was simply second.
        self._confirm()
        response = self.client.post(
            reverse("reception_move_visit", args=[self.visit.pk, VisitStatus.CONFIRMED]),
            follow=True,
        )
        self.assertContains(response, "was already confirmed")
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.CONFIRMED)

    def test_confirming_twice_records_only_one_confirmation(self):
        self._confirm()
        self.client.post(
            reverse("reception_move_visit", args=[self.visit.pk, VisitStatus.CONFIRMED])
        )
        self.assertEqual(
            self.visit.status_events.filter(to_status=VisitStatus.CONFIRMED).count(), 1
        )

    def test_a_patient_with_no_number_can_still_be_confirmed(self):
        # The ticket is explicit: say so on the card, but do not block.
        self.patient.phone = ""
        self.patient.guardian_phone = ""
        self.patient.save()

        response = self.client.get(reverse("reception_home"))
        self.assertContains(response, "No contact number")

        self._confirm()
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.CONFIRMED)
