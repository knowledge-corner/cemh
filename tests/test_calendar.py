"""
KAN-22 — the availability calendar, cabins, and conflict detection.

The story is mostly about a screen, but the part that can silently go wrong is
the conflict check, because it has to reason about the *effective* schedule
rather than the rows in the table. Every entry names its own date now — there
is no separate weekly pattern and no override replacing it, just rows, and a
date with nothing entered simply has nobody working it. Get any of that wrong
and the check either blocks something legal or, worse, lets two doctors into
one room.

Dates are pinned to fixed weekdays rather than taken as offsets from today, so
a recurring booking always lands on a predictable day and the failure reads as
a product bug rather than a flaky test.
"""

import uuid
from datetime import date, time, timedelta

from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from accounts.models import DoctorProfile, Specialisation
from appointments import calendar as clinic_calendar
from appointments.models import Cabin, ClinicHoliday, DoctorSchedule

from .factories import make_doctor, make_patient, make_receptionist, make_visit


def next_weekday(weekday, *, weeks=1):
    """A date in the future that lands on ``weekday`` (0 = Monday)."""
    day = timezone.localdate() + timedelta(days=1)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day + timedelta(weeks=weeks - 1)


MONDAY = 0
TUESDAY = 1


class CalendarTestCase(TestCase):
    """Two doctors, two cabins, and a Monday to put things on."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.asha = make_doctor(username="dr-asha", email="asha@example.in",
                                first_name="Asha", last_name="Rao")
        self.vikram = make_doctor(username="dr-vikram", email="vikram@example.in",
                                  first_name="Vikram", last_name="Joshi")
        self.one = Cabin.objects.create(name="Cabin 1")
        self.two = Cabin.objects.create(name="Cabin 2")
        self.monday = next_weekday(MONDAY)
        self.client.force_login(self.receptionist)

    def schedule(self, doctors=None, *, on=None):
        on = on or self.monday
        return clinic_calendar.Schedule(
            doctors if doctors is not None else [self.asha, self.vikram],
            start=on, end=on,
        )


# ── Cabins (FR-1, FR-2) ──────────────────────────────────────────────────────

class TestCabins(CalendarTestCase):
    def _add(self, name, **extra):
        return self.client.post(
            reverse("reception_add_cabin"), {"name": name, **extra}
        )

    def test_reception_can_add_one(self):
        self._add("Cabin 3")
        self.assertTrue(Cabin.objects.filter(name="Cabin 3").exists())

    def test_the_name_is_tidied(self):
        self._add("  Cabin   4 ")
        self.assertTrue(Cabin.objects.filter(name="Cabin 4").exists())

    def test_a_duplicate_name_is_refused(self):
        response = self._add("Cabin 1")
        self.assertEqual(Cabin.objects.filter(name__iexact="cabin 1").count(), 1)
        self.assertContains(response.wsgi_request and self.client.get(
            reverse("reception_calendar")), "already a cabin")

    def test_a_duplicate_differing_only_in_case_is_refused(self):
        # Otherwise the day view grows two columns for one room and reception
        # has to guess which one a doctor is actually in.
        self._add("cabin 1")
        self.assertEqual(Cabin.objects.filter(name__iexact="cabin 1").count(), 1)

    def test_a_duplicate_does_not_reach_the_database_as_a_500(self):
        response = self._add("Cabin 1")
        self.assertEqual(response.status_code, 302)

    def test_a_doctor_cannot_add_one(self):
        self.client.force_login(self.asha)
        self._add("Sneaky")
        self.assertFalse(Cabin.objects.filter(name="Sneaky").exists())

    def test_one_in_use_cannot_be_deleted(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self.client.post(
            reverse("reception_retire_cabin", args=[self.one.pk]),
            {"action": "delete"},
        )
        self.assertTrue(Cabin.objects.filter(pk=self.one.pk).exists())

    def test_one_with_upcoming_hours_cannot_be_retired(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self.client.post(reverse("reception_retire_cabin", args=[self.one.pk]))

        self.one.refresh_from_db()
        self.assertTrue(self.one.is_active)

    def test_one_with_no_future_hours_can_be_retired(self):
        self.client.post(reverse("reception_retire_cabin", args=[self.one.pk]))

        self.one.refresh_from_db()
        self.assertFalse(self.one.is_active)

    def test_a_past_entry_does_not_block_retiring(self):
        # Only what is still ahead counts — a one-off sitting that has already
        # happened is history, not a reason to keep the room on the books.
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday - timedelta(weeks=2), cabin=self.one,
            start_time=time(14), end_time=time(16),
        )
        self.client.post(reverse("reception_retire_cabin", args=[self.one.pk]))

        self.one.refresh_from_db()
        self.assertFalse(self.one.is_active)

    def test_bringing_a_retired_one_back_is_never_blocked(self):
        # The rule is about *starting* to retire a room doctors still use, not
        # about restoring one — there is nothing to protect against there.
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self.one.is_active = False
        self.one.save(update_fields=["is_active"])

        self.client.post(reverse("reception_retire_cabin", args=[self.one.pk]))

        self.one.refresh_from_db()
        self.assertTrue(self.one.is_active)

    def test_the_calendar_flags_a_cabin_still_on_the_hours(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        response = self.client.get(reverse("reception_calendar"))
        by_pk = {cabin.pk: cabin for cabin in response.context["all_cabins"]}
        self.assertTrue(by_pk[self.one.pk].still_scheduled)
        self.assertFalse(by_pk[self.two.pk].still_scheduled)


# ── The two views (FR-3, FR-4) ───────────────────────────────────────────────

class TestTheViews(CalendarTestCase):
    def test_the_month_view_is_the_default(self):
        response = self.client.get(reverse("reception_calendar"))
        self.assertEqual(response.context["view"], "month")
        self.assertIn("weeks", response.context)

    def test_the_day_view_can_be_asked_for(self):
        response = self.client.get(reverse("reception_calendar"), {"view": "day"})
        self.assertEqual(response.context["view"], "day")
        self.assertIn("columns", response.context)

    def test_the_day_view_has_a_column_per_cabin(self):
        response = self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat()},
        )
        names = [c["name"] for c in response.context["columns"]]
        self.assertIn("Cabin 1", names)
        self.assertIn("Cabin 2", names)

    def test_both_views_show_the_same_entry(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        month = self.client.get(
            reverse("reception_calendar"), {"date": self.monday.isoformat()}
        )
        day = self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat()},
        )
        self.assertContains(month, "Asha Rao")
        self.assertContains(day, "Asha Rao")

    def test_a_nonsense_date_falls_back_to_today_rather_than_500(self):
        response = self.client.get(reverse("reception_calendar"), {"date": "yesterday"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["anchor"], timezone.localdate())

    def test_a_month_of_entries_stays_legible(self):
        # Every doctor every day of the displayed month. Without a cap the
        # cells grow until the grid is unreadable (T-8).
        span_start, span_end = clinic_calendar.month_range(self.monday)
        day = span_start
        while day <= span_end:
            for doctor, cabin in ((self.asha, self.one), (self.vikram, self.two)):
                DoctorSchedule.objects.create(
                    doctor=doctor, date=day, cabin=cabin,
                    start_time=time(10), end_time=time(13),
                )
            day += timedelta(days=1)
        response = self.client.get(
            reverse("reception_calendar"), {"date": self.monday.isoformat()}
        )
        cells = [c for week in response.context["weeks"] for c in week]
        self.assertTrue(all(len(cell["entries"]) <= 3 for cell in cells))

    def test_a_doctor_with_no_rows_is_not_listed(self):
        # Neither doctor has a row for this date, so neither is drawn.
        response = self.client.get(
            reverse("reception_calendar"), {"date": self.monday.isoformat()}
        )
        cell = next(
            c for week in response.context["weeks"] for c in week
            if c["date"] == self.monday
        )
        self.assertEqual(cell["entries"], [])

    def test_an_unscheduled_doctor_is_not_summarised_either(self):
        # No chip, no count, nothing — a doctor with no entry for a date is
        # simply absent from the month view.
        response = self.client.get(
            reverse("reception_calendar"), {"date": self.monday.isoformat()}
        )
        self.assertNotContains(response, "on clinic hours")

    def test_a_real_sitting_is_not_hidden_behind_them(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        response = self.client.get(
            reverse("reception_calendar"), {"date": self.monday.isoformat()}
        )
        cell = next(
            c for week in response.context["weeks"] for c in week
            if c["date"] == self.monday
        )
        self.assertEqual([e.doctor for e in cell["entries"]], [self.asha])

    def test_no_cabins_and_no_doctors_does_not_crash(self):
        Cabin.objects.all().delete()
        self.asha.delete()
        self.vikram.delete()
        for view in ("month", "day"):
            with self.subTest(view=view):
                response = self.client.get(reverse("reception_calendar"), {"view": view})
                self.assertEqual(response.status_code, 200)


# ── Filters (FR-5, AC-9) ─────────────────────────────────────────────────────

class TestFilters(CalendarTestCase):
    def setUp(self):
        super().setUp()
        self.endocrinology = Specialisation.objects.get(name="Endocrinology")
        self.cardiology = Specialisation.objects.get(name="Cardiology")
        DoctorProfile.objects.create(user=self.asha, specialisation=self.endocrinology,
                                     activated_at=timezone.now())
        DoctorProfile.objects.create(user=self.vikram, specialisation=self.cardiology,
                                     activated_at=timezone.now())
        for doctor, cabin in ((self.asha, self.one), (self.vikram, self.two)):
            DoctorSchedule.objects.create(
                doctor=doctor, date=self.monday, cabin=cabin,
                start_time=time(10), end_time=time(13),
            )

    def _monday(self, **params):
        return self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat(), **params},
        )

    def _charted(self, response):
        """
        The doctors actually drawn on the day.

        Read from the columns rather than by searching the page: every doctor's
        name is also in the filter dropdown, which is correct — the filter has
        to offer the doctor you are about to switch to — but it makes
        ``assertNotContains`` on the whole page pass only by accident and fail
        for the wrong reason.
        """
        return {
            entry.doctor.display_name
            for column in response.context["columns"]
            for entry in column["entries"]
        }

    def test_it_opens_with_both_filters_on_all(self):
        response = self._monday()
        self.assertIsNone(response.context["chosen_doctor"])
        self.assertIsNone(response.context["chosen_specialisation"])
        self.assertEqual(self._charted(response), {"Asha Rao", "Vikram Joshi"})

    def test_the_doctor_filter_narrows_it(self):
        self.assertEqual(self._charted(self._monday(doctor=self.asha.pk)), {"Asha Rao"})

    def test_the_specialisation_filter_narrows_it(self):
        self.assertEqual(
            self._charted(self._monday(specialisation=self.cardiology.pk)),
            {"Vikram Joshi"},
        )

    def test_a_filter_survives_moving_to_the_next_month(self):
        response = self.client.get(
            reverse("reception_calendar"), {"specialisation": self.cardiology.pk}
        )
        self.assertIn(
            f"specialisation={self.cardiology.pk}", response.context["filter_query"]
        )
        self.assertContains(response, f"specialisation={self.cardiology.pk}")


# ── A doctor sees only themselves (FR-6, FR-7) ───────────────────────────────

class TestDoctorScoping(CalendarTestCase):
    def setUp(self):
        super().setUp()
        for doctor, cabin in ((self.asha, self.one), (self.vikram, self.two)):
            DoctorSchedule.objects.create(
                doctor=doctor, date=self.monday, cabin=cabin,
                start_time=time(10), end_time=time(13),
            )
        self.client.force_login(self.asha)

    def _monday(self, **params):
        return self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat(), **params},
        )

    def test_a_doctor_may_open_the_calendar(self):
        self.assertEqual(self._monday().status_code, 200)

    def test_a_doctor_sees_their_own_hours(self):
        self.assertContains(self._monday(), "Asha Rao")

    def test_a_doctor_does_not_see_another_doctors_hours(self):
        self.assertNotContains(self._monday(), "Vikram Joshi")

    def test_asking_for_another_doctor_in_the_url_does_not_work(self):
        # AC-6 / T-5. The scoping is done from request.user; a doctor who edits
        # the query string gets their own calendar back, not a 200 with
        # somebody else's day on it.
        response = self._monday(doctor=self.vikram.pk)
        self.assertNotContains(response, "Vikram Joshi")
        self.assertContains(response, "Asha Rao")

    def test_a_doctor_is_not_offered_the_doctor_filter(self):
        self.assertNotContains(self._monday(), "All doctors")

    def test_a_doctor_is_not_offered_the_specialisation_filter(self):
        # A single doctor's own calendar is already scoped to themselves —
        # filtering that one-doctor list by specialisation has nothing to do.
        self.assertNotContains(self._monday(), "All specialisations")

    def test_a_stray_specialisation_in_the_url_does_not_empty_their_calendar(self):
        # Mirrors test_asking_for_another_doctor_in_the_url_does_not_work: the
        # filter is reception's tool, so a doctor's own URL cannot use it to
        # filter themselves out of their own calendar.
        response = self._monday(specialisation=Specialisation.objects.first().pk)
        self.assertContains(response, "Asha Rao")

    def test_a_doctor_cannot_add_an_event(self):
        response = self.client.post(reverse("reception_add_calendar_event"), {
            "event_type": "hours", "date": self.monday.isoformat(),
            "doctor": self.asha.pk, "start_time": "15:00", "end_time": "17:00",
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(DoctorSchedule.objects.filter(doctor=self.asha).count(), 1)

    def test_a_doctor_cannot_delete_a_whole_booking_from_here(self):
        # Superseded by the doctor-scoped calendar-edit feature: a doctor may
        # now remove their own single-date entries (see
        # test_doctor_calendar_edit.py), but removing a whole recurring
        # booking in one action is still reception's call. Refused with a
        # message now rather than a flat 403, since the endpoint is no longer
        # closed to doctors altogether.
        sitting = DoctorSchedule.objects.get(doctor=self.asha)
        response = self.client.post(
            reverse("reception_delete_calendar_entry", args=["schedule", sitting.pk]),
            {"scope": "series"},
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(DoctorSchedule.objects.filter(pk=sitting.pk).exists())


# ── The effective schedule ───────────────────────────────────────────────────

class TestWhatActuallyHappensOnADate(CalendarTestCase):
    def test_an_entry_appears_on_its_date(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        entries = self.schedule([self.asha]).entries_on(self.monday)
        self.assertEqual([e.cabin for e in entries], [self.one])

    def test_it_does_not_appear_on_another_date(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        tuesday = self.monday + timedelta(days=1)
        # No entry of its own on Tuesday, so nothing carries Monday's cabin
        # and hours over — there is no fallback for a date left blank.
        entries = self.schedule([self.asha], on=tuesday).entries_on(tuesday)
        self.assertEqual(entries, [])

    def test_two_entries_on_one_date_both_appear(self):
        # A morning and an evening clinic on the same day are two entries, not
        # one replacing the other — see CalendarEventForm's own note that one
        # event is one continuous stretch of time.
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(9), end_time=time(11),
        )
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.two,
            start_time=time(17), end_time=time(19),
        )
        entries = self.schedule([self.asha]).entries_on(self.monday)
        self.assertEqual({(e.cabin, e.start) for e in entries},
                          {(self.one, time(9)), (self.two, time(17))})

    def test_a_doctor_with_no_rows_has_no_entries(self):
        # No clinic-wide default to fall back to — a doctor with no entry for
        # this date simply is not working it, agreeing with the booking form,
        # which would offer them no slots either.
        self.assertEqual(self.schedule([self.asha]).entries_on(self.monday), [])

    def test_a_holiday_empties_the_day(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        ClinicHoliday.objects.create(date=self.monday, name="Diwali")
        self.assertEqual(self.schedule([self.asha]).entries_on(self.monday), [])


# ── Conflicts (FR-17, FR-18) ─────────────────────────────────────────────────

class TestConflicts(CalendarTestCase):
    def _post(self, **overrides):
        payload = {
            "event_type": "hours",
            "date": self.monday.isoformat(),
            "doctor": self.asha.pk,
            "start_time": "10:00",
            "end_time": "13:00",
        }
        payload.update(overrides)
        return self.client.post(reverse("reception_add_calendar_event"), payload)

    def test_a_doctor_cannot_be_in_two_cabins_at_once(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self._post(start_time="11:00", end_time="15:00")
        self.assertEqual(DoctorSchedule.objects.filter(doctor=self.asha).count(), 1)

    def test_back_to_back_for_one_doctor_is_allowed(self):
        # 10:00–12:00 then 12:00–14:00 is how a doctor's day is split, not a
        # mistake.
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(12),
        )
        self._post(start_time="12:00", end_time="14:00")
        self.assertEqual(DoctorSchedule.objects.filter(doctor=self.asha).count(), 2)

    def test_two_cabins_let_two_doctors_overlap(self):
        # There is no manual cabin choice any more — the second doctor is
        # simply given whichever room is free.
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self._post(doctor=self.vikram.pk, start_time="11:00", end_time="14:00")
        row = DoctorSchedule.objects.get(doctor=self.vikram)
        self.assertEqual(row.cabin, self.two)

    def test_when_every_cabin_is_taken_the_booking_is_refused(self):
        for doctor, cabin in ((self.asha, self.one), (self.vikram, self.two)):
            DoctorSchedule.objects.create(
                doctor=doctor, date=self.monday, cabin=cabin,
                start_time=time(10), end_time=time(13),
            )
        third = make_doctor(username="dr-third", email="third@example.in",
                             first_name="Third", last_name="Doctor")
        self._post(doctor=third.pk, start_time="11:00", end_time="14:00")
        self.assertFalse(DoctorSchedule.objects.filter(doctor=third).exists())

    def test_the_refusal_names_the_conflicting_date(self):
        for doctor, cabin in ((self.asha, self.one), (self.vikram, self.two)):
            DoctorSchedule.objects.create(
                doctor=doctor, date=self.monday, cabin=cabin,
                start_time=time(10), end_time=time(13),
            )
        third = make_doctor(username="dr-third", email="third@example.in",
                             first_name="Third", last_name="Doctor")
        self._post(doctor=third.pk, start_time="11:00", end_time="14:00")
        page = self.client.get(reverse("reception_calendar"))
        self.assertContains(page, "Conflict Detected")
        self.assertContains(page, f"{self.monday:%d-%b-%Y}")

    def test_the_same_room_at_a_different_time_is_fine(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self._post(doctor=self.vikram.pk, start_time="15:00", end_time="18:00")
        row = DoctorSchedule.objects.get(doctor=self.vikram)
        self.assertEqual(row.cabin, self.one)

    def test_the_first_row_for_a_doctor_never_clashes_with_anything(self):
        # A doctor with no rows at all has nothing on record to clash with —
        # their very first entry always goes in cleanly.
        self._post()
        self.assertTrue(DoctorSchedule.objects.filter(doctor=self.asha).exists())

    def test_a_recurring_booking_clashes_with_a_one_off_weeks_ahead(self):
        # The one case a naive check misses entirely: the clash is not on the
        # date being entered, it is three Mondays later — and it takes out the
        # whole recurring submission, not just that one date.
        third_monday = next_weekday(MONDAY, weeks=3)
        DoctorSchedule.objects.create(
            doctor=self.vikram, date=third_monday, cabin=self.one,
            start_time=time(11), end_time=time(14),
        )
        self._post(
            doctor=self.vikram.pk, is_recurring="1", weekdays=["M"],
            recur_until=(self.monday + timedelta(weeks=4)).isoformat(),
        )
        self.assertEqual(DoctorSchedule.objects.filter(doctor=self.vikram).count(), 1)

    def test_editing_a_row_does_not_clash_with_itself(self):
        sitting = DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        conflicts = clinic_calendar.find_conflicts(
            doctor=self.asha, cabin=self.one, start=time(10), end=time(13),
            dates=[self.monday], exclude_pk=sitting.pk,
        )
        self.assertEqual(conflicts, [])

    def test_an_end_before_the_start_is_refused(self):
        self._post(start_time="15:00", end_time="10:00")
        self.assertFalse(DoctorSchedule.objects.exists())

    def test_a_zero_length_entry_is_refused(self):
        self._post(start_time="10:00", end_time="10:00")
        self.assertFalse(DoctorSchedule.objects.exists())


# ── The Conflict Detected dialog ─────────────────────────────────────────────

class TestConflictDialog(CalendarTestCase):
    """
    A submission with conflicting dates is held rather than refused outright:
    nothing is written, and the calendar reopens the same form with the
    conflicting dates named and a choice — skip just those, or go back and
    change the booking (see CalendarEventForm._clean_hours and
    views_calendar.calendar_view's "pending_event_conflict" handling).
    """

    def _post(self, **overrides):
        payload = {
            "event_type": "hours",
            "doctor": self.asha.pk,
            "date": self.monday.isoformat(),
            "is_recurring": "1", "weekdays": ["M"],
            "recur_until": (self.monday + timedelta(weeks=2)).isoformat(),
            "start_time": "10:00",
            "end_time": "13:00",
        }
        payload.update(overrides)
        return self.client.post(reverse("reception_add_calendar_event"), payload)

    def test_a_conflicting_submission_creates_nothing_yet(self):
        third_monday = self.monday + timedelta(weeks=2)
        DoctorSchedule.objects.create(
            doctor=self.asha, date=third_monday, cabin=self.one,
            start_time=time(11), end_time=time(12),
        )
        self._post()
        self.assertEqual(DoctorSchedule.objects.filter(doctor=self.asha).count(), 1)

    def test_the_dialog_names_only_the_conflicting_date(self):
        third_monday = self.monday + timedelta(weeks=2)
        DoctorSchedule.objects.create(
            doctor=self.asha, date=third_monday, cabin=self.one,
            start_time=time(11), end_time=time(12),
        )
        self._post()
        page = self.client.get(reverse("reception_calendar"))
        self.assertContains(page, "Conflict Detected")
        self.assertContains(page, f"{third_monday:%d-%b-%Y}")
        self.assertNotContains(page, f"{self.monday:%d-%b-%Y}, 10:00 AM")

    def test_skipping_creates_every_date_except_the_conflicting_one(self):
        third_monday = self.monday + timedelta(weeks=2)
        DoctorSchedule.objects.create(
            doctor=self.asha, date=third_monday, cabin=self.one,
            start_time=time(11), end_time=time(12),
        )
        self._post(skip_conflicts="1")
        created = DoctorSchedule.objects.filter(doctor=self.asha, start_time=time(10))
        self.assertEqual(
            set(created.values_list("date", flat=True)),
            {self.monday, self.monday + timedelta(weeks=1)},
        )

    def test_the_dialog_does_not_reappear_after_being_shown_once(self):
        third_monday = self.monday + timedelta(weeks=2)
        DoctorSchedule.objects.create(
            doctor=self.asha, date=third_monday, cabin=self.one,
            start_time=time(11), end_time=time(12),
        )
        self._post()
        self.client.get(reverse("reception_calendar"))
        page = self.client.get(reverse("reception_calendar"))
        self.assertNotContains(page, "Conflict Detected")

    def test_no_conflict_creates_everything_silently(self):
        self._post()
        self.assertEqual(DoctorSchedule.objects.filter(doctor=self.asha).count(), 3)
        page = self.client.get(reverse("reception_calendar"))
        self.assertNotContains(page, "Conflict Detected")


# ── Adding through the pop-up (FR-8 … FR-13, FR-20) ──────────────────────────

class TestTheAddEventPopUp(CalendarTestCase):
    def _post(self, **overrides):
        payload = {
            "event_type": "hours",
            "date": self.monday.isoformat(),
            "doctor": self.asha.pk,
            "start_time": "10:00",
            "end_time": "13:00",
        }
        payload.update(overrides)
        return self.client.post(reverse("reception_add_calendar_event"), payload)

    def test_the_pop_up_offers_both_event_types(self):
        form = self.client.get(reverse("reception_calendar")).context["event_form"]
        offered = dict(form.fields["event_type"].choices)
        self.assertIn("hours", offered)
        self.assertIn("holiday", offered)

    def test_working_hours_are_created_for_the_date(self):
        self._post()
        row = DoctorSchedule.objects.get(doctor=self.asha)
        self.assertEqual((row.date, row.cabin, row.start_time), (self.monday, self.one, time(10)))

    def test_a_recurring_booking_creates_one_row_per_chosen_date(self):
        self._post(
            is_recurring="1", weekdays=["M"],
            recur_until=(self.monday + timedelta(weeks=2)).isoformat(),
        )
        rows = DoctorSchedule.objects.filter(doctor=self.asha).order_by("date")
        self.assertEqual(
            list(rows.values_list("date", flat=True)),
            [self.monday, self.monday + timedelta(weeks=1), self.monday + timedelta(weeks=2)],
        )
        self.assertEqual(len({r.series_id for r in rows}), 1)
        self.assertIsNotNone(rows.first().series_id)

    def test_a_recurring_booking_without_an_end_date_defaults_to_month_end(self):
        self._post(is_recurring="1", weekdays=["M"])
        rows = DoctorSchedule.objects.filter(doctor=self.asha)
        self.assertTrue(rows.exists())
        last_of_month = clinic_calendar.month_range(self.monday)[1]
        # Every generated date must fall no later than the end of the month
        # the start date is in.
        self.assertTrue(all(
            row.date.month == self.monday.month or row.date <= last_of_month
            for row in rows
        ))

    def test_a_recurring_end_date_before_the_start_is_refused(self):
        self._post(
            is_recurring="1", weekdays=["M"],
            recur_until=(self.monday - timedelta(days=3)).isoformat(),
        )
        self.assertFalse(DoctorSchedule.objects.exists())

    def test_a_mistyped_year_is_refused_rather_than_creating_thousands(self):
        self._post(
            is_recurring="1", weekdays=["M"],
            recur_until=self.monday.replace(year=self.monday.year + 90).isoformat(),
        )
        self.assertFalse(DoctorSchedule.objects.exists())

    def test_recurring_needs_at_least_one_weekday(self):
        self._post(is_recurring="1")
        self.assertFalse(DoctorSchedule.objects.exists())

    def test_a_holiday_can_be_added_from_the_same_pop_up(self):
        self._post(event_type="holiday", name="Diwali", doctor="",
                   start_time="", end_time="")
        self.assertTrue(ClinicHoliday.objects.filter(date=self.monday).exists())

    def test_a_holiday_needs_a_name(self):
        self._post(event_type="holiday", name="", doctor="",
                   start_time="", end_time="")
        self.assertFalse(ClinicHoliday.objects.exists())

    def test_the_same_holiday_twice_is_refused(self):
        ClinicHoliday.objects.create(date=self.monday, name="Diwali")
        self._post(event_type="holiday", name="Diwali again", doctor="",
                   start_time="", end_time="")
        self.assertEqual(ClinicHoliday.objects.filter(date=self.monday).count(), 1)

    def test_working_hours_need_a_doctor(self):
        self._post(doctor="")
        self.assertFalse(DoctorSchedule.objects.exists())

    def test_a_pending_doctor_is_not_offered(self):
        # KAN-21 FR-7 / AC-8. Nobody can sign in as them, so a patient booked
        # into those hours would arrive to an empty cabin.
        DoctorProfile.objects.create(user=self.vikram)   # never activated
        form = self.client.get(reverse("reception_calendar")).context["event_form"]
        offered = [label for _value, label in form.fields["doctor"].choices]
        self.assertNotIn("Vikram Joshi", offered)

    def test_a_pending_doctor_cannot_be_given_hours(self):
        DoctorProfile.objects.create(user=self.vikram)
        self._post(doctor=self.vikram.pk)
        self.assertFalse(DoctorSchedule.objects.filter(doctor=self.vikram).exists())

    def test_the_refusal_says_why_rather_than_select_a_valid_choice(self):
        # Django's own message for a value outside the queryset is "Select a
        # valid choice" — true, useless, and nothing the receptionist can act
        # on. She needs to be told to re-send the invitation.
        DoctorProfile.objects.create(user=self.vikram)
        self._post(doctor=self.vikram.pk)
        page = self.client.get(reverse("reception_calendar"))
        self.assertContains(page, "has not set their password yet")


# ── Removing an entry ─────────────────────────────────────────────────────────

class TestRemoving(CalendarTestCase):
    def setUp(self):
        super().setUp()
        self.sitting = DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )

    def _delete(self, kind, pk, **payload):
        return self.client.post(
            reverse("reception_delete_calendar_entry", args=[kind, pk]), payload
        )

    def test_a_single_entry_is_removed_outright(self):
        self._delete("schedule", self.sitting.pk)
        self.assertFalse(DoctorSchedule.objects.filter(pk=self.sitting.pk).exists())

    def test_a_recurring_series_can_be_removed_as_a_whole(self):
        series = uuid.uuid4()
        rows = [
            DoctorSchedule.objects.create(
                doctor=self.vikram, date=self.monday + timedelta(weeks=n),
                cabin=self.two, start_time=time(9), end_time=time(11),
                series_id=series,
            )
            for n in range(3)
        ]
        self._delete("schedule", rows[0].pk, scope="series")
        self.assertEqual(DoctorSchedule.objects.filter(series_id=series).count(), 0)

    def test_removing_one_date_of_a_series_leaves_the_rest(self):
        series = uuid.uuid4()
        rows = [
            DoctorSchedule.objects.create(
                doctor=self.vikram, date=self.monday + timedelta(weeks=n),
                cabin=self.two, start_time=time(9), end_time=time(11),
                series_id=series,
            )
            for n in range(3)
        ]
        self._delete("schedule", rows[0].pk)
        self.assertFalse(DoctorSchedule.objects.filter(pk=rows[0].pk).exists())
        self.assertEqual(DoctorSchedule.objects.filter(series_id=series).count(), 2)

    def test_a_holiday_can_be_removed(self):
        holiday = ClinicHoliday.objects.create(date=self.monday, name="Diwali")
        self._delete("holiday", holiday.pk)
        self.assertFalse(ClinicHoliday.objects.filter(pk=holiday.pk).exists())

    def test_a_get_removes_nothing(self):
        # A link that deletes on GET is a link a browser can follow while
        # prefetching.
        self.client.get(
            reverse("reception_delete_calendar_entry", args=["schedule", self.sitting.pk])
        )
        self.assertTrue(DoctorSchedule.objects.filter(pk=self.sitting.pk).exists())


# ── Cabins reach the rest of the system ──────────────────────────────────────

class TestTheAddEventPopUpWritesTheSameRows(CalendarTestCase):
    """
    KAN-50 left the calendar as the only screen writing this table.

    Kept as a small integration smoke test — what matters is that hours
    entered through the pop-up show up on the calendar exactly as any other
    row would.
    """

    def test_hours_entered_there_show_on_the_calendar(self):
        self.client.post(reverse("reception_add_calendar_event"), {
            "event_type": "hours", "doctor": self.asha.pk,
            "date": self.monday.isoformat(),
            "start_time": "10:00", "end_time": "13:00",
        })
        response = self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat()},
        )
        self.assertContains(response, "Asha Rao")
        self.assertContains(response, "Cabin 1")


class TestTheAvailabilityScreenIsGone(CalendarTestCase):
    """KAN-50. Two screens writing one set of tables is two answers to
    "when does this doctor work", so the older one was removed outright."""

    def test_its_urls_no_longer_resolve(self):
        for name in ("reception_availability", "reception_add_availability",
                     "reception_remove_availability"):
            with self.subTest(name=name):
                with self.assertRaises(NoReverseMatch):
                    reverse(name)

    def test_nothing_still_links_to_it(self):
        # A dead link is worse than a missing one: it 404s in front of the user
        # rather than being noticed here.
        for url in (reverse("reception_calendar"), reverse("reception_bookings"),
                    reverse("reception_home"), reverse("reception_doctors")):
            with self.subTest(url=url):
                body = self.client.get(url).content.decode()
                self.assertNotIn("/availability", body)
