"""
KAN task #22 — a walk-in at New Bookings.

A walk-in has no appointment and is standing at the desk right now, so this is
not "pick from the grid faster" — there is no date to choose and no time to
click. The form finds today's earliest free slot itself, and the visit lands
straight in today's waiting room rather than behind a phone call to confirm it
that will never happen.
"""

from datetime import time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import ClinicHoliday, DoctorSchedule, Visit, VisitStatus

from .factories import make_doctor, make_patient, make_receptionist


class WalkInTestCase(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.patient = make_patient()
        self.client.force_login(self.receptionist)

        # Wide open every day, covering whichever hour the suite happens to
        # run at — a walk-in test that only failed after 6pm would be
        # exactly the kind of flakiness nobody notices until it does. Today
        # and the next fortnight, not just today: a doctor with any entry at
        # all stops falling back to the clinic's default hours on the dates
        # that have none, and "tomorrow" is used by several of these tests.
        today = timezone.localdate()
        for offset in range(14):
            DoctorSchedule.objects.create(
                doctor=self.doctor, date=today + timedelta(days=offset),
                start_time=time(0, 0), end_time=time(23, 45),
            )

    def _walk_in(self, **overrides):
        payload = {
            "patient": self.patient.pk,
            "doctor": self.doctor.pk,
            "walk_in": "1",
            "reason": "Walk-in review",
        }
        payload.update(overrides)
        return self.client.post(reverse("reception_new_booking"), payload)


class TestTakingAWalkIn(WalkInTestCase):
    def test_it_does_not_need_a_date_or_a_time(self):
        response = self._walk_in()
        self.assertEqual(Visit.objects.count(), 1)
        self.assertRedirects(response, reverse("reception_bookings"))

    def test_the_visit_is_scheduled_for_today(self):
        self._walk_in()
        visit = Visit.objects.get()
        self.assertEqual(
            timezone.localtime(visit.scheduled_start).date(), timezone.localdate()
        )

    def test_the_visit_lands_straight_in_the_waiting_room(self):
        self._walk_in()
        visit = Visit.objects.get()
        self.assertEqual(visit.status, VisitStatus.ARRIVED)
        self.assertIsNotNone(visit.arrived_at)

    def test_the_visit_is_flagged_as_a_walk_in(self):
        # Recorded rather than inferred from status or timing, so it is still
        # identifiable after it moves through the board, the doctor's queue,
        # or a sign-off day sheet.
        self._walk_in()
        self.assertTrue(Visit.objects.get().is_walk_in)

    def test_an_ordinary_booking_is_not_flagged(self):
        from datetime import timedelta
        from appointments import scheduling

        tomorrow = timezone.localdate() + timedelta(days=1)
        slot, _ = scheduling.available_slots(self.doctor, tomorrow)[0]
        self.client.post(reverse("reception_new_booking"), {
            "patient": self.patient.pk, "doctor": self.doctor.pk,
            "day": tomorrow.isoformat(), "slot": slot.isoformat(),
        })
        self.assertFalse(Visit.objects.get().is_walk_in)

    def test_it_appears_on_the_waiting_room_queryset(self):
        self._walk_in()
        self.assertEqual(Visit.objects.waiting_room().count(), 1)

    def test_the_slot_is_not_in_the_past(self):
        self._walk_in()
        visit = Visit.objects.get()
        self.assertGreater(visit.scheduled_end, timezone.now())

    def test_the_reason_is_kept(self):
        self._walk_in()
        self.assertEqual(Visit.objects.get().reason, "Walk-in review")

    def test_a_follow_up_walk_in_is_flagged_as_one(self):
        self._walk_in(is_follow_up="on")
        self.assertTrue(Visit.objects.get().is_follow_up)

    def test_it_is_booked_by_the_receptionist(self):
        self._walk_in()
        self.assertEqual(Visit.objects.get().booked_by, self.receptionist)

    def test_it_is_audited(self):
        from audit.models import AccessLog, AuditAction
        self._walk_in()
        entry = AccessLog.objects.filter(action=AuditAction.CREATE).get()
        self.assertEqual(entry.username, self.receptionist.username)


class TestWalkInStillNeedsADoctor(WalkInTestCase):
    def test_a_missing_doctor_is_refused(self):
        response = self._walk_in(doctor="")
        self.assertEqual(Visit.objects.count(), 0)
        self.assertContains(response, "field__error")

    def test_a_missing_patient_is_refused(self):
        response = self._walk_in(patient="")
        self.assertEqual(Visit.objects.count(), 0)
        self.assertContains(response, "field__error")


class TestADoctorWithNoRoomToday(WalkInTestCase):
    def test_a_clinic_holiday_refuses_the_walk_in(self):
        ClinicHoliday.objects.create(date=timezone.localdate(), name="Test holiday")
        response = self._walk_in()
        self.assertEqual(Visit.objects.count(), 0)
        self.assertContains(response, "no free slot left today")

    def test_the_ordinary_booking_flow_is_unaffected_by_a_full_day(self):
        # A holiday today must not stop a *scheduled* booking for tomorrow —
        # walk-in and ordinary bookings share a form, not a failure mode.
        from datetime import timedelta
        from appointments import scheduling

        ClinicHoliday.objects.create(date=timezone.localdate(), name="Test holiday")
        tomorrow = timezone.localdate() + timedelta(days=1)
        slot, _ = scheduling.available_slots(self.doctor, tomorrow)[0]
        response = self.client.post(reverse("reception_new_booking"), {
            "patient": self.patient.pk,
            "doctor": self.doctor.pk,
            "day": tomorrow.isoformat(),
            "slot": slot.isoformat(),
        })
        self.assertRedirects(response, reverse("reception_bookings"))
        self.assertEqual(Visit.objects.count(), 1)


class TestOrdinaryBookingsStillRequireADateAndSlot(WalkInTestCase):
    """Regression: making day/slot optional for walk-ins must not loosen the
    ordinary flow, which never sets walk_in."""

    def test_a_booking_without_a_date_is_refused(self):
        response = self.client.post(reverse("reception_new_booking"), {
            "patient": self.patient.pk, "doctor": self.doctor.pk,
        })
        self.assertEqual(Visit.objects.count(), 0)
        self.assertContains(response, "Choose a date.")

    def test_a_booking_without_a_time_is_refused(self):
        from datetime import timedelta
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        response = self.client.post(reverse("reception_new_booking"), {
            "patient": self.patient.pk, "doctor": self.doctor.pk, "day": tomorrow,
        })
        self.assertEqual(Visit.objects.count(), 0)
        self.assertContains(response, "Choose a time.")


class TestAWalkInCanBeMovedBackToStage1(WalkInTestCase):
    """
    Item #5 of the walk-in note: a walk-in enters Stage 2 directly but can
    still be moved back to Stage 1.

    No special-casing needed for this — Visit.previous_status is derived
    purely from the current status (BACKWARD_TRANSITIONS), never from how the
    visit got there, so a walk-in created straight at ARRIVED already works
    the same as any other visit reaching ARRIVED the ordinary way. This pins
    that down rather than leaving it to accident.
    """

    def test_the_walk_in_offers_a_way_back(self):
        self._walk_in()
        visit = Visit.objects.get()
        self.assertEqual(visit.previous_status, VisitStatus.CONFIRMED)

    def test_moving_it_back_lands_on_confirmed(self):
        self._walk_in()
        visit = Visit.objects.get()
        visit.move_back(by_user=self.receptionist)
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.CONFIRMED)

    def test_the_walk_in_flag_survives_being_moved_back(self):
        self._walk_in()
        visit = Visit.objects.get()
        visit.move_back(by_user=self.receptionist)
        visit.refresh_from_db()
        self.assertTrue(visit.is_walk_in)

    def test_the_board_button_moves_it_back(self):
        self._walk_in()
        visit = Visit.objects.get()
        self.client.post(reverse("reception_move_visit_back", args=[visit.pk]))
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.CONFIRMED)
