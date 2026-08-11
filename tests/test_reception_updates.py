"""
The reception changes asked for after the first round of use.

Most of these are rules rather than screens, and every one of them came from
something that actually went wrong at the desk: a confirm button that rendered
a bare fragment, a booking taken for last Tuesday, two patients shown in one
cabin, and yesterday's queue still sitting on today's board.
"""

from datetime import time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments import scheduling
from appointments.models import (
    ClinicHoliday, DoctorSchedule, InvalidTransition, Visit, VisitStatus,
)

from .factories import (
    give_wide_open_hours, make_doctor, make_patient, make_receptionist, make_visit,
    set_clinic_setting, today_at,
)


def _next_working_day():
    """
    The soonest day the clinic is open.

    Now that the clinic runs every day this is simply tomorrow, but it still
    consults the scheduler rather than assuming: a test that hard-codes
    "tomorrow" would start failing the moment somebody enters a holiday.
    """
    day = timezone.localdate() + timedelta(days=1)
    for _ in range(10):
        if scheduling.is_working_day(day):
            return day
        day += timedelta(days=1)
    raise AssertionError("No working day in the next ten days.")


def _tomorrow_at(hour):
    return timezone.make_aware(
        timezone.datetime.combine(_next_working_day(), time(hour, 0)),
        timezone.get_current_timezone(),
    )


class TestConfirmDeclineReturnTheRightThing(TestCase):
    """
    The reported bug. The bookings page posts an ordinary form; it was getting
    the queue-board fragment back, which rendered a partial with no page around
    it. The board posts over HTMX and does want that fragment.
    """

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.visit = make_visit(
            make_patient(), make_doctor(), start=today_at(10)
        )

    def _move(self, status, **kwargs):
        return self.client.post(
            reverse("reception_move_visit", args=[self.visit.pk, status]), **kwargs
        )

    def test_a_plain_form_post_redirects_rather_than_returning_a_fragment(self):
        response = self._move(VisitStatus.CONFIRMED)
        self.assertEqual(response.status_code, 302)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.CONFIRMED)

    def test_it_returns_to_the_page_the_button_was_on(self):
        target = reverse("reception_bookings") + "?tab=calls"
        response = self._move(VisitStatus.CONFIRMED, data={"next": target})
        self.assertRedirects(response, target)

    def test_declining_behaves_the_same_way(self):
        response = self._move(VisitStatus.CANCELLED)
        self.assertEqual(response.status_code, 302)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.CANCELLED)

    def test_the_queue_board_still_gets_its_fragment(self):
        response = self._move(VisitStatus.CONFIRMED, headers={"HX-Request": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<html")


class TestOnlyOneInCabin(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        # Fixed times, not offsets from the clock: two visits an hour apart
        # land on different days when the suite runs late in the evening, and
        # the one-per-cabin rule is scoped to the visit's own day — so the test
        # would stop testing anything without failing.
        self.first = make_visit(make_patient(), self.doctor, start=today_at(10))
        self.second = make_visit(make_patient(phone="9820011111"), self.doctor,
                                 start=today_at(11))
        for visit in (self.first, self.second):
            visit.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
            visit.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)

    def test_a_second_patient_cannot_be_sent_in(self):
        self.first.transition_to(VisitStatus.IN_CABIN, by_user=self.receptionist)
        with self.assertRaises(InvalidTransition):
            self.second.transition_to(VisitStatus.IN_CABIN, by_user=self.receptionist)

    def test_the_refusal_names_who_is_already_in_there(self):
        self.first.transition_to(VisitStatus.IN_CABIN, by_user=self.receptionist)
        with self.assertRaises(InvalidTransition) as caught:
            self.second.transition_to(VisitStatus.IN_CABIN, by_user=self.receptionist)
        self.assertIn(self.first.patient.full_name, str(caught.exception))

    def test_the_next_patient_goes_in_once_the_first_is_done(self):
        self.first.transition_to(VisitStatus.IN_CABIN, by_user=self.receptionist)
        self.first.transition_to(VisitStatus.CONSULTED, by_user=self.receptionist)
        self.second.transition_to(VisitStatus.IN_CABIN, by_user=self.receptionist)
        self.assertEqual(self.second.status, VisitStatus.IN_CABIN)

    def test_a_visit_left_open_yesterday_does_not_block_todays_clinic(self):
        # Found only because seeding a fresh install failed: a stale in-cabin
        # visit is a queue nobody closed, not a consultation in progress, and
        # letting it stop today's clinic turns tidying-up into a stoppage.
        stale = make_visit(make_patient(phone="9820033333"), self.doctor,
                           start=timezone.now() - timedelta(days=1))
        for step in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.IN_CABIN):
            stale.transition_to(step, by_user=self.receptionist)

        self.first.transition_to(VisitStatus.IN_CABIN, by_user=self.receptionist)
        self.assertEqual(self.first.status, VisitStatus.IN_CABIN)

    def test_another_doctor_is_unaffected(self):
        other = make_doctor(username="dr2", email="dr2@example.in")
        theirs = make_visit(make_patient(phone="9820022222"), other,
                            start=today_at(12))
        theirs.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
        theirs.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)

        self.first.transition_to(VisitStatus.IN_CABIN, by_user=self.receptionist)
        theirs.transition_to(VisitStatus.IN_CABIN, by_user=self.receptionist)
        self.assertEqual(theirs.status, VisitStatus.IN_CABIN)


class TestYesterdaysQueueIsNotLeftOpen(TestCase):
    """
    The plain end-of-day sweep — what "Close them off" did before KAN-48, and
    what it still does with sign-off switched off. See
    appointments.signoff._sweep_only's own docstring: with sign-off on, an
    old visit still IN_CABIN instead refuses the close outright rather than
    being swept, which is covered separately in test_day_signoff.py.
    """

    def setUp(self):
        set_clinic_setting(self, "DAY_SIGN_OFF_ENABLED", False)
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.doctor = make_doctor()
        self.old = make_visit(
            make_patient(), self.doctor, start=timezone.now() - timedelta(days=2)
        )

    def test_todays_board_does_not_list_it_as_todays_work(self):
        response = self.client.get(reverse("reception_home"))
        self.assertEqual(response.context["total"], 0)

    def test_closing_the_day_lapses_a_booking_nobody_ever_confirmed(self):
        self.client.post(reverse("reception_close_day"))
        self.old.refresh_from_db()
        self.assertEqual(self.old.status, VisitStatus.CANCELLED)

    def test_a_confirmed_booking_the_patient_missed_becomes_a_no_show(self):
        # The distinction matters: a lapsed booking and a patient who promised
        # to come and did not are different numbers to the clinic.
        self.old.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
        self.client.post(reverse("reception_close_day"))
        self.old.refresh_from_db()
        self.assertEqual(self.old.status, VisitStatus.NO_SHOW)

    def test_a_patient_left_in_the_cabin_overnight_is_closed_off(self):
        self.old.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
        self.old.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)
        self.old.transition_to(VisitStatus.IN_CABIN, by_user=self.receptionist)

        self.client.post(reverse("reception_close_day"))
        self.old.refresh_from_db()
        self.assertEqual(self.old.status, VisitStatus.CONSULTED)

    def test_the_sweep_leaves_a_trail(self):
        self.client.post(reverse("reception_close_day"))
        note = self.old.status_events.latest("created_at").note
        self.assertIn("end-of-day", note.lower())

    def test_a_visit_awaiting_payment_is_not_swept_away(self):
        # Money still has to be collected — that is a real task, not an untidy row.
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED,
                       VisitStatus.IN_CABIN, VisitStatus.CONSULTED):
            self.old.transition_to(status, by_user=self.receptionist)
        self.client.post(reverse("reception_close_day"))
        self.old.refresh_from_db()
        self.assertEqual(self.old.status, VisitStatus.CONSULTED)


class TestTodayOnlyOnTheQueue(TestCase):
    def setUp(self):
        self.client.force_login(make_receptionist())

    def test_the_board_ignores_a_day_parameter(self):
        # The date picker was removed; a stale bookmark must not resurrect it.
        response = self.client.get(reverse("reception_home"), {"day": "2020-01-01"})
        self.assertEqual(response.context["day"], timezone.localdate())

    def test_no_date_paging_links_are_offered(self):
        response = self.client.get(reverse("reception_home"))
        self.assertNotContains(response, "?day=")


class TestPastDatesCannotBeBooked(TestCase):
    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.doctor = make_doctor()
        give_wide_open_hours(self.doctor)
        self.patient = make_patient()

    def _post(self, day, slot):
        return self.client.post(reverse("reception_new_booking"), {
            "patient": self.patient.pk,
            "doctor": self.doctor.pk,
            "day": day.strftime("%Y-%m-%d"),
            "slot": slot.isoformat(),
            "reason": "Review",
        })

    def test_a_date_in_the_past_is_refused(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        slot = timezone.make_aware(
            timezone.datetime.combine(yesterday, time(11, 0)),
            timezone.get_current_timezone(),
        )
        response = self._post(yesterday, slot)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "has passed")
        self.assertFalse(Visit.objects.filter(patient=self.patient).exists())

    def test_a_future_date_is_accepted(self):
        slot = _tomorrow_at(11)
        response = self._post(slot.date(), slot)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Visit.objects.filter(patient=self.patient).exists())


class TestDoctorAvailability(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.day = timezone.localdate() + timedelta(days=2)

    def test_a_clinic_holiday_closes_the_day_for_everyone(self):
        ClinicHoliday.objects.create(date=self.day, name="Diwali")
        self.assertFalse(scheduling.is_working_day(self.day, self.doctor))
        self.assertEqual(scheduling.available_slots(self.doctor, self.day), [])

    def test_a_doctor_with_no_schedule_has_no_slots(self):
        # There is no clinic-wide default to fall back to any more —
        # availability is read entirely off the calendar.
        self.assertEqual(scheduling.day_slots(self.day, self.doctor), [])

    def test_a_schedule_entry_produces_its_own_slots(self):
        DoctorSchedule.objects.create(
            doctor=self.doctor, date=self.day,
            start_time=time(10, 0), end_time=time(11, 0), slot_minutes=30,
        )
        slots = scheduling.day_slots(self.day, self.doctor)
        self.assertEqual(len(slots), 2)

    def test_an_entry_on_another_date_does_not_cover_this_one(self):
        # Every entry names its own date — an entry elsewhere does not make
        # this date bookable, the same as if the doctor had no rows at all.
        DoctorSchedule.objects.create(
            doctor=self.doctor, date=self.day + timedelta(days=1),
            start_time=time(10, 0), end_time=time(13, 0),
        )
        self.assertFalse(scheduling.is_working_day(self.day, self.doctor))

    def test_two_entries_on_one_date_both_contribute_their_slots(self):
        # A morning and an evening clinic on the same day are two entries, not
        # one replacing the other — see CalendarEventForm's own note that one
        # event is one continuous stretch of time.
        DoctorSchedule.objects.create(
            doctor=self.doctor, date=self.day,
            start_time=time(10, 0), end_time=time(11, 0), slot_minutes=30,
        )
        DoctorSchedule.objects.create(
            doctor=self.doctor, date=self.day,
            start_time=time(17, 0), end_time=time(18, 0), slot_minutes=30,
        )
        slots = scheduling.day_slots(self.day, self.doctor)
        self.assertEqual(len(slots), 4)
        hours = {timezone.localtime(start).hour for start, _end in slots}
        self.assertEqual(hours, {10, 17})


class TestAmendingABooking(TestCase):
    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.doctor = make_doctor()
        give_wide_open_hours(self.doctor)
        self.visit = make_visit(make_patient(), self.doctor, start=_tomorrow_at(11))

    def test_the_edit_screen_opens(self):
        response = self.client.get(reverse("reception_edit_booking", args=[self.visit.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.visit.patient.full_name)

    def test_a_booking_can_be_moved_to_another_slot(self):
        new_slot = _tomorrow_at(12)
        response = self.client.post(
            reverse("reception_edit_booking", args=[self.visit.pk]),
            {"action": "reschedule", "day": new_slot.strftime("%Y-%m-%d"),
             "slot": new_slot.isoformat(), "note": "Patient asked"},
        )
        self.assertEqual(response.status_code, 302)
        self.visit.refresh_from_db()
        self.assertEqual(timezone.localtime(self.visit.scheduled_start).hour, 12)

    def test_rescheduling_keeps_the_same_visit_rather_than_making_a_new_one(self):
        new_slot = _tomorrow_at(12)
        self.client.post(
            reverse("reception_edit_booking", args=[self.visit.pk]),
            {"action": "reschedule", "day": new_slot.strftime("%Y-%m-%d"),
             "slot": new_slot.isoformat()},
        )
        self.assertEqual(Visit.objects.filter(patient=self.visit.patient).count(), 1)

    def test_the_move_is_recorded_against_the_visit(self):
        new_slot = _tomorrow_at(12)
        self.client.post(
            reverse("reception_edit_booking", args=[self.visit.pk]),
            {"action": "reschedule", "day": new_slot.strftime("%Y-%m-%d"),
             "slot": new_slot.isoformat()},
        )
        self.assertIn("Rescheduled", self.visit.status_events.latest("created_at").note)

    def test_a_booking_cannot_be_moved_into_the_past(self):
        past = timezone.now() - timedelta(days=1)
        self.client.post(
            reverse("reception_edit_booking", args=[self.visit.pk]),
            {"action": "reschedule", "day": past.strftime("%Y-%m-%d"),
             "slot": past.isoformat()},
        )
        self.visit.refresh_from_db()
        self.assertEqual(timezone.localtime(self.visit.scheduled_start).hour, 11)

    def test_an_accidental_booking_can_be_cancelled(self):
        self.client.post(
            reverse("reception_edit_booking", args=[self.visit.pk]),
            {"action": "cancel", "reason": "Booked by mistake"},
        )
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.CANCELLED)

    def test_a_patient_who_never_came_can_be_marked_a_no_show(self):
        self.visit.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
        self.client.post(
            reverse("reception_edit_booking", args=[self.visit.pk]),
            {"action": "no_show"},
        )
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.NO_SHOW)

    def test_a_settled_booking_cannot_be_edited(self):
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.IN_CABIN,
                       VisitStatus.CONSULTED, VisitStatus.BILLED):
            self.visit.transition_to(status, by_user=self.receptionist)
        response = self.client.get(
            reverse("reception_edit_booking", args=[self.visit.pk]), follow=True
        )
        self.assertContains(response, "cannot be changed")


class TestBookingTabsAndExport(TestCase):
    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.doctor = make_doctor()
        self.patient = make_patient()
        self.visit = make_visit(self.patient, self.doctor, start=_tomorrow_at(11))

    def test_both_tabs_render(self):
        for tab in ("upcoming", "completed"):
            response = self.client.get(reverse("reception_bookings"), {"tab": tab})
            self.assertEqual(response.status_code, 200, msg=tab)
            self.assertEqual(response.context["active_tab"], tab)

    def test_an_unknown_tab_falls_back_to_upcoming(self):
        response = self.client.get(reverse("reception_bookings"), {"tab": "nonsense"})
        self.assertEqual(response.context["active_tab"], "upcoming")

    def test_an_unconfirmed_booking_is_an_upcoming_appointment(self):
        # It used to sit in a call list of its own. Nobody rings it now, so it
        # is simply a booking that has not happened yet.
        response = self.client.get(reverse("reception_bookings"), {"tab": "upcoming"})
        self.assertContains(response, self.patient.patient_id)

    def test_the_export_is_a_csv_download(self):
        response = self.client.get(reverse("reception_export_bookings"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])

    def test_the_export_carries_the_payment_columns(self):
        response = self.client.get(reverse("reception_export_bookings"))
        header = response.content.decode().splitlines()[0]
        for column in ("Doctor", "Total", "Paid", "Balance", "Diagnoses"):
            self.assertIn(column, header)

    def test_filtering_by_doctor_narrows_the_history(self):
        response = self.client.get(
            reverse("reception_bookings"), {"tab": "past", "doctor": self.doctor.pk}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["filters"]["doctor"], str(self.doctor.pk))


class TestTheHistoryWithRealMoneyOnIt(TestCase):
    """
    Every earlier test in this file used visits with no charge attached, so the
    money columns were never evaluated at all — which is exactly how a wrong
    attribute name reached the running clinic. These build a settled visit.
    """

    def setUp(self):
        from billing.models import Charge, Payment

        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.doctor = make_doctor()
        self.patient = make_patient()

        self.visit = make_visit(self.patient, self.doctor,
                                start=timezone.now() - timedelta(days=1))
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED,
                       VisitStatus.IN_CABIN, VisitStatus.CONSULTED):
            self.visit.transition_to(status, by_user=self.receptionist)

        self.charge = Charge.objects.create(
            visit=self.visit, patient=self.patient,
            consultation_fee=Decimal("800.00"), procedure_fee=Decimal("200.00"),
            discount=Decimal("100.00"), set_by=self.doctor,
        )
        Payment.objects.create(
            charge=self.charge, amount=Decimal("900.00"),
            received_by=self.receptionist,
        )
        self.visit.transition_to(VisitStatus.BILLED, by_user=self.receptionist)

    def test_the_completed_tab_renders_the_money_columns(self):
        response = self.client.get(reverse("reception_bookings"), {"tab": "completed"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "900")   # 800 + 200 - 100, and paid

    def test_the_total_collected_adds_up_what_was_actually_paid(self):
        response = self.client.get(reverse("reception_bookings"), {"tab": "completed"})
        self.assertEqual(response.context["total_collected"], Decimal("900.00"))

    def test_a_part_paid_visit_is_not_a_completed_appointment(self):
        # Completed means the money is in. A visit with a balance still on it is
        # work somebody has to finish, and listing it as completed is how that
        # balance stops being chased.
        from billing.models import Payment

        self.charge.payments.all().delete()
        Payment.objects.create(
            charge=self.charge, amount=Decimal("400.00"), received_by=self.receptionist,
        )
        response = self.client.get(reverse("reception_bookings"), {"tab": "completed"})
        self.assertNotContains(response, self.patient.patient_id)

    def test_the_csv_carries_the_figures_not_just_the_headings(self):
        response = self.client.get(reverse("reception_export_bookings"))
        rows = response.content.decode().splitlines()
        self.assertEqual(len(rows), 2, msg="expected a header and one visit")
        self.assertIn("900", rows[1])

    def test_the_csv_names_the_doctor_and_the_patient(self):
        response = self.client.get(reverse("reception_export_bookings"))
        body = response.content.decode()
        self.assertIn(self.doctor.display_name, body)
        self.assertIn(self.patient.patient_id, body)


class TestCalendarAccess(TestCase):
    """
    KAN-50 replaced the availability screen with the calendar, and the two do
    not have the same access rule. The old screen was reception-only; a
    doctor may open the calendar, scoped to themselves, and — since the
    add/edit parity work — write their own working hours to it too. What a
    doctor still may not do is anything reception-only: add a cabin, or add
    a clinic holiday.
    """

    def test_a_receptionist_may_open_it(self):
        self.client.force_login(make_receptionist())
        self.assertEqual(
            self.client.get(reverse("reception_calendar")).status_code, 200
        )

    def test_a_doctor_may_read_it(self):
        self.client.force_login(make_doctor())
        self.assertEqual(
            self.client.get(reverse("reception_calendar")).status_code, 200
        )

    def test_a_doctor_may_write_their_own_hours_to_it(self):
        doctor = make_doctor()
        self.client.force_login(doctor)
        response = self.client.post(reverse("reception_add_calendar_event"), {
            "event_type": "hours", "date": timezone.localdate().isoformat(),
            "start_time": "10:00", "end_time": "12:00",
        })
        self.assertRedirects(response, reverse("reception_calendar"))

    def test_a_doctor_may_not_add_a_cabin(self):
        self.client.force_login(make_doctor())
        response = self.client.post(reverse("reception_add_cabin"), {"name": "Cabin 9"})
        self.assertEqual(response.status_code, 403)


class TestTheClinicIsOpenEveryDay(TestCase):
    """
    There is no weekend rule. Closure is always something a person recorded —
    a clinic holiday, or the absence of any schedule entry on that date.
    """

    def setUp(self):
        self.doctor = make_doctor()
        give_wide_open_hours(self.doctor)
        self.client.force_login(make_receptionist())
        self.sunday = timezone.localdate() + timedelta(days=1)
        while self.sunday.weekday() != 6:
            self.sunday += timedelta(days=1)

    def test_a_sunday_is_a_working_day(self):
        self.assertTrue(scheduling.is_working_day(self.sunday))
        self.assertTrue(scheduling.is_working_day(self.sunday, self.doctor))

    def test_slots_are_offered_on_a_sunday(self):
        self.assertTrue(scheduling.available_slots(self.doctor, self.sunday))

    def test_every_day_of_the_week_is_bookable_when_scheduled(self):
        day = timezone.localdate() + timedelta(days=1)
        for _ in range(7):
            self.assertTrue(
                scheduling.available_slots(self.doctor, day),
                msg=f"no slots on {day:%A}",
            )
            day += timedelta(days=1)

    def test_a_holiday_is_the_only_blanket_closure(self):
        ClinicHoliday.objects.create(date=self.sunday, name="Diwali")
        self.assertFalse(scheduling.is_working_day(self.sunday, self.doctor))
        self.assertEqual(scheduling.available_slots(self.doctor, self.sunday), [])

    def test_a_sunday_booking_is_accepted(self):
        patient = make_patient()
        slot = timezone.make_aware(
            timezone.datetime.combine(self.sunday, time(11, 0)),
            timezone.get_current_timezone(),
        )
        response = self.client.post(reverse("reception_new_booking"), {
            "patient": patient.pk, "doctor": self.doctor.pk,
            "day": self.sunday.strftime("%Y-%m-%d"), "slot": slot.isoformat(),
            "reason": "Sunday clinic",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Visit.objects.filter(patient=patient).exists())

    def test_the_slot_picker_names_the_holiday_when_there_is_one(self):
        ClinicHoliday.objects.create(date=self.sunday, name="Diwali")
        response = self.client.get(reverse("reception_slots"), {
            "doctor": self.doctor.pk, "day": self.sunday.strftime("%Y-%m-%d"),
        })
        self.assertContains(response, "Diwali")
