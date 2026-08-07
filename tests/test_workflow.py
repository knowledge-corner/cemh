"""
The clinic day, end to end.

Booking → reception confirms → arrived → cabin → doctor finishes with a fee and
a prescription → reception takes payment and issues a receipt → checkout.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments import scheduling
from appointments.models import Visit, VisitStatus
from audit.models import AccessLog, AuditAction
from billing.models import Charge, Payment, Receipt
from patients.models import Patient
from pharmacy.models import Prescription, PrescriptionItem

from .factories import (
    later_today, make_adult_patient, make_doctor, make_patient, make_receptionist,
    make_user, make_visit, today_at,
)

PASSWORD = "testpass12345"


def next_working_day(start=None):
    day = (start or timezone.localdate()) + timedelta(days=1)
    while not scheduling.is_working_day(day):
        day += timedelta(days=1)
    return day


class TestScheduling(TestCase):
    def setUp(self):
        self.doctor = make_doctor()

    def test_slots_are_offered_on_a_working_day(self):
        self.assertTrue(scheduling.available_slots(self.doctor, next_working_day()))

    def test_no_slots_on_a_day_somebody_marked_closed(self):
        # The clinic is open every day now, including Sundays. A closed day has
        # to be entered as a holiday — this test used to walk forward until it
        # found a weekend, which no longer terminates.
        from appointments.models import ClinicHoliday

        day = timezone.localdate() + timedelta(days=1)
        ClinicHoliday.objects.create(date=day, name="Diwali")

        self.assertFalse(scheduling.is_working_day(day))
        self.assertEqual(scheduling.available_slots(self.doctor, day), [])

    def test_a_sunday_is_bookable_unless_marked_otherwise(self):
        day = timezone.localdate() + timedelta(days=1)
        while day.weekday() != 6:            # 6 = Sunday
            day += timedelta(days=1)
        self.assertTrue(scheduling.is_working_day(day))
        self.assertTrue(scheduling.available_slots(self.doctor, day))

    def test_a_booked_slot_is_no_longer_offered(self):
        day = next_working_day()
        slots = scheduling.available_slots(self.doctor, day)
        taken = slots[0][0]

        make_visit(make_patient(), self.doctor, start=taken)

        remaining = {s for s, _ in scheduling.available_slots(self.doctor, day)}
        self.assertNotIn(taken, remaining)

    def test_a_cancelled_booking_frees_its_slot_again(self):
        day = next_working_day()
        taken = scheduling.available_slots(self.doctor, day)[0][0]
        visit = make_visit(make_patient(), self.doctor, start=taken)
        visit.transition_to(VisitStatus.CANCELLED, by_user=self.doctor)

        self.assertIn(taken, {s for s, _ in scheduling.available_slots(self.doctor, day)})

    def test_past_slots_are_hidden_unless_asked_for(self):
        today = timezone.localdate()
        if not scheduling.is_working_day(today):
            self.skipTest("Today is not a consulting day")
        offered = scheduling.available_slots(self.doctor, today)
        everything = scheduling.available_slots(self.doctor, today, include_past=True)
        self.assertLessEqual(len(offered), len(everything))


class TestReceptionBooking(TestCase):
    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.patient = make_patient()
        self.client.force_login(self.receptionist)
        self.day = next_working_day()
        self.slot = scheduling.available_slots(self.doctor, self.day)[0][0]

    def booking_payload(self, **overrides):
        payload = {
            "patient": self.patient.pk,
            "doctor": self.doctor.pk,
            "day": self.day.isoformat(),
            "slot": self.slot.isoformat(),
            "reason": "Thyroid review",
        }
        payload.update(overrides)
        return payload

    def test_reception_booking_starts_unconfirmed(self):
        response = self.client.post(reverse("reception_new_booking"), self.booking_payload())
        self.assertEqual(response.status_code, 302)

        visit = Visit.objects.get()
        # The receptionist still has to ring the patient on the day, so the
        # booking is not confirmed until she has.
        self.assertEqual(visit.status, VisitStatus.BOOKED)
        self.assertEqual(visit.booked_by, self.receptionist)

    def test_booking_a_taken_slot_is_refused_with_a_readable_error(self):
        make_visit(make_adult_patient(), self.doctor, start=self.slot)

        response = self.client.post(reverse("reception_new_booking"), self.booking_payload())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no longer free")
        self.assertEqual(Visit.objects.count(), 1)

    def test_booking_without_a_patient_is_refused(self):
        response = self.client.post(
            reverse("reception_new_booking"), self.booking_payload(patient="")
        )
        self.assertContains(response, "Choose a patient")
        self.assertEqual(Visit.objects.count(), 0)

    def test_slot_must_fall_on_the_chosen_date(self):
        response = self.client.post(reverse("reception_new_booking"), self.booking_payload(
            slot=(self.slot + timedelta(days=1)).isoformat()
        ))
        self.assertContains(response, "not on the selected date")

    def test_patient_search_finds_by_uhid_and_name(self):
        for query in (self.patient.patient_id, self.patient.first_name, self.patient.phone):
            response = self.client.get(reverse("reception_patient_lookup"), {"q": query})
            self.assertContains(response, self.patient.patient_id, msg_prefix=f"query={query}")

    def test_registering_a_patient_issues_a_uhid(self):
        self.client.post(reverse("reception_register_patient"), {
            "first_name": "Neha", "last_name": "Joshi",
            "date_of_birth": "1990-05-04", "sex": "F", "blood_group": "",
            "phone": "9820011111", "alternate_phone": "", "email": "",
            "guardian_name": "", "guardian_relation": "", "guardian_phone": "",
            "address": "", "city": "Mumbai", "pincode": "", "referred_by": "",
        })
        created = Patient.objects.get(first_name="Neha")
        self.assertRegex(created.patient_id, r"^CEMH-\d{2}-\d{5}$")


class TestReceptionQueue(TestCase):
    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.patient = make_patient()
        self.client.force_login(self.receptionist)
        self.visit = make_visit(self.patient, self.doctor, start=later_today())

    def move(self, to_status):
        """Move the visit the way the queue board does — over HTMX."""
        return self.client.post(
            reverse("reception_move_visit", args=[self.visit.pk, to_status]),
            headers={"HX-Request": "true"},
        )

    def test_queue_lists_todays_patient(self):
        response = self.client.get(reverse("reception_home"))
        self.assertContains(response, self.patient.patient_id)

    def test_walking_a_patient_through_the_day(self):
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.IN_CABIN):
            self.assertEqual(self.move(status).status_code, 200)
            self.visit.refresh_from_db()
            self.assertEqual(self.visit.status, status)

        self.assertIsNotNone(self.visit.arrived_at)
        self.assertIsNotNone(self.visit.entered_cabin_at)

    def test_an_out_of_order_move_is_refused(self):
        # Straight to the cabin without ever arriving.
        self.move(VisitStatus.IN_CABIN)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.BOOKED)

    def test_each_move_is_recorded_against_the_patient(self):
        self.move(VisitStatus.CONFIRMED)
        self.assertTrue(
            AccessLog.objects.filter(
                action=AuditAction.UPDATE, patient_id_ref=self.patient.patient_id
            ).exists()
        )

    def test_a_doctor_may_read_the_queue(self):
        # KAN-2 FR-7: a doctor needs to see who is in the waiting room without
        # having to ask reception.
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.get(reverse("reception_home")).status_code, 200)

    def test_a_doctor_still_cannot_work_the_queue(self):
        # Reading it is not working it. The stage buttons are hidden from the
        # doctor, and the view underneath refuses the move regardless.
        self.client.force_login(self.doctor)
        response = self.client.post(
            reverse("reception_move_visit", args=[self.visit.pk, VisitStatus.CONFIRMED]),
            headers={"HX-Request": "true"},
        )
        self.assertEqual(response.status_code, 403)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.BOOKED)


class TestConsultationHandover(TestCase):
    """The doctor's end-of-consultation action, and what reception receives."""

    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.patient = make_patient()
        self.visit = make_visit(self.patient, self.doctor, start=timezone.now())
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.IN_CABIN):
            self.visit.transition_to(status, by_user=self.receptionist)

    def complete(self, **overrides):
        payload = {"consultation_fee": "800", "procedure_fee": "0", "discount": "0", "notes": ""}
        payload.update(overrides)
        self.client.force_login(self.doctor)
        return self.client.post(
            reverse("doctor_complete_consultation", args=[self.patient.patient_id]), payload
        )

    def test_completing_sets_the_fee_and_moves_the_visit(self):
        response = self.complete()
        self.assertEqual(response.status_code, 200)

        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.CONSULTED)

        charge = Charge.objects.get(visit=self.visit)
        self.assertEqual(charge.total, Decimal("800"))
        self.assertEqual(charge.set_by, self.doctor)

    def test_completing_does_not_create_or_send_a_prescription(self):
        # Prescriptions are the doctor's own document now, written and printed
        # from their own tab — completing a consultation is fee-only.
        self.complete()
        self.assertFalse(Prescription.objects.exists())

    def test_a_discount_reduces_what_reception_collects(self):
        self.complete(consultation_fee="800", discount="200")
        self.assertEqual(Charge.objects.get().total, Decimal("600"))

    def test_completing_is_refused_when_the_patient_is_not_in_the_cabin(self):
        self.visit.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        self.assertEqual(self.complete().status_code, 404)

    def test_a_receptionist_cannot_set_the_fee(self):
        self.client.force_login(self.receptionist)
        response = self.client.post(
            reverse("doctor_complete_consultation", args=[self.patient.patient_id]),
            {"consultation_fee": "50"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Charge.objects.exists())


class TestBilling(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.patient = make_patient()
        self.visit = make_visit(self.patient, self.doctor, start=timezone.now())
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED,
                       VisitStatus.IN_CABIN, VisitStatus.CONSULTED):
            self.visit.transition_to(status, by_user=self.doctor)

        self.charge = Charge.objects.create(
            visit=self.visit, patient=self.patient,
            consultation_fee=Decimal("800.00"), set_by=self.doctor,
        )
        self.prescription = Prescription.objects.create(
            visit=self.visit, patient=self.patient, doctor=self.doctor
        )
        PrescriptionItem.objects.create(
            prescription=self.prescription, drug_name="Levothyroxine", strength="50 mcg"
        )
        self.prescription.generate()
        self.client.force_login(self.receptionist)

    def pay(self, amount):
        # KAN-36 left the Generate receipt pop-up as the only way money is
        # taken. Same charge, same lock, same receipt — only the screen moved.
        return self.client.post(
            reverse("reception_generate_receipt", args=[self.visit.pk]), {
                "amount": amount, "method": "CASH", "reference": "", "notes": "",
            })

    def test_the_receipt_dialog_shows_what_is_owed(self):
        response = self.client.get(
            reverse("reception_generate_receipt", args=[self.visit.pk])
        )
        self.assertContains(response, "800")
        self.assertContains(response, self.patient.patient_id)

    def test_payment_in_full_issues_a_receipt_and_settles_the_visit(self):
        self.pay("800.00")

        payment = Payment.objects.get()
        self.assertEqual(payment.received_by, self.receptionist)

        receipt = Receipt.objects.get()
        self.assertRegex(receipt.receipt_number, r"^R-\d{2}-\d{5}$")

        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.BILLED)

    def test_a_part_payment_leaves_the_visit_unsettled(self):
        self.pay("300.00")

        self.charge.refresh_from_db()
        self.assertEqual(self.charge.balance, Decimal("500.00"))

        self.visit.refresh_from_db()
        # Still on the billing list — there is money outstanding.
        self.assertEqual(self.visit.status, VisitStatus.CONSULTED)

    def test_receipt_numbers_do_not_collide(self):
        self.pay("300.00")
        self.pay("500.00")
        numbers = list(Receipt.objects.values_list("receipt_number", flat=True))
        self.assertEqual(len(numbers), len(set(numbers)))

    def test_checkout_completes_the_visit(self):
        self.pay("800.00")
        self.client.post(reverse("reception_complete_visit", args=[self.visit.pk]))
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.COMPLETED)

    def test_printing_a_prescription_records_it(self):
        response = self.client.get(
            reverse("print_prescription", args=[self.visit.pk]), {"mark": "1"}
        )
        self.assertContains(response, "Levothyroxine")
        self.prescription.refresh_from_db()
        self.assertIsNotNone(self.prescription.printed_at)
        self.assertTrue(AccessLog.objects.filter(action=AuditAction.PRINT).exists())

    def test_printing_a_receipt_shows_the_number(self):
        self.pay("800.00")
        receipt = Receipt.objects.get()
        response = self.client.get(reverse("print_receipt", args=[receipt.pk]))
        self.assertContains(response, receipt.receipt_number)

    def test_a_patient_cannot_reach_billing(self):
        self.client.force_login(make_user())
        self.assertEqual(
            self.client.get(
                reverse("reception_generate_receipt", args=[self.visit.pk])
            ).status_code, 403
        )


class TestNoPatientPortal(TestCase):
    """
    Patients never sign in — they telephone or send a WhatsApp message from the
    public page. These routes must be gone, not merely unlinked.
    """

    def test_portal_routes_no_longer_exist(self):
        for path in ("/my/", "/my/book/", "/my/book/slots/"):
            self.assertEqual(self.client.get(path).status_code, 404, msg=path)

    def test_a_patient_account_still_cannot_reach_a_chart(self):
        # Such an account can still exist; it must not see clinical records.
        self.client.force_login(make_user())
        patient = make_patient()
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[patient.patient_id])
        )
        self.assertEqual(response.status_code, 403)


class TestAuditLogCannotBeBulkErased(TestCase):
    def test_queryset_delete_is_refused(self):
        AccessLog.objects.create(action=AuditAction.VIEW, username="someone")
        with self.assertRaises(ValueError):
            AccessLog.objects.all().delete()

    def test_queryset_update_is_refused(self):
        AccessLog.objects.create(action=AuditAction.VIEW, username="someone")
        with self.assertRaises(ValueError):
            AccessLog.objects.all().update(description="tampered")


class TestChartShowsTheVisitInProgress(TestCase):
    """
    A follow-up already in the diary must not hide the consultation happening
    now — that would take the "Complete consultation" action off the screen.
    """

    def setUp(self):
        self.doctor = make_doctor()
        self.patient = make_patient()
        self.client.force_login(self.doctor)

        self.today_visit = make_visit(self.patient, self.doctor, start=timezone.now())
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.IN_CABIN):
            self.today_visit.transition_to(status, by_user=self.doctor)

        make_visit(self.patient, self.doctor, start=timezone.now() + timedelta(days=30))

    def test_complete_action_survives_a_future_booking(self):
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[self.patient.patient_id])
        )
        self.assertContains(response, "Complete consultation")

    def test_chart_reports_the_in_cabin_visit(self):
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[self.patient.patient_id])
        )
        self.assertEqual(response.context["active_visit"], self.today_visit)


class TestAppointmentsAreOneStage(TestCase):
    """
    The board used to separate patients still to ring from those already
    reached. The confirming call is not made any more, so the two columns held
    the same thing and one of them was always empty: they are one Appointments
    stage now, and both bookings belong to it.
    """

    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)

        self.to_ring = make_patient(phone="9820011111")
        self.reached = make_patient(phone="9820022222")

        make_visit(self.to_ring, self.doctor, start=today_at(10))
        confirmed = make_visit(self.reached, self.doctor, start=today_at(11))
        confirmed.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)

    def test_the_board_has_no_to_confirm_column(self):
        response = self.client.get(reverse("reception_home"))
        self.assertNotContains(response, "To confirm")
        self.assertContains(response, "Stage 1 · Appointments")

    def test_both_bookings_sit_in_the_appointments_stage(self):
        columns = {c["key"]: c["count"] for c in
                   self.client.get(reverse("reception_home")).context["columns"]}
        self.assertEqual(columns["appointments"], 2)

    def test_the_number_is_still_on_the_card(self):
        # Nobody rings to confirm any more, but reception still has to reach a
        # patient who has not turned up.
        response = self.client.get(reverse("reception_home"))
        self.assertContains(response, f'tel:{self.to_ring.contact_phone}')

    def test_an_unconfirmed_booking_can_be_marked_arrived_directly(self):
        # There is no confirming step in between any more. The patient is either
        # standing at the desk or they are not.
        visit = Visit.objects.get(patient=self.to_ring)
        self.client.post(reverse("reception_move_visit", args=[visit.pk, "ARRIVED"]))
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.ARRIVED)

    def test_confirming_still_works_underneath(self):
        # Kept rather than removed: visits already carry it, and backward
        # movement out of the waiting room lands on it.
        visit = Visit.objects.get(patient=self.to_ring)
        self.client.post(reverse("reception_move_visit", args=[visit.pk, "CONFIRMED"]))
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.CONFIRMED)
