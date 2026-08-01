"""
The availability tables in the Django admin.

Reception has its own screen for the day-to-day work. The admin matters for the
bulk case — a year of public holidays, or every doctor's week at go-live — and
for answering "why was that slot not offered?" by looking at the rows directly.

These are smoke tests rather than behaviour tests: the admin is generated code,
and what actually breaks is a misconfigured `list_display` or an autocomplete
pointing at a model with no `search_fields`, both of which only show up when the
page is fetched.
"""

from datetime import time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import (
    ClinicHoliday, DoctorLeave, DoctorSchedule, ScheduleOverride, VisitStatus,
)

from .factories import make_doctor, make_patient, make_visit

PASSWORD = "adminpass12345"


class AdminCase(TestCase):
    def setUp(self):
        from accounts.models import User

        self.admin = User.objects.create_superuser(
            username="clinicadmin", email="admin@example.in", password=PASSWORD,
        )
        self.client.force_login(self.admin)
        self.doctor = make_doctor()
        self.day = timezone.localdate() + timedelta(days=3)

    def _url(self, model, view="changelist"):
        return reverse(f"admin:appointments_{model}_{view}")


class TestEveryAvailabilityTableIsReachable(AdminCase):
    MODELS = ("clinicholiday", "doctorschedule", "scheduleoverride", "doctorleave")

    def test_each_changelist_opens(self):
        for model in self.MODELS:
            response = self.client.get(self._url(model))
            self.assertEqual(response.status_code, 200, msg=model)

    def test_each_add_form_opens(self):
        for model in self.MODELS:
            response = self.client.get(self._url(model, "add"))
            self.assertEqual(response.status_code, 200, msg=model)

    def test_they_appear_on_the_admin_index(self):
        response = self.client.get(reverse("admin:index"))
        for label in ("Clinic holiday", "Doctor schedule", "Schedule override",
                      "Doctor leave"):
            self.assertContains(response, label)


class TestRowsRenderInTheChangelist(AdminCase):
    """A bad list_display only fails once there is a row to render with it."""

    def test_a_holiday_renders(self):
        ClinicHoliday.objects.create(date=self.day, name="Diwali")
        response = self.client.get(self._url("clinicholiday"))
        self.assertContains(response, "Diwali")

    def test_a_schedule_row_shows_the_slot_length_it_will_actually_use(self):
        DoctorSchedule.objects.create(
            doctor=self.doctor, weekday=self.day.weekday(),
            start_time=time(10, 0), end_time=time(13, 0),
        )
        response = self.client.get(self._url("doctorschedule"))
        # Empty slot_minutes must read as the clinic default, not a blank cell.
        self.assertContains(response, "clinic default")

    def test_an_explicit_slot_length_is_shown_as_given(self):
        DoctorSchedule.objects.create(
            doctor=self.doctor, weekday=self.day.weekday(),
            start_time=time(10, 0), end_time=time(13, 0), slot_minutes=30,
        )
        response = self.client.get(self._url("doctorschedule"))
        self.assertContains(response, "30 min")

    def test_an_override_renders(self):
        ScheduleOverride.objects.create(
            doctor=self.doctor, date=self.day,
            start_time=time(17, 0), end_time=time(19, 0), note="Extra evening clinic",
        )
        response = self.client.get(self._url("scheduleoverride"))
        self.assertContains(response, "Extra evening clinic")

    def test_whole_day_leave_reads_as_all_day(self):
        DoctorLeave.objects.create(doctor=self.doctor, date=self.day, reason="Conference")
        response = self.client.get(self._url("doctorleave"))
        self.assertContains(response, "All day")

    def test_part_day_leave_shows_its_hours(self):
        DoctorLeave.objects.create(
            doctor=self.doctor, date=self.day,
            start_time=time(10, 0), end_time=time(12, 0),
        )
        response = self.client.get(self._url("doctorleave"))
        self.assertContains(response, "10:00–12:00")


class TestLeaveWarnsAboutStrandedPatients(AdminCase):
    """
    Leave entered here collides with confirmed appointments just as easily as
    leave entered at reception — and nobody setting up a month of it would think
    to go and check the queue.
    """

    def setUp(self):
        super().setUp()
        start = timezone.make_aware(
            timezone.datetime.combine(self.day, time(11, 0)),
            timezone.get_current_timezone(),
        )
        self.visit = make_visit(make_patient(), self.doctor, start=start)
        self.visit.transition_to(VisitStatus.CONFIRMED, by_user=self.admin)

    def test_the_changelist_counts_the_patients_to_ring(self):
        DoctorLeave.objects.create(doctor=self.doctor, date=self.day)
        response = self.client.get(self._url("doctorleave"))
        self.assertContains(response, "1 to ring")

    def test_leave_with_nobody_booked_shows_a_dash(self):
        other = make_doctor(username="dr2", email="dr2@example.in")
        DoctorLeave.objects.create(doctor=other, date=self.day)
        response = self.client.get(self._url("doctorleave"))
        self.assertContains(response, "—")


class TestOnlyStaffReachTheAdmin(TestCase):
    def test_a_receptionist_is_redirected_to_the_admin_login(self):
        from .factories import make_receptionist

        self.client.force_login(make_receptionist())
        response = self.client.get(reverse("admin:appointments_doctorleave_changelist"))
        self.assertEqual(response.status_code, 302)
