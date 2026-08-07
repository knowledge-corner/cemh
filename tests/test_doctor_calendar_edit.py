"""
Item #5's remainder — a doctor editing their own calendar by hand, one event
at a time, rather than only through a CSV upload.

"Editing" here means what reception's own calendar already means by it: there
is no in-place time/cabin change even for reception, only remove-and-redo — so
a doctor gets the same Remove / "Off this day" / "Not away after all" actions
reception has, scoped to their own entries, and never a clinic holiday or their
whole weekly pattern (that stays with re-uploading a schedule).
"""

from datetime import date, time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import (
    Cabin, ClinicHoliday, DoctorLeave, DoctorSchedule, ScheduleOverride,
)

from .factories import make_doctor, make_receptionist


def next_weekday(weekday, *, weeks=1):
    day = timezone.localdate() + timedelta(days=1)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day + timedelta(weeks=weeks - 1)


MONDAY = 0


class DoctorCalendarEditTestCase(TestCase):
    def setUp(self):
        self.receptionist = make_receptionist()
        self.asha = make_doctor(
            username="dr-asha", email="asha@example.in",
            first_name="Asha", last_name="Rao",
        )
        self.vikram = make_doctor(
            username="dr-vikram", email="vikram@example.in",
            first_name="Vikram", last_name="Joshi",
        )
        self.cabin = Cabin.objects.create(name="Cabin 1")
        self.monday = next_weekday(MONDAY)
        self.client.force_login(self.asha)

    def _delete(self, kind, pk, **payload):
        return self.client.post(
            reverse("reception_delete_calendar_entry", args=[kind, pk]), payload
        )


class TestADoctorCanRemoveTheirOwnOverride(DoctorCalendarEditTestCase):
    def test_it_is_removed(self):
        override = ScheduleOverride.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )
        self._delete("override", override.pk)
        self.assertFalse(ScheduleOverride.objects.filter(pk=override.pk).exists())

    def test_it_is_audited_as_the_doctor(self):
        from audit.models import AccessLog, AuditAction
        override = ScheduleOverride.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )
        self._delete("override", override.pk)
        entry = AccessLog.objects.filter(action=AuditAction.DELETE).get()
        self.assertEqual(entry.username, self.asha.username)


class TestADoctorCannotTouchAnotherDoctorsOverride(DoctorCalendarEditTestCase):
    def test_it_is_refused(self):
        override = ScheduleOverride.objects.create(
            doctor=self.vikram, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )
        response = self._delete("override", override.pk)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(ScheduleOverride.objects.filter(pk=override.pk).exists())


class TestADoctorCanClearOneOccurrenceOfTheirOwnWeek(DoctorCalendarEditTestCase):
    def setUp(self):
        super().setUp()
        self.sitting = DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )

    def test_the_series_survives(self):
        self._delete("schedule", self.sitting.pk, scope="occurrence",
                      date=self.monday.isoformat())
        self.assertTrue(DoctorSchedule.objects.filter(pk=self.sitting.pk).exists())

    def test_that_date_becomes_leave(self):
        self._delete("schedule", self.sitting.pk, scope="occurrence",
                      date=self.monday.isoformat())
        self.assertTrue(
            DoctorLeave.objects.filter(doctor=self.asha, date=self.monday).exists()
        )

    def test_the_leave_is_attributed_to_the_doctor_themselves(self):
        self._delete("schedule", self.sitting.pk, scope="occurrence",
                      date=self.monday.isoformat())
        leave = DoctorLeave.objects.get(doctor=self.asha, date=self.monday)
        self.assertEqual(leave.created_by, self.asha)


class TestADoctorCannotRemoveTheirWholeWeeklyPattern(DoctorCalendarEditTestCase):
    def setUp(self):
        super().setUp()
        self.sitting = DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )

    def test_the_series_is_refused(self):
        self._delete("schedule", self.sitting.pk, scope="series")
        self.assertTrue(DoctorSchedule.objects.filter(pk=self.sitting.pk).exists())

    def test_series_is_the_default_scope_and_is_still_refused(self):
        # The endpoint defaults scope to "series" when it is not posted at
        # all — a doctor omitting it must not fall through to the one action
        # that is meant to be unavailable to them.
        self._delete("schedule", self.sitting.pk)
        self.assertTrue(DoctorSchedule.objects.filter(pk=self.sitting.pk).exists())


class TestADoctorCannotTouchAnotherDoctorsWeeklyPattern(DoctorCalendarEditTestCase):
    def test_occurrence_removal_is_refused(self):
        sitting = DoctorSchedule.objects.create(
            doctor=self.vikram, weekday=MONDAY, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )
        response = self._delete("schedule", sitting.pk, scope="occurrence",
                                 date=self.monday.isoformat())
        self.assertEqual(response.status_code, 404)
        self.assertFalse(DoctorLeave.objects.filter(doctor=self.vikram).exists())


class TestADoctorCanCancelTheirOwnLeave(DoctorCalendarEditTestCase):
    def test_it_is_removed(self):
        leave = DoctorLeave.objects.create(doctor=self.asha, date=self.monday)
        self._delete("leave", leave.pk)
        self.assertFalse(DoctorLeave.objects.filter(pk=leave.pk).exists())

    def test_another_doctors_leave_cannot_be_touched(self):
        leave = DoctorLeave.objects.create(doctor=self.vikram, date=self.monday)
        response = self._delete("leave", leave.pk)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(DoctorLeave.objects.filter(pk=leave.pk).exists())


class TestADoctorCannotTouchAClinicHoliday(DoctorCalendarEditTestCase):
    def test_it_is_refused(self):
        holiday = ClinicHoliday.objects.create(date=self.monday, name="Diwali")
        response = self._delete("holiday", holiday.pk)
        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(ClinicHoliday.objects.filter(pk=holiday.pk).exists())


class TestTheDayViewOffersTheRightButtonsToADoctor(DoctorCalendarEditTestCase):
    def test_a_doctor_sees_remove_on_their_own_override(self):
        ScheduleOverride.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )
        response = self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat()},
        )
        self.assertContains(response, "Remove")

    def test_a_doctor_does_not_see_remove_every_week(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )
        response = self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat()},
        )
        self.assertContains(response, "Off this")
        self.assertNotContains(response, "Remove every week")

    def test_reception_still_sees_remove_every_week(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, weekday=MONDAY, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )
        self.client.force_login(self.receptionist)
        response = self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat()},
        )
        self.assertContains(response, "Remove every week")
