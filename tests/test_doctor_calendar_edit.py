"""
Item #5's remainder — a doctor editing their own calendar by hand, one event
at a time, rather than only through a CSV upload.

A doctor now has the same reach into their own working hours reception has:
add, edit and remove, one date or a whole booking at once — the same
CalendarEventForm, the same conflict check and the same cabin allocation
(see TestEditingAnEvent / TestTheAddEventPopUp in test_calendar.py for that
machinery), just always scoped to themselves. A clinic holiday stays
reception-only.
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

    def _add(self, **overrides):
        payload = {
            "event_type": "hours",
            "date": self.monday.isoformat(),
            "start_time": "10:00", "end_time": "13:00",
        }
        payload.update(overrides)
        return self.client.post(reverse("reception_add_calendar_event"), payload)

    def _edit(self, pk, **overrides):
        payload = {
            "event_type": "hours",
            "date": self.monday.isoformat(),
            "start_time": "10:00", "end_time": "13:00",
        }
        payload.update(overrides)
        return self.client.post(
            reverse("reception_edit_calendar_event", args=[pk]), payload
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


class TestADoctorCanRemoveTheirWholeSeries(DoctorCalendarEditTestCase):
    def setUp(self):
        super().setUp()
        self.series = uuid.uuid4()
        self.rows = [
            DoctorSchedule.objects.create(
                doctor=self.asha, date=self.monday + timedelta(weeks=n),
                cabin=self.cabin, start_time=time(10), end_time=time(13),
                series_id=self.series,
            )
            for n in range(3)
        ]

    def test_the_whole_series_is_removed(self):
        self._delete("schedule", self.rows[0].pk, scope="series")
        self.assertEqual(DoctorSchedule.objects.filter(series_id=self.series).count(), 0)

    def test_it_is_audited_as_the_doctor(self):
        from audit.models import AccessLog, AuditAction
        self._delete("schedule", self.rows[0].pk, scope="series")
        record = AccessLog.objects.filter(action=AuditAction.DELETE).latest("created_at")
        self.assertEqual(record.username, self.asha.username)


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

    def test_a_doctor_sees_edit_on_their_own_entry(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )
        response = self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat()},
        )
        self.assertContains(response, "Edit")

    def test_a_doctor_now_sees_remove_whole_booking_on_their_own_series(self):
        DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13), series_id=uuid.uuid4(),
        )
        response = self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat()},
        )
        self.assertContains(response, "Remove this date")
        self.assertContains(response, "Remove whole booking")

    def test_a_doctor_sees_add_working_hours(self):
        response = self.client.get(
            reverse("reception_calendar"),
            {"view": "day", "date": self.monday.isoformat()},
        )
        self.assertContains(response, "Add working hours")

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


class TestADoctorCanAddTheirOwnHours(DoctorCalendarEditTestCase):
    def test_a_single_day_is_created(self):
        self._add()
        row = DoctorSchedule.objects.get(doctor=self.asha)
        self.assertEqual((row.date, row.start_time), (self.monday, time(10)))

    def test_a_recurring_booking_creates_one_row_per_date(self):
        self._add(
            is_recurring="1", weekdays=["M"],
            recur_until=(self.monday + timedelta(weeks=2)).isoformat(),
        )
        rows = DoctorSchedule.objects.filter(doctor=self.asha)
        self.assertEqual(rows.count(), 3)
        self.assertEqual(len({r.series_id for r in rows}), 1)

    def test_it_is_audited_as_the_doctor(self):
        from audit.models import AccessLog, AuditAction
        self._add()
        record = AccessLog.objects.filter(action=AuditAction.CREATE).get()
        self.assertEqual(record.username, self.asha.username)


class TestADoctorCannotAddForAnotherDoctor(DoctorCalendarEditTestCase):
    def test_the_doctor_field_is_ignored(self):
        # Whatever a crafted request names, the hours land against whoever
        # is actually signed in — the field is not even rendered for a
        # doctor, so this is the server's own guarantee, not the client's.
        self._add(doctor=self.vikram.pk)
        self.assertFalse(DoctorSchedule.objects.filter(doctor=self.vikram).exists())
        self.assertTrue(DoctorSchedule.objects.filter(doctor=self.asha).exists())


class TestADoctorCannotAddAHoliday(DoctorCalendarEditTestCase):
    def test_the_event_type_is_ignored(self):
        self._add(event_type="holiday", name="Diwali")
        self.assertFalse(ClinicHoliday.objects.exists())
        # Forced to "hours" instead, so the submission still goes through
        # as working hours rather than being silently dropped.
        self.assertTrue(DoctorSchedule.objects.filter(doctor=self.asha).exists())


class TestADoctorCanEditTheirOwnEntry(DoctorCalendarEditTestCase):
    def setUp(self):
        super().setUp()
        self.sitting = DoctorSchedule.objects.create(
            doctor=self.asha, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )

    def test_the_time_can_be_changed(self):
        self._edit(self.sitting.pk, start_time="11:00", end_time="14:00")
        self.sitting.refresh_from_db()
        self.assertEqual((self.sitting.start_time, self.sitting.end_time),
                          (time(11), time(14)))

    def test_it_is_audited_as_the_doctor(self):
        from audit.models import AccessLog, AuditAction
        self._edit(self.sitting.pk, start_time="11:00", end_time="14:00")
        record = AccessLog.objects.filter(action=AuditAction.UPDATE).get()
        self.assertEqual(record.username, self.asha.username)


class TestADoctorCannotEditAnotherDoctorsEntry(DoctorCalendarEditTestCase):
    def test_it_is_refused(self):
        entry = DoctorSchedule.objects.create(
            doctor=self.vikram, date=self.monday, cabin=self.cabin,
            start_time=time(10), end_time=time(13),
        )
        response = self._edit(entry.pk, start_time="11:00", end_time="14:00")
        self.assertEqual(response.status_code, 404)
        entry.refresh_from_db()
        self.assertEqual(entry.start_time, time(10))
