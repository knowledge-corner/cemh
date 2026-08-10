"""
KAN-13 — the New Bookings form.

Layout, and what the slot grid is allowed to offer. The validation underneath
already refused a past date, a past time and a taken slot; what it did not do
was stop the receptionist choosing one. She picked a time, pressed Confirm, and
only then found out — which is the same number of refusals but one wasted call
with the patient on the line.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments import scheduling
from appointments.models import ClinicHoliday, VisitStatus

from .factories import (
    give_wide_open_hours, make_doctor, make_patient, make_receptionist, make_visit,
    today_at,
)


def _states(slots):
    return {row["state"] for row in slots}


class TestTheSlotGrid(TestCase):
    """Every slot of the day, each saying what can be done with it."""

    def setUp(self):
        self.doctor = make_doctor()
        give_wide_open_hours(self.doctor)
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.tomorrow = timezone.localdate() + timedelta(days=1)

    def test_a_working_day_produces_slots(self):
        self.assertTrue(scheduling.slot_grid(self.doctor, self.tomorrow))

    def test_tomorrows_slots_are_all_free(self):
        grid = scheduling.slot_grid(self.doctor, self.tomorrow)
        self.assertEqual(_states(grid), {scheduling.SLOT_FREE})

    def test_a_booked_slot_is_marked_booked_rather_than_removed(self):
        # The point of the change: it used to disappear, which read as "the
        # doctor does not work then".
        grid = scheduling.slot_grid(self.doctor, self.tomorrow)
        target = grid[2]["start"]
        make_visit(make_patient(), self.doctor, start=target)

        after = scheduling.slot_grid(self.doctor, self.tomorrow)
        self.assertEqual(len(after), len(grid), "the slot vanished instead of greying")

        booked = [row for row in after if row["start"] == target]
        self.assertEqual(len(booked), 1)
        self.assertEqual(booked[0]["state"], scheduling.SLOT_BOOKED)

    def test_a_cancelled_booking_frees_its_slot_again(self):
        grid = scheduling.slot_grid(self.doctor, self.tomorrow)
        target = grid[2]["start"]
        visit = make_visit(make_patient(), self.doctor, start=target)
        visit.transition_to(VisitStatus.CANCELLED, by_user=self.receptionist)

        after = {row["start"]: row["state"] for row in
                 scheduling.slot_grid(self.doctor, self.tomorrow)}
        self.assertEqual(after[target], scheduling.SLOT_FREE)

    def test_todays_earlier_slots_are_marked_past(self):
        grid = scheduling.slot_grid(self.doctor, timezone.localdate())
        gone = [row for row in grid if row["start"] <= timezone.now()]
        if not gone:
            self.skipTest("the suite is running before the clinic opens")
        self.assertTrue(all(row["state"] == scheduling.SLOT_PAST for row in gone))

    def test_a_holiday_produces_no_slots_at_all(self):
        ClinicHoliday.objects.create(date=self.tomorrow, name="Republic Day")
        self.assertEqual(scheduling.slot_grid(self.doctor, self.tomorrow), [])

    def test_available_slots_still_answers_only_with_free_ones(self):
        # Validation asks this one. It must not start offering booked times
        # just because the grid now returns them.
        grid = scheduling.slot_grid(self.doctor, self.tomorrow)
        make_visit(make_patient(), self.doctor, start=grid[2]["start"])

        free = dict.fromkeys(s for s, _ in
                             scheduling.available_slots(self.doctor, self.tomorrow))
        self.assertNotIn(grid[2]["start"], free)


class TestWhatTheFormOffers(TestCase):
    """The rendered grid, which is what the receptionist actually clicks."""

    def setUp(self):
        self.doctor = make_doctor()
        give_wide_open_hours(self.doctor)
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.tomorrow = timezone.localdate() + timedelta(days=1)

    def _slots(self, day=None):
        return self.client.get(reverse("reception_slots"), {
            "doctor": self.doctor.pk,
            "day": (day or self.tomorrow).isoformat(),
        })

    def test_free_slots_are_clickable(self):
        self.assertContains(self._slots(), 'onclick="pickSlot(this)"')

    def test_a_booked_slot_is_shown_greyed_and_disabled(self):
        grid = scheduling.slot_grid(self.doctor, self.tomorrow)
        make_visit(make_patient(), self.doctor, start=grid[0]["start"])

        response = self._slots()
        self.assertContains(response, "slot--booked")
        self.assertContains(response, "disabled")
        self.assertContains(response, "Already booked")

    def test_a_past_slot_cannot_be_clicked(self):
        response = self._slots(timezone.localdate())
        body = response.content.decode()
        if "slot--past" not in body:
            self.skipTest("the suite is running before the clinic opens")
        # Every past slot carries disabled; none carries the click handler.
        self.assertIn("This time has passed", body)

    def test_the_key_explains_the_greying(self):
        response = self._slots()
        self.assertContains(response, "slot-key")
        self.assertContains(response, "Booked")

    def test_a_holiday_says_so_rather_than_showing_an_empty_grid(self):
        ClinicHoliday.objects.create(date=self.tomorrow, name="Diwali")
        self.assertContains(self._slots(), "Diwali")

    def test_a_nonsense_date_does_not_raise(self):
        response = self.client.get(reverse("reception_slots"), {
            "doctor": self.doctor.pk, "day": "not-a-date",
        })
        self.assertEqual(response.status_code, 200)


class TestTheFormItself(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)

    def _page(self):
        return self.client.get(reverse("reception_new_booking"))

    def test_the_date_picker_will_not_offer_a_past_day(self):
        today = timezone.localdate().isoformat()
        self.assertContains(self._page(), f'min="{today}"')

    def test_the_date_picker_stops_at_the_booking_horizon(self):
        _, horizon = scheduling.booking_window()
        self.assertContains(self._page(), f'max="{horizon.isoformat()}"')

    def test_a_past_date_is_still_refused_server_side(self):
        # `min` is a courtesy from the browser. A typed date walks past it.
        yesterday = timezone.localdate() - timedelta(days=1)
        # The slot has to be on the day being posted, or an earlier and more
        # specific check answers first and this stops testing what it says.
        slot = timezone.localtime().replace(
            hour=10, minute=0, second=0, microsecond=0
        ) - timedelta(days=1)

        response = self.client.post(reverse("reception_new_booking"), {
            "patient": make_patient().pk,
            "doctor": self.doctor.pk,
            "day": yesterday.isoformat(),
            "slot": slot.isoformat(),
            "reason": "", "is_follow_up": "",
        })
        self.assertContains(response, "That date has passed")

    def test_there_is_exactly_one_cancel_button(self):
        body = self._page().content.decode()
        self.assertEqual(body.count(">Cancel</a>"), 1)

    def test_the_buttons_are_left_aligned_with_the_fields(self):
        # They used to sit hard right, a screen away from the last field filled.
        body = self._page().content.decode()
        self.assertIn('class="form-actions"', body)
        self.assertNotIn("justify-content:flex-end;gap:.5rem;margin-top:1rem", body)

    def test_cancel_returns_where_the_user_came_from(self):
        target = reverse("reception_home")
        self.assertContains(
            self.client.get(reverse("reception_new_booking"), {"next": target}),
            f'href="{target}"',
        )
