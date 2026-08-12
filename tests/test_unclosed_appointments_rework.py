"""
The Unclosed appointments rework.

Three changes, one worklist:

* the tab shows the whole day's clinical worklist — every consultation
  completed today, billed or not — and a row stays on it, updated in place,
  until the day itself is signed off (see appointments.signoff.unclosed);
* the sign-off button now also waits for the calendar: every doctor's own
  scheduled sitting for the day has to have actually ended, not just for the
  board to happen to look empty in a gap between sittings;
* a previous day left unsigned holds new arrivals *and* walk-ins, not just
  arrivals — a walk-in was the one back door round KAN-49's hold.
"""

from datetime import time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments import signoff
from appointments.models import DoctorSchedule, VisitStatus
from billing.models import Charge

from .factories import (
    make_doctor, make_patient, make_receptionist, make_visit,
    set_clinic_setting, today_at,
)


def _set_sign_off_enabled(test_case, value):
    set_clinic_setting(test_case, "DAY_SIGN_OFF_ENABLED", value)


class CalendarHoursGateTestCase(TestCase):
    def setUp(self):
        _set_sign_off_enabled(self, True)
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)
        self.today = timezone.localdate()
        # A fixed moment on today's date — never the real clock — so this
        # test's outcome cannot depend on what time the suite happens to run.
        self.now = today_at(12, 0)

    def _billed_visit(self, hour=9):
        visit = make_visit(make_patient(), self.doctor, start=today_at(hour))
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED,
                       VisitStatus.IN_CABIN, VisitStatus.CONSULTED):
            visit.transition_to(status, by_user=self.receptionist)
        Charge.objects.create(
            visit=visit, patient=visit.patient,
            consultation_fee=Decimal("800"), set_by=self.doctor,
        )
        self.client.post(
            reverse("reception_generate_receipt", args=[visit.pk]),
            {"amount": "800", "method": "CASH", "reference": "", "notes": ""},
        )
        return visit


class TestTheCalendarHoursGate(CalendarHoursGateTestCase):
    def test_sign_off_waits_while_a_doctor_is_still_within_scheduled_hours(self):
        # 08:00-14:00, and "now" is midday — the doctor is still sitting,
        # even though the board is otherwise completely clear.
        DoctorSchedule.objects.create(
            doctor=self.doctor, date=self.today,
            start_time=time(8, 0), end_time=time(14, 0),
        )
        self._billed_visit()
        self.assertFalse(signoff.can_close(self.today, now=self.now))
        self.assertIsNone(signoff.day_to_close(now=self.now))

    def test_sign_off_is_offered_once_the_scheduled_hours_have_passed(self):
        DoctorSchedule.objects.create(
            doctor=self.doctor, date=self.today,
            start_time=time(8, 0), end_time=time(11, 0),
        )
        self._billed_visit()
        self.assertTrue(signoff.can_close(self.today, now=self.now))
        self.assertEqual(signoff.day_to_close(now=self.now), self.today)

    def test_a_day_with_no_schedule_entries_at_all_has_nothing_to_wait_for(self):
        # Nothing on the calendar for anyone — the gate does not invent a
        # reason to hold the day when there was nothing to wait for.
        self._billed_visit()
        self.assertTrue(signoff.can_close(self.today, now=self.now))

    def test_the_latest_of_several_sittings_is_what_the_gate_waits_for(self):
        other_doctor = make_doctor(username="dr-second", email="second@example.in")
        DoctorSchedule.objects.create(
            doctor=self.doctor, date=self.today,
            start_time=time(8, 0), end_time=time(11, 0),
        )
        DoctorSchedule.objects.create(
            doctor=other_doctor, date=self.today,
            start_time=time(14, 0), end_time=time(18, 0),
        )
        self._billed_visit()
        # Only one doctor is actually finished by midday; the other's own
        # sitting still has hours left on it.
        self.assertFalse(signoff.can_close(self.today, now=self.now))

    def test_the_gate_does_not_apply_to_a_previous_unsigned_day(self):
        # Yesterday has fully elapsed by definition, so nothing on today's
        # calendar can hold it open — this is what lets "sign off the next
        # working day" work at all.
        yesterday = self.today - timedelta(days=1)
        DoctorSchedule.objects.create(
            doctor=self.doctor, date=yesterday,
            start_time=time(8, 0), end_time=time(20, 0),
        )
        visit = make_visit(make_patient(), self.doctor, start=today_at(9, days=-1))
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED,
                       VisitStatus.IN_CABIN, VisitStatus.CONSULTED):
            visit.transition_to(status, by_user=self.receptionist)
        Charge.objects.create(
            visit=visit, patient=visit.patient,
            consultation_fee=Decimal("800"), set_by=self.doctor,
        )
        self.client.post(
            reverse("reception_generate_receipt", args=[visit.pk]),
            {"amount": "800", "method": "CASH", "reference": "", "notes": ""},
        )
        self.assertTrue(signoff.can_close(yesterday, now=self.now))
        self.assertEqual(signoff.day_to_close(now=self.now), yesterday)


class TestTheWorklistStaysPutUntilSignedOff(CalendarHoursGateTestCase):
    def test_a_billed_row_keeps_its_place_with_a_reprint_link_not_generate(self):
        visit = self._billed_visit()
        response = self.client.get(reverse("reception_bookings"), {"tab": "unclosed"})
        self.assertContains(response, visit.patient.patient_id)
        self.assertContains(response, "View &amp; reprint")
        self.assertNotContains(response, "Generate receipt")

    def test_signing_off_finally_clears_it(self):
        self._billed_visit()
        DoctorSchedule.objects.filter(date=self.today).delete()  # nothing to wait for
        self.client.post(
            reverse("reception_close_day"), {"date": self.today.isoformat()},
        )
        self.assertEqual(signoff.unclosed(), [])


class TestWalkInsAreHeldTheSameWayArrivalsAre(TestCase):
    def setUp(self):
        _set_sign_off_enabled(self, True)
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)
        self.yesterday = timezone.localdate() - timedelta(days=1)
        # An unsigned, unbilled consultation yesterday — the ordinary way a
        # day is left open, per TestTheNextMorningIsHeldUp.
        stale = make_visit(make_patient(), self.doctor, start=today_at(10, days=-1))
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED,
                       VisitStatus.IN_CABIN, VisitStatus.CONSULTED):
            stale.transition_to(status, by_user=self.receptionist)
        Charge.objects.create(
            visit=stale, patient=stale.patient,
            consultation_fee=Decimal("800"), set_by=self.doctor,
        )

    def _walk_in(self):
        patient = make_patient(phone="9820094949")
        return self.client.post(reverse("reception_new_booking"), {
            "patient": patient.pk, "doctor": self.doctor.pk, "walk_in": "on",
            "reason": "", "is_follow_up": "",
        })

    def test_a_walk_in_is_refused_while_yesterday_is_unsigned(self):
        response = self._walk_in()
        self.assertEqual(response.status_code, 200)  # redisplayed with an error
        self.assertContains(response, "has not been signed off")
        self.assertFalse(VisitStatus.ARRIVED in
                          [v.status for v in self.doctor.visits_as_doctor.all()])

    def test_the_new_booking_screen_explains_the_hold(self):
        response = self.client.get(reverse("reception_new_booking"))
        self.assertContains(response, "has not been signed off yet")
        self.assertContains(response, "disabled")

    def test_a_walk_in_succeeds_once_the_day_is_clear(self):
        DoctorSchedule.objects.filter(date=self.yesterday).delete()
        # Bill and sign yesterday off first.
        stale = self.doctor.visits_as_doctor.get(status=VisitStatus.CONSULTED)
        self.client.post(
            reverse("reception_generate_receipt", args=[stale.pk]),
            {"amount": "800", "method": "CASH", "reference": "", "notes": ""},
        )
        self.client.post(
            reverse("reception_close_day"), {"date": self.yesterday.isoformat()},
        )
        response = self._walk_in()
        self.assertEqual(response.status_code, 302)
