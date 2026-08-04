"""
KAN-28 — alerts are pop-ups that clear themselves.

Two things were wrong with the bar at the top of the page. It took part in the
layout, so appearing and disappearing pushed the screen down and back up while
somebody was reaching for a button. And it stayed until the next page load, so
"Booking saved" could still be sitting there twenty minutes later.

The second half of that had a sharper edge: an action on the board replies with
the board fragment, and Django clears a message only once it has been rendered.
A message set by one of those actions was never rendered, so it waited in the
session and surfaced on whatever page the receptionist opened next.
"""

from django.test import TestCase
from django.urls import reverse

from appointments.models import VisitStatus

from .factories import (
    make_doctor, make_patient, make_receptionist, make_visit, today_at,
)


class TestThereIsNoAlertBar(TestCase):
    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)
        self.visit = make_visit(make_patient(), self.doctor, start=today_at(10))

    def _act(self):
        """Something that produces a message, then land on a full page."""
        return self.client.post(
            reverse("reception_move_visit", args=[self.visit.pk, "ARRIVED"]),
            follow=True,
        )

    def test_the_message_is_shown_as_a_toast(self):
        # Matched on the toast itself, not the host that holds them: the
        # host is on every page whether there is a message or not.
        self.assertContains(self._act(), 'class="toast toast--success"')

    def test_no_view_keeps_a_message_list_at_the_top_of_the_page(self):
        # The old markup, gone from every screen rather than just this one.
        for url in (reverse("reception_home"), reverse("reception_bookings")):
            self.assertNotContains(self.client.get(url), '<ul class="messages">')

    def test_the_toast_host_sits_outside_the_page_body(self):
        # Fixed and outside <main>, so it cannot move the page as it comes and
        # goes. If it were inside, the layout shift is back.
        body = self.client.get(reverse("reception_home")).content.decode()
        self.assertIn('id="toast-host"', body)
        self.assertLess(body.index("</main>"), body.index('id="toast-host"'))

    def test_the_script_that_dismisses_them_is_loaded(self):
        self.assertContains(self.client.get(reverse("reception_home")), "js/toasts.js")


class TestSuccessGoesQuietlyAndErrorsDoNot(TestCase):
    """
    The ticket asks for every alert to close itself after three seconds. Errors
    are deliberately excluded — see the comment on the ticket. This pins the
    distinction so it cannot be undone by accident.
    """

    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)
        self.visit = make_visit(make_patient(), self.doctor, start=today_at(10))

    def test_a_success_toast_is_left_to_time_out(self):
        response = self.client.post(
            reverse("reception_move_visit", args=[self.visit.pk, "ARRIVED"]),
            follow=True,
        )
        self.assertContains(response, "toast--success")
        self.assertNotContains(response, 'data-persist="1"')

    def test_an_error_toast_waits_to_be_dismissed(self):
        # Moving a booking straight to a stage it cannot reach.
        response = self.client.post(
            reverse("reception_move_visit", args=[self.visit.pk, "BILLED"]),
            follow=True,
        )
        self.assertContains(response, "toast--error")
        self.assertContains(response, 'data-persist="1"')

    def test_an_error_is_announced_assertively(self):
        response = self.client.post(
            reverse("reception_move_visit", args=[self.visit.pk, "BILLED"]),
            follow=True,
        )
        self.assertContains(response, 'role="alert"')


class TestAMessageFromTheBoardIsNotStranded(TestCase):
    """
    The bug underneath the ticket: board actions reply with a fragment, and an
    unrendered message stays in the session until some later page happens to
    render it.
    """

    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)
        self.visit = make_visit(make_patient(), self.doctor, start=today_at(10))

    def _htmx_move(self, to):
        return self.client.post(
            reverse("reception_move_visit", args=[self.visit.pk, to]),
            headers={"HX-Request": "true"},
        )

    def test_a_refusal_comes_back_with_the_board(self):
        # An out-of-order click is refused, and the reason has to reach the
        # screen with the fragment that answers it.
        body = self._htmx_move("BILLED").content.decode()
        self.assertIn("toast--error", body)
        self.assertIn('hx-swap-oob="true"', body)

    def test_the_refusal_does_not_turn_up_on_the_next_page_instead(self):
        # The bug this fixes: unrendered, the message waited in the session and
        # appeared on whatever screen was opened next, minutes later.
        self._htmx_move("BILLED")
        later = self.client.get(reverse("reception_bookings"))
        self.assertNotContains(later, "toast--error")

    def test_a_move_that_simply_repeats_itself_says_so(self):
        self._htmx_move("ARRIVED")
        body = self._htmx_move("ARRIVED").content.decode()
        self.assertIn("was already arrived", body)

    def test_a_successful_stage_move_stays_silent(self):
        # Deliberate: the card visibly moving from one stage to the next is the
        # confirmation. A toast on every click would be forty a day telling the
        # receptionist something she is already looking at.
        body = self._htmx_move("ARRIVED").content.decode()
        self.assertNotIn("toast", body)

    def test_the_thirty_second_poll_does_not_wipe_a_toast_off_the_screen(self):
        # The poll answers with the same template. An unconditional swap would
        # clear an error the receptionist was still reading.
        board = self.client.get(
            reverse("reception_board"), headers={"HX-Request": "true"}
        )
        self.assertNotContains(board, "toast-host")

    def test_the_board_itself_still_comes_back(self):
        self.assertContains(self._htmx_move("ARRIVED"), "board__col")
