"""
A doctor uploading their own schedule.

Reception's rota importer already exists and is unchanged; this is the
doctor's own version of it — same file format, same "Replace my schedule
for these dates" option, but fenced so a doctor can only ever move their own
hours, never another doctor's.
"""

from datetime import date
from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments import schedules_csv
from appointments.models import Cabin, ScheduleOverride

from .factories import make_doctor, make_patient, make_receptionist, make_visit


def _upload(text, name="rota.csv"):
    handle = BytesIO(text.encode("utf-8"))
    handle.name = name
    return handle


HEADER = ",".join(schedules_csv.COLUMNS) + "\n"


class OwnScheduleTestCase(TestCase):
    def setUp(self):
        self.asha = make_doctor(username="dr-asha", email="asha@example.in",
                                first_name="Asha", last_name="Rao")
        self.vikram = make_doctor(username="dr-vikram", email="vikram@example.in",
                                  first_name="Vikram", last_name="Joshi")
        self.receptionist = make_receptionist()
        self.one = Cabin.objects.create(name="Cabin 1")
        self.two = Cabin.objects.create(name="Cabin 2")
        self.client.force_login(self.asha)

    def _row(self, **overrides):
        row = {
            "doctor_email": "asha@example.in", "cabin": "Cabin 1",
            "start_date": "01-09-2026", "end_date": "04-09-2026",
            "days": "M-T-W-Th-F", "start_time": "10:00", "end_time": "13:00",
        }
        row.update(overrides)
        return ",".join(row[name] for name in schedules_csv.COLUMNS) + "\n"

    def _check(self, text, replace=False):
        payload = {"file": _upload(text)}
        if replace:
            payload["replace"] = "1"
        return self.client.post(reverse("doctor_import_schedule"), payload)

    def _seed_via_reception(self, text):
        """
        Give a doctor an existing schedule the way reception legitimately
        would — not through the doctor's own fenced upload, which is the
        thing under test and would refuse a row for anyone but the doctor
        uploading it.
        """
        self.client.force_login(self.receptionist)
        self.client.post(
            reverse("reception_import_schedules"),
            {"file": _upload(text), "confirm": "1"},
        )
        self.client.force_login(self.asha)

    def _confirm(self, text, replace=False):
        payload = {"file": _upload(text), "confirm": "1"}
        if replace:
            payload["replace"] = "1"
        return self.client.post(reverse("doctor_import_schedule"), payload, follow=True)


class TestUploadingMyOwnHours(OwnScheduleTestCase):
    def test_it_reaches_the_page(self):
        self.assertEqual(self.client.get(reverse("doctor_import_schedule")).status_code, 200)

    def test_the_calendar_links_to_it(self):
        response = self.client.get(reverse("reception_calendar"))
        self.assertContains(response, reverse("doctor_import_schedule"))
        self.assertNotContains(response, reverse("reception_import_schedules"))

    def test_it_creates_entries_for_myself(self):
        self._confirm(HEADER + self._row())
        self.assertEqual(ScheduleOverride.objects.filter(doctor=self.asha).count(), 4)

    def test_a_receptionist_cannot_reach_this_page(self):
        self.client.force_login(self.receptionist)
        self.assertEqual(self.client.get(reverse("doctor_import_schedule")).status_code, 403)

    def test_a_row_for_another_doctor_is_refused(self):
        response = self._check(HEADER + self._row(doctor_email="vikram@example.in"))
        self.assertContains(response, "you can only upload your own schedule")

    def test_a_row_for_another_doctor_creates_nothing(self):
        self._confirm(HEADER + self._row(doctor_email="vikram@example.in"))
        self.assertFalse(ScheduleOverride.objects.filter(doctor=self.vikram).exists())

    def test_a_retired_cabin_is_refused(self):
        self.two.is_active = False
        self.two.save()
        response = self._check(HEADER + self._row(cabin="Cabin 2"))
        self.assertContains(response, "retired")
        self.assertEqual(ScheduleOverride.objects.count(), 0)

    def test_a_mixed_file_keeps_my_rows_and_refuses_the_rest(self):
        text = (
            HEADER
            + self._row()
            + self._row(doctor_email="vikram@example.in", cabin="Cabin 2")
        )
        response = self._confirm(text)
        self.assertEqual(ScheduleOverride.objects.filter(doctor=self.asha).count(), 4)
        self.assertFalse(ScheduleOverride.objects.filter(doctor=self.vikram).exists())
        self.assertContains(response, "could not be used")


class TestReplacingMyOwnSchedule(OwnScheduleTestCase):
    def test_without_replace_a_changed_time_is_refused(self):
        self._confirm(HEADER + self._row(start_time="10:00", end_time="13:00"))
        response = self._check(HEADER + self._row(start_time="11:00", end_time="14:00"))
        self.assertContains(response, "already")
        self.assertEqual(
            ScheduleOverride.objects.filter(doctor=self.asha, start_time="10:00:00").count(), 4,
        )

    def test_replace_lets_the_new_time_through(self):
        self._confirm(HEADER + self._row(start_time="10:00", end_time="13:00"))
        self._confirm(
            HEADER + self._row(start_time="11:00", end_time="14:00"), replace=True,
        )
        self.assertEqual(
            ScheduleOverride.objects.filter(doctor=self.asha, start_time="10:00:00").count(), 0,
        )
        self.assertEqual(
            ScheduleOverride.objects.filter(doctor=self.asha, start_time="11:00:00").count(), 4,
        )

    def test_replace_can_add_a_day_at_the_same_time_as_changing_others(self):
        self._confirm(HEADER + self._row(start_time="10:00", end_time="13:00"))
        self._confirm(
            HEADER + self._row(start_time="11:00", end_time="14:00",
                               days="M-T-W-Th-F-Sa", end_date="05-09-2026"),
            replace=True,
        )
        self.assertEqual(ScheduleOverride.objects.filter(doctor=self.asha).count(), 5)
        self.assertTrue(
            ScheduleOverride.objects.filter(
                doctor=self.asha, date=date(2026, 9, 5),
            ).exists()
        )

    def test_replace_cannot_touch_another_doctors_hours_even_if_named(self):
        # Vikram's row is refused before the replace/commit step ever sees
        # it, so his existing hours cannot be part of what gets cleared.
        self._seed_via_reception(HEADER + self._row(
            doctor_email="vikram@example.in", cabin="Cabin 2",
        ))
        self._confirm(
            HEADER + self._row(doctor_email="vikram@example.in", cabin="Cabin 2",
                               start_time="11:00", end_time="14:00"),
            replace=True,
        )
        self.assertEqual(
            ScheduleOverride.objects.filter(doctor=self.vikram, start_time="10:00:00").count(), 4,
        )

    def test_replace_still_refuses_a_genuine_cabin_clash_with_another_doctor(self):
        self._seed_via_reception(HEADER + self._row(
            doctor_email="vikram@example.in", cabin="Cabin 1",
            start_time="11:00", end_time="14:00",
        ))
        response = self._check(
            HEADER + self._row(cabin="Cabin 1", start_time="11:00", end_time="14:00"),
            replace=True,
        )
        self.assertContains(response, "already taken")

    def test_the_preview_warns_about_my_own_bookings(self):
        self._confirm(HEADER + self._row(start_time="10:00", end_time="13:00"))
        patient = make_patient(phone="9820088888")
        make_visit(patient, self.asha, start=timezone.make_aware(
            timezone.datetime(2026, 9, 1, 10, 30)
        ))
        response = self._check(
            HEADER + self._row(start_time="11:00", end_time="14:00"), replace=True,
        )
        self.assertContains(response, "already booked")
