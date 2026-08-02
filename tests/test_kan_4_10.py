"""
KAN-4 to KAN-10 — the rest of the Today's Clinic epic.

Written against the acceptance criteria in each ticket. As with KAN-2 and KAN-3,
a good deal already existed; these cover the behaviour the tickets asked for
that was not there, and pin the rules the tickets are explicit about.

KAN-7 (email and WhatsApp delivery) has no tests here. It is genuinely blocked —
see the comment on the ticket.
"""

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from appointments.models import BACKWARD_TRANSITIONS, InvalidTransition, VisitStatus
from billing.models import Charge, Payment, Receipt
from portal.forms import PaymentForm

from .factories import (
    make_doctor, make_patient, make_receptionist, make_visit, today_at,
)


def _arrived(patient, doctor, by, hours=1):
    """
    A patient who has arrived and is waiting.

    ``hours`` must differ per patient for the same doctor: the double-booking
    exclusion constraint refuses two overlapping visits, which is exactly what
    it is for.

    A fixed hour of the morning rather than an offset from the clock. Offsets
    run past midnight when the suite runs late, and then the two visits land on
    different days — which silently switches off the one-patient-per-cabin rule
    these tests exist to check, because that rule is scoped to a single day.
    """
    visit = make_visit(patient, doctor, start=today_at(8 + hours))
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


class TestTakingTheMoney(TestCase):
    """
    KAN-6 — AC-2, AC-3, AC-5, and the amount edge cases.

    The receipt itself, its numbering and the move to Settled already worked.
    What did not: a second click took the fee twice, and the payment box would
    accept a negative amount, a zero, or more than the bill came to.
    """

    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.visit = _arrived(make_patient(), self.doctor, self.receptionist)
        self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.visit.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        self.charge = Charge.objects.create(
            visit=self.visit, patient=self.visit.patient,
            consultation_fee=Decimal("800.00"), set_by=self.doctor,
        )
        self.client.force_login(self.receptionist)

    def _pay(self, amount="800", method="CASH"):
        return self.client.post(
            reverse("reception_billing", args=[self.visit.pk]),
            {"amount": amount, "method": method, "reference": "", "notes": ""},
        )

    def test_confirming_payment_issues_a_receipt_and_settles_the_visit(self):
        # AC-2.
        self._pay()
        self.visit.refresh_from_db()
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(self.visit.status, VisitStatus.BILLED)

    def test_a_second_click_does_not_take_the_money_twice(self):
        # AC-5 / T-5. Two payments of 800 against an 800 bill used to leave the
        # patient 800 in credit and two receipt numbers spent.
        self._pay()
        self._pay()
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)
        self.assertEqual(self.charge.balance, Decimal("0.00"))

    def test_a_settled_bill_offers_no_second_payment(self):
        # What the receptionist sees once the fee is in: the payment box is
        # replaced by the settled notice, so there is nothing left to click.
        self._pay()
        page = self.client.get(reverse("reception_billing", args=[self.visit.pk]))
        self.assertContains(page, "Nothing outstanding")

    def test_a_payment_posted_at_a_settled_bill_is_refused(self):
        # The box being hidden is not the guard — this is.
        self._pay()
        self._pay()
        self.assertEqual(Payment.objects.count(), 1)

    def test_the_refusal_survives_the_form_check_being_bypassed(self):
        # T-6 proper. Two receptionists submitting at the same instant both read
        # a positive balance before either wrote to it, so the form check cannot
        # see the collision — the row lock in the view is what stops it. Proved
        # by taking the form check out of the way, which is the same position a
        # genuinely simultaneous second request is in.
        self._pay()
        with patch.object(PaymentForm, "clean_amount", lambda self: self.cleaned_data["amount"]):
            response = self._pay()

        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(Receipt.objects.count(), 1)
        messages = " ".join(str(m) for m in response.wsgi_request._messages)
        self.assertIn("already settled", messages)

    def test_a_negative_payment_is_refused(self):
        # Refunds are out of scope for this ticket, and a minus sign is not one.
        self._pay(amount="-200")
        self.assertEqual(Payment.objects.count(), 0)

    def test_a_payment_of_nothing_is_refused(self):
        # It would otherwise spend a receipt number on no money.
        self._pay(amount="0")
        self.assertEqual(Receipt.objects.count(), 0)

    def test_more_than_the_bill_is_refused(self):
        response = self._pay(amount="5000")
        self.assertEqual(Payment.objects.count(), 0)
        self.assertContains(response, "outstanding on this bill")

    def test_a_part_payment_leaves_the_visit_on_the_billing_list(self):
        self._pay(amount="300")
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.CONSULTED)
        self.assertEqual(self.charge.balance, Decimal("500.00"))

    def test_the_rest_of_a_part_payment_settles_it(self):
        self._pay(amount="300")
        self._pay(amount="500")
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.BILLED)
        self.assertEqual(Receipt.objects.count(), 2)

    def test_the_printed_receipt_carries_everything_ac_3_asks_for(self):
        # AC-3 — patient, doctor, date, amount, payment mode, receipt number.
        self._pay(method="UPI")
        receipt = Receipt.objects.get()
        page = self.client.get(reverse("print_receipt", args=[receipt.pk]))
        for expected in (
            self.visit.patient.full_name,
            self.doctor.display_name,
            receipt.receipt_number,
            "800",
            "UPI",
        ):
            self.assertContains(page, expected)

    def test_reprinting_does_not_issue_a_new_number(self):
        self._pay()
        receipt = Receipt.objects.get()
        self.client.get(reverse("print_receipt", args=[receipt.pk]))
        self.client.get(reverse("print_receipt", args=[receipt.pk]))
        self.assertEqual(Receipt.objects.count(), 1)
        receipt.refresh_from_db()
        self.assertEqual(Receipt.objects.get().receipt_number, receipt.receipt_number)

    def test_a_doctor_cannot_take_the_money(self):
        # T-3.
        self.client.force_login(self.doctor)
        self.assertEqual(self._pay().status_code, 403)
        self.assertEqual(Payment.objects.count(), 0)


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

    def test_the_booking_cannot_be_edited_through_the_endpoint(self):
        # AC-5 / T-3 — refused server-side, not merely hidden.
        response = self.client.get(
            reverse("reception_edit_booking", args=[self.visit.pk]), follow=True
        )
        self.assertContains(response, "cannot be changed")

    def test_the_slot_is_unchanged_after_an_attempted_edit(self):
        before = self.visit.scheduled_start
        self.client.post(
            reverse("reception_edit_booking", args=[self.visit.pk]),
            {"action": "cancel", "reason": "changed my mind"},
        )
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.BILLED)
        self.assertEqual(self.visit.scheduled_start, before)

    def test_reprinting_five_times_leaves_the_record_alone(self):
        # T-5 / FR-6 — "does not alter the record … or change any timestamp".
        url = reverse("print_receipt", args=[self.receipt.pk]) + "?mark=1"
        self.client.get(url)
        self.receipt.refresh_from_db()
        first_printed = self.receipt.printed_at
        self.assertIsNotNone(first_printed)

        for _ in range(4):
            self.client.get(url)

        self.receipt.refresh_from_db()
        self.assertEqual(self.receipt.printed_at, first_printed)

    def test_a_visit_settled_earlier_can_still_be_opened_from_all_bookings(self):
        # KAN-8's last edge case — the same read-only view, reached from the
        # Past bookings tab. A patient ringing next month for a copy of their
        # receipt is what that screen's date filter is for.
        page = self.client.get(reverse("reception_bookings") + "?tab=completed")
        self.assertContains(
            page, reverse("reception_settled_visit", args=[self.visit.pk])
        )

    def test_every_print_is_still_recorded_in_the_audit_log(self):
        # The history of prints belongs in the append-only log, not in a
        # timestamp on the document that reprinting would overwrite.
        from audit.models import AccessLog

        url = reverse("print_receipt", args=[self.receipt.pk]) + "?mark=1"
        for _ in range(3):
            self.client.get(url)

        self.assertEqual(
            AccessLog.objects.filter(
                description__contains=self.receipt.receipt_number
            ).count(),
            3,
        )


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
                             start=today_at(16))
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

    def test_a_finished_card_says_it_is_locked(self):
        # AC-2 and the accessibility note. A card with no actions left reads as
        # a page that has not finished loading, so the reason is said in words
        # rather than left to the absence of a control.
        #
        # Narrowed since Stage 4 gained the Generate receipt button: a consulted
        # visit is not editable, but its card now carries a live action, and
        # "closed to changes" printed beside a button the user is meant to press
        # contradicts it. The badge is for cards with nothing left on them.
        from billing.models import Charge, Payment

        self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.visit.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)

        charge = Charge.objects.create(
            visit=self.visit, patient=self.visit.patient,
            consultation_fee=Decimal("800.00"), set_by=self.doctor,
        )
        Payment.objects.create(
            charge=charge, amount=Decimal("800.00"), received_by=self.receptionist,
        )
        self.visit.transition_to(VisitStatus.BILLED, by_user=self.receptionist)

        response = self.client.get(reverse("reception_home"))
        self.assertContains(response, "Locked")
        self.assertContains(response, "closed to changes")

    def test_a_visit_waiting_to_be_paid_is_still_not_editable(self):
        # The badge moved; the lock itself did not.
        self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.visit.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        self.assertTrue(self.visit.is_locked)
        self.assertIsNone(self.visit.previous_status)

    def test_an_open_card_is_not_marked_locked(self):
        self.assertNotContains(self.client.get(reverse("reception_home")), "Locked")


class TestTheActionsBar(TestCase):
    """KAN-10 — FR-1 to FR-6 and AC-1 to AC-6."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)

    def test_all_four_actions_are_offered(self):
        # AC-1. The fourth is the doctor filter: setting a doctor's working days
        # is diary work and moved to the Bookings screen, so what sits on the
        # board is the filter plus the three things reception reaches for while
        # the clinic is running.
        response = self.client.get(reverse("reception_home"))
        for label in ("All doctors", "Add patient", "New booking", "All bookings"):
            self.assertContains(response, label)

    def test_each_action_points_at_its_screen(self):
        # AC-5.
        response = self.client.get(reverse("reception_home"))
        for name in ("reception_register_patient", "reception_new_booking",
                     "reception_bookings"):
            self.assertContains(response, reverse(name))

    def test_the_doctor_schedule_is_reached_from_bookings_not_the_board(self):
        board = self.client.get(reverse("reception_home"))
        self.assertNotContains(board, reverse("reception_availability"))

        diary = self.client.get(reverse("reception_bookings"))
        self.assertContains(diary, reverse("reception_availability"))
        self.assertContains(diary, "Doctor schedule")

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

    # ── The duplicate-patient edge case ──────────────────────────────────────

    FIELDS = {
        "first_name": "Meera", "last_name": "Kulkarni", "date_of_birth": "1988-03-11",
        "sex": "F", "blood_group": "", "phone": "9820099999",
        "alternate_phone": "", "email": "", "guardian_name": "",
        "guardian_relation": "", "guardian_phone": "", "address": "",
        "city": "Mumbai", "pincode": "", "referred_by": "",
    }

    def _register(self, **overrides):
        return self.client.post(
            reverse("reception_register_patient"), {**self.FIELDS, **overrides}
        )

    def test_the_same_person_twice_is_queried_before_a_second_record_is_made(self):
        from patients.models import Patient

        self._register()
        existing = Patient.objects.get(first_name="Meera")

        response = self._register()
        self.assertEqual(Patient.objects.filter(first_name="Meera").count(), 1)
        self.assertContains(response, "already registered")
        self.assertContains(response, existing.patient_id)

    def test_the_existing_record_can_be_booked_straight_from_the_warning(self):
        from patients.models import Patient

        self._register()
        existing = Patient.objects.get(first_name="Meera")
        self.assertContains(self._register(), f"patient_id={existing.patient_id}")

    def test_reception_can_say_it_is_a_different_person(self):
        # Not a hard block — two people really can share a name and a number.
        from patients.models import Patient

        self._register()
        self._register(confirm="1")
        self.assertEqual(Patient.objects.filter(first_name="Meera").count(), 2)

    def test_a_sibling_on_the_same_number_is_not_treated_as_a_duplicate(self):
        # Families share a mobile constantly; this is the ordinary case, not
        # the mistaken one.
        from patients.models import Patient

        self._register()
        self._register(first_name="Rohan", date_of_birth="2015-07-02", sex="M")
        self.assertEqual(Patient.objects.filter(phone="9820099999").count(), 2)

    def test_the_return_path_survives_the_duplicate_warning(self):
        # AC-4 — the warning re-posts the page, and losing the filter there
        # would defeat the point of carrying it.
        target = f"{reverse('reception_home')}?doctor={self.doctor.pk}"
        self._register(next=target)
        response = self._register(next=target)
        self.assertContains(response, "already registered")
        self.assertContains(response, f'value="{target}"')

    def test_the_cancel_link_goes_back_where_the_user_came_from(self):
        # AC-3 / T-5. There were three Cancel links on this form and two went
        # somewhere else entirely.
        target = f"{reverse('reception_home')}?doctor={self.doctor.pk}"
        body = self.client.get(
            f"{reverse('reception_register_patient')}?next={target}"
        ).content.decode()
        self.assertEqual(body.count(">Cancel<"), 1)
        self.assertIn(f'href="{target}"', body)
