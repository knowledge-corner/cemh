"""
KAN-22 — the availability calendar, cabins, and conflict detection.

The story is mostly about a screen, but the part that can silently go wrong is
the conflict check, because it has to reason about the *effective* schedule
rather than the rows in the tables. An override replaces a doctor's ordinary
week for its date; a weekly row does not exist on a date the doctor already has
an override for; and the clinic-wide default hours are a fallback rather than a
commitment. Get any of those wrong and the check either blocks something legal
or, worse, lets two doctors into one room.

Dates are pinned to fixed weekdays rather than taken as offsets from today. A
weekly pattern is keyed on the weekday, so "today + 3" tests a different rule on
a Friday than on a Monday, and the failure reads as a product bug.
"""

from datetime import date, time, timedelta

from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from accounts.models import DoctorProfile, Specialisation
from appointments import calendar as clinic_calendar
from appointments.models import (
    Cabin, ClinicHoliday, DoctorLeave, DoctorSchedule, ScheduleOverride,
)

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
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self.client.post(
            reverse("reception_retire_cabin", args=[self.one.pk]),
            {"action": "delete"},
        )
        self.assertTrue(Cabin.objects.filter(pk=self.one.pk).exists())

    def test_one_still_on_a_doctors_weekly_hours_cannot_be_retired(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self.client.post(reverse("reception_retire_cabin", args=[self.one.pk]))

        self.one.refresh_from_db()
        self.assertTrue(self.one.is_active)

    def test_one_with_an_upcoming_override_cannot_be_retired(self):
        ScheduleOverride.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(14), end_time=time(16),
        )
        self.client.post(reverse("reception_retire_cabin", args=[self.one.pk]))

        self.one.refresh_from_db()
        self.assertTrue(self.one.is_active)

    def test_one_with_no_future_hours_can_be_retired(self):
        self.client.post(reverse("reception_retire_cabin", args=[self.one.pk]))

        self.one.refresh_from_db()
        self.assertFalse(self.one.is_active)

    def test_a_past_override_does_not_block_retiring(self):
        # Only what is still ahead counts — a one-off sitting that has already
        # happened is history, not a reason to keep the room on the books.
        ScheduleOverride.objects.create(
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
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self.one.is_active = False
        self.one.save(update_fields=["is_active"])

        self.client.post(reverse("reception_retire_cabin", args=[self.one.pk]))

        self.one.refresh_from_db()
        self.assertTrue(self.one.is_active)

    def test_the_calendar_flags_a_cabin_still_on_the_hours(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        response = self.client.get(reverse("reception_calendar"))
        by_pk = {cabin.pk: cabin for cabin in response.context["all_cabins"]}
        self.assertTrue(by_pk[self.one.pk].still_scheduled)
        self.assertFalse(by_pk[self.two.pk].still_scheduled)

    def test_a_retired_one_is_not_offered_for_new_hours(self):
        self.one.is_active = False
        self.one.save()
        response = self.client.get(reverse("reception_calendar"))
        choices = response.context["event_form"].fields["cabin"].queryset
        self.assertNotIn(self.one, choices)
        self.assertIn(self.two, choices)


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
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
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
        # Every doctor every weekday. Without a cap the cells grow until the
        # grid is unreadable (T-8).
        for weekday in range(7):
            for doctor, cabin in ((self.asha, self.one), (self.vikram, self.two)):
                DoctorSchedule.objects.create(
                    doctor=doctor, weekday=weekday, cabin=cabin,
                    start_time=time(10), end_time=time(13),
                )
        response = self.client.get(reverse("reception_calendar"))
        cells = [c for week in response.context["weeks"] for c in week]
        self.assertTrue(all(len(cell["entries"]) <= 3 for cell in cells))

    def test_doctors_on_clinic_default_hours_are_counted_not_listed(self):
        # Neither doctor has rows, so both fall back to clinic hours on every
        # working day. Drawing them filled every cell in the month with
        # identical chips and buried the real sittings underneath — which only
        # a browser showed, because the count assertions all still passed.
        response = self.client.get(
            reverse("reception_calendar"), {"date": self.monday.isoformat()}
        )
        cell = next(
            c for week in response.context["weeks"] for c in week
            if c["date"] == self.monday
        )
        self.assertEqual(cell["entries"], [])
        self.assertEqual(cell["on_default"], 2)

    def test_they_are_still_reported_rather_than_dropped(self):
        # Those hours are genuinely bookable. A month claiming nobody works
        # would disagree with the booking form, quietly.
        response = self.client.get(
            reverse("reception_calendar"), {"date": self.monday.isoformat()}
        )
        self.assertContains(response, "on clinic hours")

    def test_a_real_sitting_is_not_hidden_behind_them(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
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
                doctor=doctor, weekday=MONDAY, cabin=cabin,
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
                doctor=doctor, weekday=MONDAY, cabin=cabin,
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

    def test_a_doctor_cannot_add_an_event(self):
        response = self.client.post(reverse("reception_add_calendar_event"), {
            "event_type": "hours", "date": self.monday.isoformat(),
            "doctor": self.asha.pk, "cabin": self.one.pk,
            "start_time": "15:00", "end_time": "17:00", "repeat": "once",
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ScheduleOverride.objects.exists())

    def test_a_doctor_cannot_delete_their_whole_weekly_pattern_from_here(self):
        # Superseded by the doctor-scoped calendar-edit feature: a doctor may
        # now remove their own single-day entries (see
        # test_doctor_calendar_edit.py), but the whole weekly pattern is still
        # out of reach from this screen — re-uploading a schedule is the tool
        # for that. Refused with a message now rather than a flat 403, since
        # the endpoint is no longer closed to doctors altogether.
        sitting = DoctorSchedule.objects.get(doctor=self.asha)
        response = self.client.post(
            reverse("reception_delete_calendar_entry", args=["schedule", sitting.pk]),
            {"scope": "series"},
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(DoctorSchedule.objects.filter(pk=sitting.pk).exists())


# ── The effective schedule ───────────────────────────────────────────────────

class TestWhatActuallyHappensOnADate(CalendarTestCase):
    def test_a_weekly_row_appears_on_its_weekday(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        entries = self.schedule([self.asha]).entries_on(self.monday)
        self.assertEqual([e.cabin for e in entries], [self.one])

    def test_it_does_not_appear_on_another_weekday(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        tuesday = self.monday + timedelta(days=1)
        self.assertEqual(self.schedule([self.asha], on=tuesday).entries_on(tuesday), [])

    def test_an_override_replaces_the_week_for_its_date(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        ScheduleOverride.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.two,
            start_time=time(16), end_time=time(19),
        )
        entries = self.schedule([self.asha]).entries_on(self.monday)
        self.assertEqual([(e.cabin, e.start) for e in entries], [(self.two, time(16))])

    def test_a_doctor_with_no_rows_falls_back_to_clinic_hours(self):
        # And is shown, rather than left off. Those hours are genuinely
        # bookable, so a calendar that hides them disagrees with the booking
        # form — in the direction that puts a patient in front of nobody.
        entries = self.schedule([self.asha]).entries_on(self.monday)
        self.assertEqual(len(entries), 1)
        self.assertIsNone(entries[0].cabin)
        self.assertEqual(entries[0].source, clinic_calendar.SOURCE_DEFAULT)

    def test_a_holiday_empties_the_day(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        ClinicHoliday.objects.create(date=self.monday, name="Diwali")
        self.assertEqual(self.schedule([self.asha]).entries_on(self.monday), [])

    def test_leave_marks_the_entry_rather_than_hiding_it(self):
        # Reception has to see that somebody was down to work and is not, or
        # the day reads as one nobody was ever scheduled for.
        DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        DoctorLeave.objects.create(doctor=self.asha, date=self.monday)
        entries = self.schedule([self.asha]).entries_on(self.monday)
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0].away)


# ── Conflicts (FR-17, FR-18) ─────────────────────────────────────────────────

class TestConflicts(CalendarTestCase):
    def _post(self, **overrides):
        payload = {
            "event_type": "hours",
            "date": self.monday.isoformat(),
            "doctor": self.asha.pk,
            "cabin": self.one.pk,
            "start_time": "10:00",
            "end_time": "13:00",
            "repeat": "once",
        }
        payload.update(overrides)
        return self.client.post(reverse("reception_add_calendar_event"), payload)

    def test_two_doctors_cannot_share_a_cabin_at_the_same_time(self):
        ScheduleOverride.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self._post(doctor=self.vikram.pk, start_time="12:00", end_time="14:00")
        self.assertEqual(
            ScheduleOverride.objects.filter(doctor=self.vikram).count(), 0
        )

    def test_the_clash_names_who_has_the_room(self):
        ScheduleOverride.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self._post(doctor=self.vikram.pk, start_time="12:00", end_time="14:00")
        page = self.client.get(reverse("reception_calendar"))
        self.assertContains(page, "Cabin 1 is already taken by Asha Rao")

    def test_back_to_back_in_one_cabin_is_allowed(self):
        # 10:00–12:00 then 12:00–14:00 is how a room is shared, not a mistake.
        ScheduleOverride.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(12),
        )
        self._post(doctor=self.vikram.pk, start_time="12:00", end_time="14:00")
        self.assertTrue(ScheduleOverride.objects.filter(doctor=self.vikram).exists())

    def test_one_doctor_cannot_be_in_two_cabins_at_once(self):
        ScheduleOverride.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self._post(cabin=self.two.pk, start_time="11:00", end_time="15:00")
        self.assertEqual(ScheduleOverride.objects.filter(doctor=self.asha).count(), 1)

    def test_the_same_room_at_a_different_time_is_fine(self):
        ScheduleOverride.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self._post(doctor=self.vikram.pk, start_time="15:00", end_time="18:00")
        self.assertTrue(ScheduleOverride.objects.filter(doctor=self.vikram).exists())

    def test_the_first_row_for_a_doctor_does_not_clash_with_their_own_default(self):
        # Clinic-default hours are a fallback, not a commitment. Counting them
        # would make it impossible to enter the very first row for a doctor.
        self._post()
        self.assertTrue(ScheduleOverride.objects.filter(doctor=self.asha).exists())

    def test_an_override_does_not_clash_with_the_week_it_replaces(self):
        # The whole point of an override is to supersede that Monday. If the
        # weekly row it is replacing counted as occupying the cabin, no doctor
        # could ever change their hours for a single date.
        DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self._post(cabin=self.one.pk, start_time="11:00", end_time="15:00")
        self.assertTrue(ScheduleOverride.objects.filter(doctor=self.asha).exists())

    def test_a_weekly_pattern_clashes_with_another_weekly_pattern(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self._post(doctor=self.vikram.pk, repeat="weekly",
                   start_time="11:00", end_time="14:00")
        self.assertFalse(DoctorSchedule.objects.filter(doctor=self.vikram).exists())

    def test_a_weekly_pattern_clashes_with_a_one_off_weeks_ahead(self):
        # The one case a naive check misses entirely: the clash is not on the
        # date being entered, it is three Mondays later.
        third_monday = next_weekday(MONDAY, weeks=3)
        ScheduleOverride.objects.create(
            doctor=self.vikram, date=third_monday, cabin=self.one,
            start_time=time(11), end_time=time(14),
        )
        self._post(repeat="weekly", start_time="10:00", end_time="13:00")
        self.assertFalse(DoctorSchedule.objects.filter(doctor=self.asha).exists())

    def test_a_weekly_pattern_ignores_a_date_the_doctor_has_an_override_for(self):
        # On that Monday the new weekly row does not apply at all, so it cannot
        # take a cabin there and has nothing to clash with.
        third_monday = next_weekday(MONDAY, weeks=3)
        ScheduleOverride.objects.create(
            doctor=self.asha, date=third_monday, cabin=self.two,
            start_time=time(16), end_time=time(18),
        )
        ScheduleOverride.objects.create(
            doctor=self.vikram, date=third_monday, cabin=self.one,
            start_time=time(11), end_time=time(12),
        )
        self._post(repeat="weekly", start_time="10:00", end_time="13:00")
        self.assertTrue(DoctorSchedule.objects.filter(doctor=self.asha).exists())

    def test_editing_a_row_does_not_clash_with_itself(self):
        sitting = DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        conflicts = clinic_calendar.find_conflicts(
            kind="schedule", doctor=self.asha, cabin=self.one,
            start=time(10), end=time(13), weekday=MONDAY, exclude_pk=sitting.pk,
        )
        self.assertEqual(conflicts, [])

    def test_an_end_before_the_start_is_refused(self):
        self._post(start_time="15:00", end_time="10:00")
        self.assertFalse(ScheduleOverride.objects.exists())

    def test_a_zero_length_entry_is_refused(self):
        self._post(start_time="10:00", end_time="10:00")
        self.assertFalse(ScheduleOverride.objects.exists())


# ── Adding through the pop-up (FR-8 … FR-13, FR-20) ──────────────────────────

class TestTheAddEventPopUp(CalendarTestCase):
    def _post(self, **overrides):
        payload = {
            "event_type": "hours",
            "date": self.monday.isoformat(),
            "doctor": self.asha.pk,
            "cabin": self.one.pk,
            "start_time": "10:00",
            "end_time": "13:00",
            "repeat": "once",
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
        row = ScheduleOverride.objects.get(doctor=self.asha)
        self.assertEqual((row.date, row.cabin, row.start_time), (self.monday, self.one, time(10)))

    def test_a_weekly_repeat_creates_the_pattern_not_one_date(self):
        self._post(repeat="weekly")
        self.assertFalse(ScheduleOverride.objects.exists())
        self.assertEqual(DoctorSchedule.objects.get(doctor=self.asha).weekday, MONDAY)

    def test_a_date_range_creates_one_entry_per_day(self):
        self._post(until=(self.monday + timedelta(days=2)).isoformat())
        self.assertEqual(ScheduleOverride.objects.filter(doctor=self.asha).count(), 3)

    def test_a_weekly_repeat_refuses_an_end_date_rather_than_ignoring_it(self):
        # The model has no end date for a weekly pattern. Accepting one would
        # create hours that silently never stop.
        self._post(repeat="weekly", until=(self.monday + timedelta(days=30)).isoformat())
        self.assertFalse(DoctorSchedule.objects.exists())
        self.assertFalse(ScheduleOverride.objects.exists())

    def test_an_end_before_the_start_date_is_refused(self):
        self._post(until=(self.monday - timedelta(days=3)).isoformat())
        self.assertFalse(ScheduleOverride.objects.exists())

    def test_a_mistyped_year_is_refused_rather_than_creating_thousands(self):
        self._post(until=self.monday.replace(year=self.monday.year + 90).isoformat())
        self.assertFalse(ScheduleOverride.objects.exists())

    def test_a_holiday_can_be_added_from_the_same_pop_up(self):
        self._post(event_type="holiday", name="Diwali", doctor="", cabin="",
                   start_time="", end_time="")
        self.assertTrue(ClinicHoliday.objects.filter(date=self.monday).exists())

    def test_a_holiday_needs_a_name(self):
        self._post(event_type="holiday", name="", doctor="", cabin="",
                   start_time="", end_time="")
        self.assertFalse(ClinicHoliday.objects.exists())

    def test_the_same_holiday_twice_is_refused(self):
        ClinicHoliday.objects.create(date=self.monday, name="Diwali")
        self._post(event_type="holiday", name="Diwali again", doctor="", cabin="",
                   start_time="", end_time="")
        self.assertEqual(ClinicHoliday.objects.filter(date=self.monday).count(), 1)

    def test_working_hours_need_a_doctor_and_a_cabin(self):
        for missing in ("doctor", "cabin"):
            with self.subTest(missing=missing):
                self._post(**{missing: ""})
                self.assertFalse(ScheduleOverride.objects.exists())

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
        self.assertFalse(ScheduleOverride.objects.filter(doctor=self.vikram).exists())

    def test_the_refusal_says_why_rather_than_select_a_valid_choice(self):
        # Django's own message for a value outside the queryset is "Select a
        # valid choice" — true, useless, and nothing the receptionist can act
        # on. She needs to be told to re-send the invitation.
        DoctorProfile.objects.create(user=self.vikram)
        self._post(doctor=self.vikram.pk)
        page = self.client.get(reverse("reception_calendar"))
        self.assertContains(page, "has not set their password yet")


# ── Deleting, which is how leave is recorded (FR-15, FR-16) ──────────────────

class TestRemoving(CalendarTestCase):
    def setUp(self):
        super().setUp()
        self.sitting = DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )

    def _delete(self, kind, pk, **payload):
        return self.client.post(
            reverse("reception_delete_calendar_entry", args=[kind, pk]), payload
        )

    def test_the_whole_weekly_pattern_can_be_removed(self):
        self._delete("schedule", self.sitting.pk, scope="series")
        self.assertFalse(DoctorSchedule.objects.filter(pk=self.sitting.pk).exists())

    def test_removing_one_occurrence_leaves_the_series_alone(self):
        # AC-14. The row *is* every other Monday, so deleting it to clear one
        # date would take the doctor off the calendar for good.
        self._delete("schedule", self.sitting.pk, scope="occurrence",
                     date=self.monday.isoformat())
        self.assertTrue(DoctorSchedule.objects.filter(pk=self.sitting.pk).exists())

    def test_removing_one_occurrence_clears_that_date(self):
        self._delete("schedule", self.sitting.pk, scope="occurrence",
                     date=self.monday.isoformat())
        entries = self.schedule([self.asha]).entries_on(self.monday)
        self.assertTrue(all(entry.away for entry in entries))

    def test_removing_one_occurrence_leaves_the_next_week_working(self):
        self._delete("schedule", self.sitting.pk, scope="occurrence",
                     date=self.monday.isoformat())
        next_monday = self.monday + timedelta(days=7)
        entries = self.schedule([self.asha], on=next_monday).entries_on(next_monday)
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0].away)

    def test_removing_one_occurrence_leaves_a_record_of_who_and_when(self):
        # KAN-22 notes that recording leave by deletion leaves nothing to
        # report on afterwards. A weekly pattern cannot lose one date by
        # deleting the row anyway, so that case writes leave instead — which is
        # both the correct behaviour and the missing positive record.
        self._delete("schedule", self.sitting.pk, scope="occurrence",
                     date=self.monday.isoformat())
        leave = DoctorLeave.objects.get(doctor=self.asha, date=self.monday)
        self.assertEqual(leave.created_by, self.receptionist)

    def test_removing_a_date_says_who_has_to_be_rung(self):
        # The case that actually matters: patients already booked for hours
        # that have just been withdrawn.
        patient = make_patient()
        start = timezone.make_aware(
            timezone.datetime.combine(self.monday, time(11)),
            timezone.get_current_timezone(),
        )
        make_visit(patient, self.asha, start=start)

        self._delete("schedule", self.sitting.pk, scope="occurrence",
                     date=self.monday.isoformat())
        page = self.client.get(reverse("reception_calendar"))
        self.assertContains(page, patient.full_name)

    def test_a_one_off_entry_is_simply_removed(self):
        override = ScheduleOverride.objects.create(
            doctor=self.vikram, date=self.monday, cabin=self.two,
            start_time=time(16), end_time=time(18),
        )
        self._delete("override", override.pk)
        self.assertFalse(ScheduleOverride.objects.filter(pk=override.pk).exists())

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
    KAN-50 left the calendar as the only screen writing these tables.

    These were written against the availability screen it replaced. They are
    kept, pointed at the pop-up: what they check is that a cabin chosen here
    reaches the schedule row and that a clash is refused, and both still have to
    be true of whichever screen does the writing.
    """

    def _add(self, doctor, **overrides):
        payload = {
            "event_type": "hours", "doctor": doctor.pk, "cabin": self.one.pk,
            "date": self.monday.isoformat(), "repeat": "weekly",
            "start_time": "10:00", "end_time": "13:00",
        }
        payload.update(overrides)
        return self.client.post(reverse("reception_add_calendar_event"), payload)

    def test_a_cabin_can_be_chosen_there_too(self):
        self._add(self.asha)
        self.assertEqual(DoctorSchedule.objects.get(doctor=self.asha).cabin, self.one)

    def test_it_refuses_a_clash_as_well(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.one,
            start_time=time(10), end_time=time(13),
        )
        self._add(self.vikram, start_time="11:00", end_time="14:00")
        self.assertFalse(DoctorSchedule.objects.filter(doctor=self.vikram).exists())

    def test_hours_entered_there_show_on_the_calendar(self):
        self._add(self.asha)
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
