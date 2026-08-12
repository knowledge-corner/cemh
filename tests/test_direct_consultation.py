"""
A doctor running a whole consultation themselves — no reception, no prior
appointment.

"Start consultation" lives on the read-only chart (reached by "Open", from
search or from the queue, identically) and unlocks it exactly as "Send in"
does: reusing a waiting visit already booked with this doctor if there is
one, or creating a fresh ad-hoc visit — marked direct — if there is none at
all. "End consultation" is the direct-only counterpart to "Complete
consultation": it takes the fee, collects the payment and issues the receipt
in one step, since reception never saw this visit and has no billing worklist
for it to land on.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from appointments.models import InvalidTransition, Visit, VisitStatus
from billing.models import Charge, Payment, Receipt

from .factories import (
    make_doctor, make_patient, make_receptionist, make_visit, today_at,
)


class DirectConsultationTestCase(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.client.force_login(self.doctor)
        self.patient = make_patient()

    def start_url(self):
        return reverse("doctor_start_consultation", args=[self.patient.patient_id])

    def end_url(self):
        return reverse("doctor_end_consultation", args=[self.patient.patient_id])

    def dashboard_url(self):
        return reverse("doctor_patient_dashboard", args=[self.patient.patient_id])


class TestStartingWithNoVisitAtAll(DirectConsultationTestCase):
    def test_it_creates_a_visit_straight_into_the_cabin(self):
        self.client.post(self.start_url())
        visit = Visit.objects.get(patient=self.patient)
        self.assertEqual(visit.status, VisitStatus.IN_CABIN)
        self.assertTrue(visit.is_direct)
        self.assertEqual(visit.doctor, self.doctor)

    def test_it_lands_on_the_now_editable_dashboard(self):
        self.client.post(self.start_url())
        response = self.client.get(self.dashboard_url())
        self.assertTrue(response.context["is_editable"])
        self.assertNotContains(response, "Read-only")

    def test_it_is_refused_while_another_patient_is_in_the_cabin(self):
        occupied_patient = make_patient(phone="9820055501")
        make_visit(
            occupied_patient, self.doctor, start=today_at(9),
            status=VisitStatus.IN_CABIN,
        )
        self.client.post(self.start_url())
        self.assertFalse(Visit.objects.filter(patient=self.patient).exists())

    def test_a_get_request_does_not_start_anything(self):
        self.client.get(self.start_url())
        self.assertFalse(Visit.objects.filter(patient=self.patient).exists())


class TestStartingWithAWaitingVisit(DirectConsultationTestCase):
    def test_it_reuses_the_visit_exactly_like_send_in(self):
        visit = make_visit(
            self.patient, self.doctor, start=today_at(9),
            status=VisitStatus.ARRIVED,
        )
        self.client.post(self.start_url())
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.IN_CABIN)
        # Reused, not replaced — reception's own record of this booking is
        # untouched, so it is not marked direct.
        self.assertFalse(visit.is_direct)
        self.assertEqual(Visit.objects.filter(patient=self.patient).count(), 1)

    def test_a_walk_ins_own_tag_survives_being_started_directly(self):
        visit = make_visit(
            self.patient, self.doctor, start=today_at(9),
            status=VisitStatus.ARRIVED, is_walk_in=True,
        )
        self.client.post(self.start_url())
        visit.refresh_from_db()
        self.assertTrue(visit.is_walk_in)
        self.assertFalse(visit.is_direct)

    def test_a_visit_waiting_for_another_doctor_is_left_alone(self):
        other_doctor = make_doctor(username="drother", email="other@example.in")
        theirs = make_visit(
            self.patient, other_doctor, start=today_at(9),
            status=VisitStatus.ARRIVED,
        )
        self.client.post(self.start_url())
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, VisitStatus.ARRIVED)
        # A brand new direct visit was created for this doctor instead.
        mine = Visit.objects.get(doctor=self.doctor, patient=self.patient)
        self.assertEqual(mine.status, VisitStatus.IN_CABIN)
        self.assertTrue(mine.is_direct)


class TestStartingWithAConfirmedNotYetArrivedVisit(DirectConsultationTestCase):
    """
    Regression: the chart used to offer "Start consultation" for a patient
    already CONFIRMED for today — a booking that has not been sent in yet,
    the way an ARRIVED one has. Clicking it fell straight to the "no visit at
    all" branch and created a second, redundant direct visit underneath the
    real booking, instead of leaving it for "Send in" to pick up once the
    patient actually arrives.
    """

    def _confirmed_today(self, doctor=None):
        return make_visit(
            self.patient, doctor or self.doctor, start=today_at(9),
            status=VisitStatus.CONFIRMED,
        )

    def test_the_button_is_not_offered_on_the_chart(self):
        self._confirmed_today()
        response = self.client.get(self.dashboard_url())
        self.assertNotContains(response, "Start consultation")

    def test_posting_it_anyway_is_refused_and_creates_nothing(self):
        visit = self._confirmed_today()
        self.client.post(self.start_url())
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.CONFIRMED)
        self.assertEqual(Visit.objects.filter(patient=self.patient).count(), 1)

    def test_the_read_only_notice_points_at_send_in_only(self):
        self._confirmed_today()
        response = self.client.get(self.dashboard_url())
        self.assertContains(response, "Send in")
        self.assertNotContains(response, "Start consultation")

    def test_a_confirmed_visit_with_another_doctor_does_not_block_this_one(self):
        other_doctor = make_doctor(username="drother2", email="other2@example.in")
        theirs = self._confirmed_today(doctor=other_doctor)
        response = self.client.get(self.dashboard_url())
        self.assertContains(response, "Start consultation")

        self.client.post(self.start_url())
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, VisitStatus.CONFIRMED)
        mine = Visit.objects.get(doctor=self.doctor, patient=self.patient)
        self.assertEqual(mine.status, VisitStatus.IN_CABIN)
        self.assertTrue(mine.is_direct)


class TestEndingADirectConsultation(DirectConsultationTestCase):
    def _start(self):
        self.client.post(self.start_url())
        return Visit.objects.get(patient=self.patient, is_direct=True)

    def test_it_is_not_offered_for_an_ordinary_consultation(self):
        # An appointment sent in normally still ends through Complete
        # consultation, which routes to reception's billing list as before.
        make_visit(
            self.patient, self.doctor, start=today_at(9),
            status=VisitStatus.IN_CABIN,
        )
        response = self.client.get(self.dashboard_url())
        self.assertContains(response, "Complete consultation")
        self.assertNotContains(response, "End consultation")
        self.assertEqual(self.client.get(self.end_url()).status_code, 404)

    def test_submitting_the_fee_bills_and_completes_the_visit_in_one_step(self):
        visit = self._start()
        response = self.client.post(self.end_url(), {
            "consultation_fee": "600", "procedure_fee": "0",
            "discount": "0", "notes": "",
        })
        self.assertEqual(response.status_code, 200)
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.COMPLETED)

        charge = Charge.objects.get(visit=visit)
        self.assertEqual(charge.total, Decimal("600"))

        payment = Payment.objects.get(charge=charge)
        self.assertEqual(payment.amount, Decimal("600"))
        self.assertEqual(payment.received_by, self.doctor)
        self.assertTrue(Receipt.objects.filter(payment=payment).exists())

    def test_a_free_consultation_completes_with_no_payment_recorded(self):
        visit = self._start()
        self.client.post(self.end_url(), {
            "consultation_fee": "0", "procedure_fee": "0",
            "discount": "0", "notes": "Follow-up review, no charge",
        })
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.COMPLETED)
        self.assertFalse(Payment.objects.filter(charge__visit=visit).exists())

    def test_another_doctor_cannot_end_it(self):
        self._start()
        other_doctor = make_doctor(username="drother", email="other@example.in")
        self.client.force_login(other_doctor)
        response = self.client.post(self.end_url(), {
            "consultation_fee": "600", "procedure_fee": "0",
            "discount": "0", "notes": "",
        })
        self.assertEqual(response.status_code, 403)

    def test_a_receptionist_cannot_reach_either_endpoint(self):
        self.client.force_login(self.receptionist)
        self.assertEqual(self.client.post(self.start_url()).status_code, 403)
        self.assertEqual(self.client.get(self.end_url()).status_code, 403)


class TestTheUnclosedConsultationDoesNotBlockACabinForever(DirectConsultationTestCase):
    def test_a_second_direct_start_is_refused_while_the_first_is_open(self):
        self.client.post(self.start_url())
        another_patient = make_patient(phone="9820055502")
        response = self.client.post(
            reverse("doctor_start_consultation", args=[another_patient.patient_id])
        )
        self.assertRedirects(response, reverse(
            "doctor_patient_dashboard", args=[another_patient.patient_id]
        ))
        self.assertFalse(Visit.objects.filter(patient=another_patient).exists())

    def test_send_in_is_also_refused_while_a_direct_consultation_is_open(self):
        self.client.post(self.start_url())
        waiting_patient = make_patient(phone="9820055503")
        waiting = make_visit(
            waiting_patient, self.doctor, start=today_at(9),
            status=VisitStatus.ARRIVED,
        )
        with self.assertRaises(InvalidTransition):
            waiting.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)


class TestTheDirectTagInCompletedBookings(TestCase):
    """
    All Bookings → Completed Bookings: a direct consultation shows up there
    tagged the same way an appointment or a walk-in does, and the
    Appointment type filter can single any of the three out.
    """

    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)

        self.appointment = self._completed_visit(is_walk_in=False, is_direct=False)
        self.walk_in = self._completed_visit(is_walk_in=True, is_direct=False, phone="9820055601")
        self.direct = self._completed_visit(is_walk_in=False, is_direct=True, phone="9820055602")

    def _completed_visit(self, *, is_walk_in, is_direct, phone="9820055600"):
        patient = make_patient(phone=phone)
        visit = make_visit(
            patient, self.doctor, start=today_at(9),
            status=VisitStatus.COMPLETED, is_walk_in=is_walk_in, is_direct=is_direct,
        )
        return visit

    def _completed(self, **params):
        return self.client.get(reverse("reception_bookings"), {"tab": "completed", **params})

    def test_each_row_is_tagged_by_how_it_came_to_exist(self):
        response = self._completed()
        self.assertContains(response, "Appointment")
        self.assertContains(response, "Walk-in")
        self.assertContains(response, "Direct")

    def test_the_appointment_type_filter_narrows_to_direct_only(self):
        response = self._completed(visit_type="DIRECT")
        visits = list(response.context["past"])
        self.assertEqual(visits, [self.direct])

    def test_the_appointment_type_filter_narrows_to_walk_in_only(self):
        response = self._completed(visit_type="WALK_IN")
        visits = list(response.context["past"])
        self.assertEqual(visits, [self.walk_in])

    def test_the_appointment_type_filter_narrows_to_appointment_only(self):
        response = self._completed(visit_type="APPOINTMENT")
        visits = list(response.context["past"])
        self.assertEqual(visits, [self.appointment])


class TestTheUnclosedDirectConsultationBanner(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.patient = make_patient()

    def test_it_shows_on_receptions_calendar(self):
        make_visit(
            self.patient, self.doctor, start=today_at(9),
            status=VisitStatus.IN_CABIN, is_direct=True,
        )
        self.client.force_login(self.receptionist)
        response = self.client.get(reverse("reception_calendar"))
        self.assertContains(response, "Consultation still open")
        self.assertContains(response, self.patient.full_name)

    def test_it_shows_on_the_doctors_own_calendar_too(self):
        make_visit(
            self.patient, self.doctor, start=today_at(9),
            status=VisitStatus.IN_CABIN, is_direct=True,
        )
        self.client.force_login(self.doctor)
        response = self.client.get(reverse("reception_calendar"))
        self.assertContains(response, "Consultation still open")

    def test_it_is_absent_once_the_consultation_is_closed(self):
        make_visit(
            self.patient, self.doctor, start=today_at(9),
            status=VisitStatus.COMPLETED, is_direct=True,
        )
        self.client.force_login(self.receptionist)
        response = self.client.get(reverse("reception_calendar"))
        self.assertNotContains(response, "Consultation still open")

    def test_an_ordinary_in_cabin_visit_does_not_trigger_it(self):
        # Only the direct/unclosed case does — a normal consultation in
        # progress is business as usual, not something to warn about.
        make_visit(
            self.patient, self.doctor, start=today_at(9),
            status=VisitStatus.IN_CABIN,
        )
        self.client.force_login(self.receptionist)
        response = self.client.get(reverse("reception_calendar"))
        self.assertNotContains(response, "Consultation still open")
