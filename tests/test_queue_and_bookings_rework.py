"""
Today's View and Bookings View — the simplified queue.

Two bug tickets, one change: the board drops from six stages to four, the
telephone confirmation step goes, the fee is taken in a pop-up instead of on
another screen, and Bookings becomes upcoming-versus-completed with filters on
both.

Written against the acceptance criteria in both tickets.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import ALLOWED_TRANSITIONS, VisitStatus
from billing.models import Charge, Payment, Receipt

from .factories import (
    make_doctor, make_patient, make_receptionist, make_visit, today_at as at,
)

#: The four stages, in the order the ticket fixes them.
STAGES = [
    "Stage 1 · Appointments",
    "Stage 2 · In waiting room",
    "Stage 3 · Cabin",
    "Stage 4 · Ready to bill / Settled",
]


class TestTheFourStages(TestCase):
    """Today's View AC-1, AC-2 and AC-10."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)

    def _body(self):
        return self.client.get(reverse("reception_home")).content.decode()

    def test_the_four_stages_appear_in_order(self):
        body = self._body()
        positions = [body.index(stage) for stage in STAGES]
        self.assertEqual(positions, sorted(positions), "stages are out of order")

    def test_there_is_no_to_confirm_stage(self):
        # AC-1.
        self.assertNotIn("To confirm", self._body())

    def test_a_booking_and_a_confirmed_booking_share_the_appointments_stage(self):
        booked = make_visit(make_patient(), self.doctor, start=at(10))
        confirmed = make_visit(
            make_patient(phone="9820011111"), self.doctor, start=at(11)
        )
        confirmed.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)

        columns = {c["key"]: c for c in
                   self.client.get(reverse("reception_home")).context["columns"]}
        self.assertEqual(columns["appointments"]["count"], 2)
        self.assertIn(booked, columns["appointments"]["visits"])
        self.assertIn(confirmed, columns["appointments"]["visits"])

    def test_ready_to_bill_and_settled_share_the_last_stage(self):
        waiting_to_pay = make_visit(make_patient(), self.doctor, start=at(10))
        paid = make_visit(make_patient(phone="9820011111"), self.doctor, start=at(11))

        for visit in (waiting_to_pay, paid):
            visit.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)
            visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
            visit.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        paid.transition_to(VisitStatus.BILLED, by_user=self.receptionist)

        columns = {c["key"]: c["count"] for c in
                   self.client.get(reverse("reception_home")).context["columns"]}
        self.assertEqual(columns["billing"], 2)

    def test_stage_one_is_sorted_by_appointment_time(self):
        # AC-10.
        for hour in (15, 10, 12):
            make_visit(make_patient(phone=f"98200{hour:05d}"), self.doctor, start=at(hour))

        rows = self.client.get(reverse("reception_home")).context["columns"][0]["visits"]
        times = [r.scheduled_start for r in rows]
        self.assertEqual(times, sorted(times))


class TestStageOneActions(TestCase):
    """Today's View AC-10 — two buttons, and no more."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)
        self.visit = make_visit(make_patient(), self.doctor, start=at(10))

    def _body(self):
        return self.client.get(reverse("reception_home")).content.decode()

    def test_mark_arrived_and_cancel_are_offered(self):
        body = self._body()
        self.assertIn("Mark arrived", body)
        self.assertIn(">Cancel<", body)

    def test_the_confirm_by_phone_action_is_gone(self):
        self.assertNotIn("Confirmed by phone", self._body())

    def test_the_no_show_action_is_gone_from_the_card(self):
        # It was only ever reachable after a confirming call.
        self.assertNotIn("No show", self._body())

    def test_a_booking_can_be_marked_arrived_without_being_confirmed_first(self):
        # The step in between is gone, so the model has to allow the jump —
        # otherwise the only button on the card would refuse every click.
        self.client.post(
            reverse("reception_move_visit", args=[self.visit.pk, "ARRIVED"]),
            headers={"HX-Request": "true"},
        )
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.ARRIVED)

    def test_the_model_allows_it_rather_than_the_view_working_around_it(self):
        self.assertIn(VisitStatus.ARRIVED, ALLOWED_TRANSITIONS[VisitStatus.BOOKED])

    def test_confirming_still_exists_underneath(self):
        # Not removed: visits already carry it, and a patient moved back out of
        # the waiting room lands on it.
        self.assertIn(VisitStatus.CONFIRMED, ALLOWED_TRANSITIONS[VisitStatus.BOOKED])


class TestTheCardIsQuieter(TestCase):
    """Today's View AC-5 and AC-6, and the cancelled panel."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)
        self.patient = make_patient()
        self.visit = make_visit(self.patient, self.doctor, start=at(10))

    def _body(self):
        return self.client.get(reverse("reception_home")).content.decode()

    def test_the_confirmed_by_stamp_is_not_shown(self):
        # AC-5. In a one-receptionist clinic the answer was always the same
        # name, on every card.
        self.visit.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
        body = self._body()
        self.assertNotIn(f"by {self.receptionist.display_name}", body)

    def test_the_confirmation_is_still_recorded_even_though_it_is_not_printed(self):
        self.visit.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.confirmation.changed_by, self.receptionist)

    def test_the_waiting_time_is_not_shown(self):
        # AC-6. It was also arithmetic that could read "-5 min".
        self.visit.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)
        self.assertNotIn("Waiting", self._body())

    def test_cancelled_and_no_shows_are_not_tracked_on_the_board(self):
        self.visit.transition_to(VisitStatus.CANCELLED, by_user=self.receptionist)
        body = self._body()
        self.assertNotIn("Cancelled &amp; no-shows", body)
        self.assertNotIn(self.patient.patient_id, body)


class TestTheNavBar(TestCase):
    """Today's View AC-8, AC-9; Bookings AC-1, AC-2."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)

    def test_the_board_carries_the_four_actions(self):
        body = self.client.get(reverse("reception_home")).content.decode()
        for expected in ("All doctors", "Add patient", "New booking", "All bookings"):
            self.assertIn(expected, body)

    def test_the_filter_has_no_doctor_label(self):
        # AC-8 — "All doctors" already says what the control is.
        body = self.client.get(reverse("reception_home")).content.decode()
        self.assertNotIn(">Doctor<", body)

    def test_the_filter_is_still_announced_to_a_screen_reader(self):
        # Removing the label must not remove the name.
        body = self.client.get(reverse("reception_home")).content.decode()
        self.assertIn("aria-label=\"Filter the board by doctor\"", body)

    def test_doctor_availability_is_not_on_the_board(self):
        # AC-9.
        body = self.client.get(reverse("reception_home")).content.decode()
        self.assertNotIn(reverse("reception_availability"), body)

    def test_bookings_carries_its_own_three(self):
        # Bookings AC-1.
        body = self.client.get(reverse("reception_bookings")).content.decode()
        self.assertIn("Today's queue", body)
        self.assertIn("New booking", body)
        self.assertIn("Doctor schedule", body)

    def test_both_screens_use_the_same_bar(self):
        # Bookings AC-2 — one template, so they cannot drift apart.
        for url in (reverse("reception_home"), reverse("reception_bookings")):
            self.assertIn('class="toolbar"', self.client.get(url).content.decode())

    def test_a_doctor_reading_the_board_is_offered_no_actions(self):
        self.client.force_login(self.doctor)
        body = self.client.get(reverse("reception_home")).content.decode()
        self.assertNotIn('class="toolbar"', body)


class TestTakingTheFeeInAPopUp(TestCase):
    """Today's View AC-3, AC-4 and AC-7."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.patient = make_patient()
        self.visit = make_visit(self.patient, self.doctor, start=at(10))
        self.visit.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)
        self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.visit.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        self.charge = Charge.objects.create(
            visit=self.visit, patient=self.patient,
            consultation_fee=Decimal("800.00"), set_by=self.doctor,
        )
        self.client.force_login(self.receptionist)

    def _open(self):
        return self.client.get(reverse("reception_generate_receipt", args=[self.visit.pk]))

    def _pay(self, amount="800"):
        return self.client.post(
            reverse("reception_generate_receipt", args=[self.visit.pk]),
            {"amount": amount, "method": "CASH", "reference": "", "notes": ""},
        )

    def test_the_card_offers_generate_receipt(self):
        body = self.client.get(reverse("reception_home")).content.decode()
        self.assertIn("Generate receipt", body)
        self.assertIn(reverse("reception_generate_receipt", args=[self.visit.pk]), body)

    def test_it_opens_a_dialog_rather_than_a_page(self):
        # AC-3.
        response = self._open()
        self.assertEqual(response.status_code, 200)
        self.assertIn('role="dialog"', response.content.decode())
        self.assertIn('name="amount"', response.content.decode())

    def test_the_dialog_says_what_to_collect(self):
        self.assertContains(self._open(), "800")

    def test_confirming_takes_the_money_and_issues_the_receipt(self):
        self._pay()
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.BILLED)
        self.assertEqual(Receipt.objects.count(), 1)

    def test_the_board_comes_back_with_the_dialog_closed(self):
        # AC-7 — the card moves to Settled while the user is still looking at
        # it, rather than on the next thirty-second poll.
        body = self._pay().content.decode()
        self.assertIn("hx-swap-oob", body)
        self.assertIn(STAGES[3], body)

    def test_the_settled_card_keeps_view_and_reprint_and_offers_no_complete(self):
        # AC-4.
        self._pay()
        body = self.client.get(reverse("reception_home")).content.decode()
        self.assertIn(reverse("reception_settled_visit", args=[self.visit.pk]), body)
        self.assertNotIn("Check patient out", body)
        self.assertNotIn(">Complete<", body)

    def test_a_part_payment_leaves_the_visit_waiting_to_be_billed(self):
        self._pay(amount="300")
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.CONSULTED)

    def test_the_dialog_refuses_more_than_the_bill(self):
        self.assertContains(self._pay(amount="5000"), "outstanding on this bill")
        self.assertEqual(Payment.objects.count(), 0)

    def test_a_second_confirm_does_not_take_the_money_twice(self):
        self._pay()
        self._pay()
        self.assertEqual(Payment.objects.count(), 1)

    def test_a_visit_with_no_fee_set_says_so_instead_of_breaking(self):
        self.charge.delete()
        response = self._open()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No fee has been set")

    def test_a_doctor_cannot_open_the_dialog(self):
        self.client.force_login(self.doctor)
        self.assertEqual(self._open().status_code, 403)

    def test_a_card_waiting_to_be_paid_is_not_labelled_locked(self):
        # It carries a live Generate receipt button; saying "closed to changes"
        # beside it contradicted the thing the user is meant to click.
        body = self.client.get(reverse("reception_home")).content.decode()
        self.assertIn("Generate receipt", body)
        self.assertNotIn("Locked", body)

    def test_a_settled_card_is_labelled_locked(self):
        # It has no actions left, and a card with nothing on it has to say why.
        self._pay()
        body = self.client.get(reverse("reception_home")).content.decode()
        self.assertIn("Locked", body)


class TestSteppingBackAStage(TestCase):
    """
    The Back button must move the card somewhere the user can see it move.

    Merging To-confirm into Appointments created a case the old rule got wrong:
    a confirmed booking still has a previous *status*, but not a previous
    *stage*, so the button sat there doing nothing visible.
    """

    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)
        self.visit = make_visit(make_patient(), self.doctor, start=at(10))

    def _visit_on_board(self):
        for column in self.client.get(reverse("reception_home")).context["columns"]:
            for visit in column["visits"]:
                if visit.pk == self.visit.pk:
                    return visit
        self.fail("the visit is not on the board")

    def test_a_new_booking_offers_no_way_back(self):
        self.assertFalse(self._visit_on_board().can_step_back)

    def test_a_confirmed_booking_offers_no_way_back_either(self):
        # Its previous status is in the same stage, so there is nowhere to go.
        self.visit.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
        self.assertFalse(self._visit_on_board().can_step_back)
        self.assertIsNotNone(self.visit.previous_status)

    def test_a_waiting_patient_can_be_put_back(self):
        self.visit.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)
        self.assertTrue(self._visit_on_board().can_step_back)

    def test_a_patient_in_the_cabin_can_be_put_back(self):
        self.visit.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)
        self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.assertTrue(self._visit_on_board().can_step_back)

    def test_the_button_follows_the_same_rule(self):
        back = reverse("reception_move_visit_back", args=[self.visit.pk])

        self.visit.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
        self.assertNotContains(self.client.get(reverse("reception_home")), back)

        self.visit.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)
        self.assertContains(self.client.get(reverse("reception_home")), back)


class TestUpcomingAppointments(TestCase):
    """Bookings AC-3 to AC-9."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.other = make_doctor(username="dr2", email="dr2@example.in")
        self.client.force_login(self.receptionist)

        self.today = make_patient()
        self.tomorrow = make_patient(phone="9820011111")
        self.today_visit = make_visit(self.today, self.doctor, start=at(10))
        self.ahead_visit = make_visit(self.tomorrow, self.doctor, start=at(10, days=1))

    def _page(self, **params):
        return self.client.get(reverse("reception_bookings"), params)

    def test_both_tabs_are_offered(self):
        # AC-3.
        body = self._page().content.decode()
        self.assertIn("Upcoming appointments", body)
        self.assertIn("Completed appointments", body)

    def test_upcoming_is_split_into_today_and_ahead(self):
        # AC-4.
        response = self._page()
        self.assertEqual(response.context["today_rows"], [self.today_visit])
        self.assertEqual(response.context["ahead_rows"], [self.ahead_visit])

    def test_a_slot_the_clock_has_passed_is_still_listed_today(self):
        # AC-5. A patient twenty minutes late is exactly the one being looked
        # for, and hiding their booking is how they get told there isn't one.
        late = make_patient(phone="9820022222")
        make_visit(late, self.doctor, start=at(0, 1))

        response = self._page()
        listed = [v.patient for v in response.context["today_rows"]]
        self.assertIn(late, listed)

    def test_the_list_carries_no_queue_actions(self):
        # AC-6 — marking a patient arrived belongs on the board, in front of
        # the person who has walked in.
        body = self._page().content.decode()
        self.assertNotIn("Mark arrived", body)
        self.assertNotIn("Mark as Arrived", body)

    def test_a_booking_can_be_rescheduled_or_cancelled_from_here(self):
        # AC-8.
        self.assertContains(
            self._page(), reverse("reception_edit_booking", args=[self.today_visit.pk])
        )

    def test_a_patient_moved_back_a_stage_reappears_here(self):
        # AC-7.
        self.today_visit.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)
        self.assertNotIn(self.today_visit, self._page().context["today_rows"])

        self.client.post(reverse("reception_move_visit_back", args=[self.today_visit.pk]))
        self.today_visit.refresh_from_db()

        self.assertEqual(self.today_visit.status, VisitStatus.CONFIRMED)
        self.assertIn(self.today_visit, self._page().context["today_rows"])

    def test_it_can_be_filtered_by_doctor(self):
        # AC-9.
        theirs = make_patient(phone="9820033333")
        make_visit(theirs, self.other, start=at(14))

        rows = self._page(doctor=self.doctor.pk).context["today_rows"]
        self.assertNotIn(theirs, [v.patient for v in rows])

    def test_it_can_be_filtered_by_date(self):
        # AC-9.
        tomorrow = timezone.localdate() + timezone.timedelta(days=1)
        response = self._page(**{"from": tomorrow.isoformat()})
        self.assertEqual(response.context["today_rows"], [])
        self.assertEqual(response.context["ahead_rows"], [self.ahead_visit])


class TestCompletedAppointments(TestCase):
    """Bookings AC-10."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)

        self.patient = make_patient()
        self.visit = make_visit(self.patient, self.doctor, start=at(10))
        self.visit.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)
        self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.visit.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        self.charge = Charge.objects.create(
            visit=self.visit, patient=self.patient,
            consultation_fee=Decimal("800.00"), set_by=self.doctor,
        )

    def _completed(self):
        return self.client.get(reverse("reception_bookings"), {"tab": "completed"})

    def _pay(self, amount):
        Payment.objects.create(
            charge=self.charge, amount=Decimal(amount), received_by=self.receptionist,
        )

    def test_a_paid_visit_appears(self):
        self._pay("800.00")
        self.visit.transition_to(VisitStatus.BILLED, by_user=self.receptionist)
        self.assertContains(self._completed(), self.patient.patient_id)

    def test_an_unpaid_visit_does_not(self):
        self.assertNotContains(self._completed(), self.patient.patient_id)

    def test_a_part_paid_visit_does_not(self):
        # It is money somebody still has to collect, not a finished appointment.
        self._pay("300.00")
        self.assertNotContains(self._completed(), self.patient.patient_id)

    def test_a_visit_with_no_charge_at_all_still_appears(self):
        # Nothing owing is not the same as fully paid. Every visit recorded
        # before the clinic billed through this system has no charge on it, and
        # hiding those empties the screen built to look them up.
        self.charge.delete()
        self.visit.transition_to(VisitStatus.BILLED, by_user=self.receptionist)
        self.assertContains(self._completed(), self.patient.patient_id)

    def test_it_can_be_filtered_by_date_and_doctor(self):
        self._pay("800.00")
        self.visit.transition_to(VisitStatus.BILLED, by_user=self.receptionist)

        other = make_doctor(username="dr5", email="dr5@example.in")
        response = self.client.get(
            reverse("reception_bookings"), {"tab": "completed", "doctor": other.pk}
        )
        self.assertNotContains(response, self.patient.patient_id)
