"""
KAN-4 to KAN-10 — the rest of the Today's Clinic epic.

Written against the acceptance criteria in each ticket. As with KAN-2 and KAN-3,
a good deal already existed; these cover the behaviour the tickets asked for
that was not there, and pin the rules the tickets are explicit about.

KAN-7 (email and WhatsApp delivery) has no tests here. It is genuinely blocked —
see the comment on the ticket.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from appointments.models import BACKWARD_TRANSITIONS, InvalidTransition, VisitStatus
from billing.models import Charge, Payment, Receipt

from .factories import (
    later_today, make_doctor, make_patient, make_receptionist, make_visit,
)


def _arrived(patient, doctor, by, hours=1):
    """
    A patient who has arrived and is waiting.

    ``hours`` must differ per patient for the same doctor: the double-booking
    exclusion constraint refuses two overlapping visits, which is exactly what
    it is for.
    """
    visit = make_visit(patient, doctor, start=later_today(hours))
    visit.transition_to(VisitStatus.CONFIRMED, by_user=by)
    visit.transition_to(VisitStatus.ARRIVED, by_user=by)
    return visit


class TestDoctorCallsTheNextPatient(TestCase):
    """KAN-4 — FR-3, FR-4, FR-6 and AC-2 to AC-5."""

    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.first = _arrived(make_patient(), self.doctor, self.receptionist)
        self.second = _arrived(
            make_patient(phone="9820011111"), self.doctor, self.receptionist, hours=2
        )
        self.client.force_login(self.doctor)

    def _send(self, visit):
        return self.client.post(reverse("doctor_send_for_patient", args=[visit.pk]))

    def test_the_doctor_can_call_a_waiting_patient_in(self):
        self._send(self.first)
        self.first.refresh_from_db()
        self.assertEqual(self.first.status, VisitStatus.IN_CABIN)

    def test_the_send_button_is_offered_on_the_doctors_queue(self):
        # FR-3: the doctor triggers this, not reception.
        response = self.client.get(reverse("doctor_home"))
        self.assertContains(response, reverse("doctor_send_for_patient", args=[self.first.pk]))

    def test_a_second_patient_is_refused_while_the_cabin_is_occupied(self):
        # AC-3.
        self._send(self.first)
        response = self._send(self.second)
        self.second.refresh_from_db()
        self.assertEqual(self.second.status, VisitStatus.ARRIVED)
        messages = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any("already in" in m for m in messages))

    def test_the_refusal_names_who_is_in_there(self):
        self._send(self.first)
        response = self._send(self.second)
        messages = " ".join(str(m) for m in response.wsgi_request._messages)
        self.assertIn(self.first.patient.full_name, messages)

    def test_only_one_patient_is_highlighted_as_in_the_cabin(self):
        # AC-4.
        self._send(self.first)
        body = self.client.get(reverse("doctor_home")).content.decode()
        self.assertEqual(body.count("queue__row--in-cabin"), 1)

    def test_a_doctor_cannot_call_another_doctors_patient(self):
        other = make_doctor(username="dr2", email="dr2@example.in")
        theirs = _arrived(make_patient(phone="9820022222"), other, self.receptionist)

        response = self._send(theirs)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, VisitStatus.ARRIVED)

        # The Edge Cases table asks for an explanatory message, not a page that
        # vanishes — and it should name the doctor to go and find.
        messages = " ".join(str(m) for m in response.wsgi_request._messages)
        self.assertIn(other.display_name, messages)

    def test_two_doctors_can_each_hold_a_patient(self):
        # AC-5.
        other = make_doctor(username="dr3", email="dr3@example.in")
        theirs = _arrived(make_patient(phone="9820033333"), other, self.receptionist)

        self._send(self.first)
        self.client.force_login(other)
        self.client.post(reverse("doctor_send_for_patient", args=[theirs.pk]))

        theirs.refresh_from_db()
        self.assertEqual(theirs.status, VisitStatus.IN_CABIN)

    def test_a_receptionist_cannot_use_the_doctors_send_action(self):
        self.client.force_login(self.receptionist)
        self.assertEqual(self._send(self.first).status_code, 403)


class TestCompletingTheConsultation(TestCase):
    """
    KAN-5 — FR-2, AC-3, AC-5 and the zero-or-negative edge case.

    Everything else in KAN-5 already worked. These are the parts that did not:
    any doctor could end any other doctor's consultation, and a bill of zero, a
    negative fee or a discount larger than the fee all went through to the desk.
    """

    def setUp(self):
        self.doctor = make_doctor()
        self.other = make_doctor(username="dr9", email="dr9@example.in")
        self.receptionist = make_receptionist()
        self.visit = _arrived(make_patient(), self.doctor, self.receptionist)
        self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.patient = self.visit.patient
        self.client.force_login(self.doctor)

    def _complete(self, **overrides):
        payload = {
            "consultation_fee": "800", "procedure_fee": "0",
            "discount": "0", "notes": "",
        }
        payload.update(overrides)
        return self.client.post(
            reverse("doctor_complete_consultation", args=[self.patient.patient_id]),
            payload,
        )

    def _status(self):
        self.visit.refresh_from_db()
        return self.visit.status

    def test_the_doctor_seeing_the_patient_can_complete(self):
        self._complete()
        self.assertEqual(self._status(), VisitStatus.CONSULTED)

    def test_another_doctor_cannot_complete_this_consultation(self):
        # FR-2 / AC-3 / T-3.
        self.client.force_login(self.other)
        response = self._complete()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._status(), VisitStatus.IN_CABIN)

    def test_the_refusal_names_the_doctor_who_has_the_patient(self):
        self.client.force_login(self.other)
        self.assertContains(
            self._complete(), self.doctor.display_name, status_code=403
        )

    def test_another_doctor_is_not_even_offered_the_dialog(self):
        self.client.force_login(self.other)
        response = self.client.get(
            reverse("doctor_complete_consultation", args=[self.patient.patient_id])
        )
        self.assertEqual(response.status_code, 403)

    def test_a_negative_fee_is_refused(self):
        self._complete(consultation_fee="-500")
        self.assertEqual(self._status(), VisitStatus.IN_CABIN)

    def test_a_bill_of_nothing_is_refused(self):
        # AC-5 — prompted, rather than moving forward with a zero amount.
        response = self._complete(consultation_fee="0")
        self.assertEqual(self._status(), VisitStatus.IN_CABIN)
        self.assertContains(response, "comes to nothing")

    def test_a_free_visit_goes_through_when_the_reason_is_given(self):
        # A free visit is real; it just has to be deliberate.
        self._complete(consultation_fee="0", notes="Wound check, no charge")
        self.assertEqual(self._status(), VisitStatus.CONSULTED)

    def test_a_discount_larger_than_the_fee_is_refused(self):
        # Otherwise the clinic ends up owing the patient money.
        response = self._complete(consultation_fee="500", discount="900")
        self.assertEqual(self._status(), VisitStatus.IN_CABIN)
        self.assertContains(response, "more than the fee")

    def test_nothing_is_charged_when_the_bill_is_refused(self):
        self._complete(consultation_fee="500", discount="900")
        self.assertFalse(Charge.objects.filter(visit=self.visit).exists())

    def test_completing_twice_records_one_completion(self):
        # T-5 — the second attempt finds no patient in the cabin.
        self._complete()
        self.assertEqual(self._complete().status_code, 404)
        self.assertEqual(self._status(), VisitStatus.CONSULTED)

    def test_completion_records_who_and_when(self):
        # AC-4.
        self._complete()
        charge = Charge.objects.get(visit=self.visit)
        self.assertEqual(charge.set_by, self.doctor)
        self.assertIsNotNone(charge.created_at)

    def test_completing_frees_the_cabin_for_the_next_patient(self):
        # AC-2 / T-2.
        nxt = _arrived(make_patient(phone="9820044444"), self.doctor,
                       self.receptionist, hours=3)
        self._complete()
        self.client.post(reverse("doctor_send_for_patient", args=[nxt.pk]))
        nxt.refresh_from_db()
        self.assertEqual(nxt.status, VisitStatus.IN_CABIN)


class TestReadyToBillShowsTheAmount(TestCase):
    """KAN-6 — FR-1 and AC-1."""

    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.visit = _arrived(make_patient(), self.doctor, self.receptionist)
        self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.visit.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        Charge.objects.create(
            visit=self.visit, patient=self.visit.patient,
            consultation_fee=Decimal("800.00"), set_by=self.doctor,
        )

    def test_the_amount_to_collect_is_on_the_card(self):
        response = self.client.get(reverse("reception_home"))
        self.assertContains(response, "800")
        self.assertContains(response, "to collect")


class TestSettledIsReadOnly(TestCase):
    """KAN-8 — FR-1, FR-2, FR-3, FR-4 and AC-1 to AC-6."""

    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.visit = _arrived(make_patient(), self.doctor, self.receptionist)
        for status in (VisitStatus.IN_CABIN, VisitStatus.CONSULTED):
            self.visit.transition_to(status, by_user=self.doctor)
        self.charge = Charge.objects.create(
            visit=self.visit, patient=self.visit.patient,
            consultation_fee=Decimal("800.00"), set_by=self.doctor,
        )
        payment = Payment.objects.create(
            charge=self.charge, amount=Decimal("800.00"), received_by=self.receptionist
        )
        self.receipt = Receipt.objects.create(payment=payment)
        self.visit.transition_to(VisitStatus.BILLED, by_user=self.receptionist)

    def test_the_settled_card_carries_no_check_out_button(self):
        # FR-2 / AC-1 — the button is gone from the board entirely.
        response = self.client.get(reverse("reception_home"))
        self.assertNotContains(response, "Check out")

    def test_the_settled_card_opens_a_read_only_view(self):
        # FR-3 / AC-2.
        response = self.client.get(reverse("reception_home"))
        self.assertContains(response, reverse("reception_settled_visit", args=[self.visit.pk]))

    def test_the_read_only_view_says_it_is_closed(self):
        response = self.client.get(reverse("reception_settled_visit", args=[self.visit.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "closed")

    def test_it_offers_both_documents(self):
        # FR-4 / AC-3.
        response = self.client.get(reverse("reception_settled_visit", args=[self.visit.pk]))
        self.assertContains(response, self.receipt.receipt_number)
        self.assertContains(response, reverse("print_receipt", args=[self.receipt.pk]))

    def test_print_prescription_is_unavailable_when_there_is_none(self):
        # AC-6 — disabled rather than producing a blank document.
        response = self.client.get(reverse("reception_settled_visit", args=[self.visit.pk]))
        self.assertContains(response, "No prescription was issued")

    def test_reprinting_does_not_issue_a_new_receipt_number(self):
        # FR-6 / AC-3.
        before = self.receipt.receipt_number
        self.client.get(reverse("print_receipt", args=[self.receipt.pk]))
        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.receipt_number, before)
        self.assertEqual(Receipt.objects.count(), 1)

    def test_a_settled_visit_reports_itself_locked(self):
        # AC-5 — the lock is on the model, so it holds however it is reached.
        self.visit.refresh_from_db()
        self.assertTrue(self.visit.is_locked)


class TestBackwardMovement(TestCase):
    """KAN-9 — FR-3, FR-4, FR-6, FR-7 and AC-3 to AC-7."""

    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.visit = _arrived(make_patient(), self.doctor, self.receptionist)

    def _back(self, visit=None):
        return self.client.post(
            reverse("reception_move_visit_back", args=[(visit or self.visit).pk]),
            headers={"HX-Request": "true"},
        )

    def test_a_waiting_patient_goes_back_to_confirmed(self):
        # AC-3.
        self._back()
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.CONFIRMED)

    def test_the_first_stage_offers_no_way_back(self):
        # FR-4 / AC-4.
        booking = make_visit(make_patient(phone="9820044444"), self.doctor,
                             start=later_today(2))
        self.assertIsNone(booking.previous_status)
        with self.assertRaises(InvalidTransition):
            booking.move_back(by_user=self.receptionist)

    def test_pulling_a_patient_out_of_the_cabin_frees_it(self):
        # FR-6 / AC-5.
        self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self._back()
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.ARRIVED)

        waiting = _arrived(make_patient(phone="9820055555"), self.doctor,
                           self.receptionist, hours=3)
        waiting.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.assertEqual(waiting.status, VisitStatus.IN_CABIN)

    def test_a_completed_consultation_cannot_be_moved_back(self):
        # FR-2 — the record locks when the doctor finishes.
        self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.visit.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        self.assertTrue(self.visit.is_locked)
        self.assertIsNone(self.visit.previous_status)
        with self.assertRaises(InvalidTransition):
            self.visit.move_back(by_user=self.receptionist)

    def test_the_lock_holds_server_side_not_only_in_the_page(self):
        # FR-7 / AC-6 — posting straight at the endpoint is still refused.
        self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.visit.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        self._back()
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.CONSULTED)

    def test_every_backward_move_is_recorded(self):
        # FR-5 / AC-7.
        self._back()
        event = self.visit.status_events.latest("created_at")
        self.assertEqual(event.changed_by, self.receptionist)
        self.assertIn("Moved back", event.note)

    def test_a_reason_is_kept_when_one_is_given(self):
        self.client.post(
            reverse("reception_move_visit_back", args=[self.visit.pk]),
            {"reason": "Called in by mistake"},
        )
        self.assertIn("mistake", self.visit.status_events.latest("created_at").note)

    def test_the_back_button_is_offered_only_while_the_visit_is_open(self):
        response = self.client.get(reverse("reception_home"))
        self.assertContains(response, reverse("reception_move_visit_back", args=[self.visit.pk]))

        self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.visit.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        response = self.client.get(reverse("reception_home"))
        self.assertNotContains(
            response, reverse("reception_move_visit_back", args=[self.visit.pk])
        )

    def test_the_backward_map_stops_at_the_consultation(self):
        # Nothing after the doctor finishes is reversible by design.
        for locked in (VisitStatus.CONSULTED, VisitStatus.BILLED, VisitStatus.COMPLETED):
            self.assertNotIn(locked, BACKWARD_TRANSITIONS)

    def test_a_doctor_cannot_move_a_visit_backward(self):
        self.client.force_login(self.doctor)
        self.assertEqual(self._back().status_code, 403)


class TestTheActionsBar(TestCase):
    """KAN-10 — FR-1 to FR-6 and AC-1 to AC-6."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)

    def test_all_four_actions_are_offered(self):
        # AC-1.
        response = self.client.get(reverse("reception_home"))
        for label in ("Add patient", "New booking", "All bookings", "Doctor availability"):
            self.assertContains(response, label)

    def test_each_action_points_at_its_screen(self):
        # AC-5.
        response = self.client.get(reverse("reception_home"))
        for name in ("reception_register_patient", "reception_new_booking",
                     "reception_bookings", "reception_availability"):
            self.assertContains(response, reverse(name))

    def test_a_doctor_is_shown_none_of_them(self):
        # AC-6 — a doctor reads the board but does not work it.
        self.client.force_login(self.doctor)
        response = self.client.get(reverse("reception_home"))
        self.assertNotContains(response, "Add patient")
        self.assertNotContains(response, "All bookings")

    def test_the_filter_is_carried_into_add_patient(self):
        # AC-4 — so returning lands on the same view they left.
        response = self.client.get(reverse("reception_home"), {"doctor": self.doctor.pk})
        self.assertContains(response, "next=")

    def test_saving_a_patient_returns_to_where_the_user_came_from(self):
        # AC-2.
        target = f"{reverse('reception_home')}?doctor={self.doctor.pk}"
        response = self.client.post(
            f"{reverse('reception_register_patient')}?next={target}",
            {"first_name": "Neha", "last_name": "Joshi", "date_of_birth": "1990-05-04",
             "sex": "F", "blood_group": "", "phone": "9820077777",
             "alternate_phone": "", "email": "", "guardian_name": "",
             "guardian_relation": "", "guardian_phone": "", "address": "",
             "city": "Mumbai", "pincode": "", "referred_by": "", "next": target},
        )
        self.assertRedirects(response, target)

    def test_cancelling_registration_returns_without_creating_anybody(self):
        # AC-3 — the Cancel link is a plain link back; nothing is posted.
        from patients.models import Patient

        before = Patient.objects.count()
        response = self.client.get(
            f"{reverse('reception_register_patient')}?next={reverse('reception_home')}"
        )
        self.assertContains(response, reverse("reception_home"))
        self.assertEqual(Patient.objects.count(), before)

    def test_a_next_pointing_off_site_is_refused(self):
        # Not in the ticket, but a `next` that accepts any URL is an open
        # redirect, and this is a clinical system.
        response = self.client.post(
            f"{reverse('reception_register_patient')}?next=https://evil.example.com/",
            {"first_name": "Test", "last_name": "Patient", "date_of_birth": "1990-05-04",
             "sex": "F", "blood_group": "", "phone": "9820088888",
             "alternate_phone": "", "email": "", "guardian_name": "",
             "guardian_relation": "", "guardian_phone": "", "address": "",
             "city": "", "pincode": "", "referred_by": "",
             "next": "https://evil.example.com/"},
        )
        self.assertNotIn("evil.example.com", response["Location"])
