"""
Item #5's remainder — a doctor editing their own calendar by hand, one event
at a time, rather than only through a CSV upload.

"Editing" here means what reception's own calendar already means by it: there
is no in-place time/cabin change even for reception, only remove-and-redo — so
a doctor gets the same Remove actions reception has, scoped to their own
entries, and never a clinic holiday or a whole recurring booking at once (that
stays with reception, or with re-uploading a schedule).
"""

import uuid
from datetime import date, time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import Cabin, ClinicHoliday, DoctorSchedule

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


class TestADoctorCanRemoveTheirOwnEntry(DoctorCalendarEditTestCase):
    def test_it_is_removed(self):
        entry = DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )
        self._delete("schedule", entry.pk)
        self.assertFalse(DoctorSchedule.objects.filter(pk=entry.pk).exists())

    def test_it_is_audited_as_the_doctor(self):
        from audit.models import AccessLog, AuditAction
        entry = DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )
        self._delete("schedule", entry.pk)
        record = AccessLog.objects.filter(action=AuditAction.DELETE).get()
        self.assertEqual(record.username, self.asha.username)


class TestADoctorCannotTouchAnotherDoctorsEntry(DoctorCalendarEditTestCase):
    def test_it_is_refused(self):
        entry = DoctorSchedule.objects.create(
            doctor=self.vikram, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )
        response = self._delete("schedule", entry.pk)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(DoctorSchedule.objects.filter(pk=entry.pk).exists())


class TestADoctorCanRemoveOneDateOfTheirOwnSeries(DoctorCalendarEditTestCase):
    def setUp(self):
        super().setUp()
        self.series = uuid.uuid4()
        self.first = DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13), series_id=self.series,
        )
        self.second = DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday + timedelta(weeks=1), cabin=self.cabin,
            start_time=time(10), end_time=time(13), series_id=self.series,
        )

    def test_that_date_is_removed(self):
        # "date" is the default scope, so a doctor deleting one entry of
        # their own series does not have to know to say so explicitly.
        self._delete("schedule", self.first.pk)
        self.assertFalse(DoctorSchedule.objects.filter(pk=self.first.pk).exists())

    def test_the_rest_of_the_series_survives(self):
        self._delete("schedule", self.first.pk)
        self.assertTrue(DoctorSchedule.objects.filter(pk=self.second.pk).exists())


class TestADoctorCannotRemoveTheirWholeSeries(DoctorCalendarEditTestCase):
    def setUp(self):
        super().setUp()
        self.series = uuid.uuid4()
        self.sitting = DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13), series_id=self.series,
        )

    def test_the_whole_series_is_refused(self):
        self._delete("schedule", self.sitting.pk, scope="series")
        self.assertTrue(DoctorSchedule.objects.filter(pk=self.sitting.pk).exists())


class TestADoctorCannotTouchAnotherDoctorsSeries(DoctorCalendarEditTestCase):
    def test_it_is_refused(self):
        entry = DoctorSchedule.objects.create(
            doctor=self.vikram, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13), series_id=uuid.uuid4(),
        )
        response = self._delete("schedule", entry.pk)
        self.assertEqual(response.status_code, 404)
        self.assertTrue(DoctorSchedule.objects.filter(pk=entry.pk).exists())


class TestADoctorCannotTouchAClinicHoliday(DoctorCalendarEditTestCase):
    def test_it_is_refused(self):
        holiday = ClinicHoliday.objects.create(date=self.monday, name="Diwali")
        response = self._delete("holiday", holiday.pk)
        self.assertNotEqual(response.status_code, 200)
        self.assertTrue(ClinicHoliday.objects.filter(pk=holiday.pk).exists())


class TestTheDayViewOffersTheRightButtonsToADoctor(DoctorCalendarEditTestCase):
    def test_a_doctor_sees_remove_on_a_single_entry(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )
        response = self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat()},
        )
        self.assertContains(response, "Remove")

    def test_a_doctor_does_not_see_remove_whole_booking(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13), series_id=uuid.uuid4(),
        )
        response = self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat()},
        )
        self.assertContains(response, "Remove this date")
        self.assertNotContains(response, "Remove whole booking")

    def test_reception_still_sees_remove_whole_booking(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13), series_id=uuid.uuid4(),
        )
        self.client.force_login(self.receptionist)
        response = self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat()},
        )
        self.assertContains(response, "Remove whole booking")
