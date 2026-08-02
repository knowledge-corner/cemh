"""Model behaviour: patient IDs, ages, the visit lifecycle and the audit trail."""

from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from appointments.models import ALLOWED_TRANSITIONS, InvalidTransition, Visit, VisitStatus
from audit.models import AccessLog, AuditAction
from patients.models import Patient, allocate_patient_id

from .factories import (
    make_adult_patient, make_doctor, make_measurement, make_patient, make_visit,
)


class TestPatientIdentifier(TestCase):
    def test_patient_id_is_generated_on_save(self):
        patient = make_patient()
        self.assertTrue(patient.patient_id)
        self.assertRegex(patient.patient_id, r"^CEMH-\d{2}-\d{5}$")

    def test_patient_ids_are_unique_and_sequential(self):
        ids = [make_patient(phone="9820012345").patient_id for _ in range(5)]
        self.assertEqual(len(set(ids)), 5, "Patient IDs must never collide")

        serials = [int(pid.rsplit("-", 1)[1]) for pid in ids]
        self.assertEqual(serials, sorted(serials), "Serial part should increase")

    def test_allocate_patient_id_never_repeats(self):
        # The sequence is the whole reason two receptionists registering at the
        # same moment cannot produce a duplicate.
        allocated = {allocate_patient_id() for _ in range(50)}
        self.assertEqual(len(allocated), 50)

    def test_patient_id_is_not_overwritten_on_later_saves(self):
        patient = make_patient()
        original = patient.patient_id
        patient.first_name = "Renamed"
        patient.save()
        patient.refresh_from_db()
        self.assertEqual(patient.patient_id, original)


class TestPatientAge(TestCase):
    def test_age_years_accounts_for_birthday_not_yet_passed(self):
        today = timezone.localdate()
        born = today.replace(year=today.year - 10) + timedelta(days=1)
        patient = make_patient(date_of_birth=born)
        self.assertEqual(patient.age_years, 9)

    def test_infant_age_displays_in_months(self):
        patient = make_patient(date_of_birth=timezone.localdate() - timedelta(days=200))
        # Spelled out, and with no leading "0 yrs" — see Patient.age_display.
        self.assertEqual(patient.age_display, "6 months")

    def test_child_is_paediatric_and_adult_is_not(self):
        self.assertTrue(make_patient().is_paediatric)
        self.assertFalse(make_adult_patient().is_paediatric)

    def test_contact_phone_prefers_guardian_for_a_child(self):
        patient = make_patient(guardian_phone="9911223344")
        self.assertEqual(patient.contact_phone, "9911223344")

    def test_contact_phone_uses_patient_number_for_an_adult(self):
        patient = make_adult_patient(guardian_phone="9911223344")
        self.assertEqual(patient.contact_phone, patient.phone)


class TestVisitLifecycle(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.patient = make_patient()
        self.visit = make_visit(self.patient, self.doctor)

    def test_new_visit_starts_as_booked(self):
        self.assertEqual(self.visit.status, VisitStatus.BOOKED)

    def test_full_happy_path_is_permitted(self):
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.IN_CABIN,
                       VisitStatus.CONSULTED, VisitStatus.BILLED, VisitStatus.COMPLETED):
            self.visit.transition_to(status, by_user=self.doctor)
        self.assertEqual(self.visit.status, VisitStatus.COMPLETED)

    def test_skipping_a_step_is_rejected(self):
        # A receptionist must not be able to send a patient into the cabin
        # before marking them arrived.
        with self.assertRaises(InvalidTransition):
            self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)

    def test_completed_visit_is_terminal(self):
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.IN_CABIN,
                       VisitStatus.CONSULTED, VisitStatus.BILLED, VisitStatus.COMPLETED):
            self.visit.transition_to(status, by_user=self.doctor)
        with self.assertRaises(InvalidTransition):
            self.visit.transition_to(VisitStatus.ARRIVED, by_user=self.doctor)

    def test_cancelled_visit_cannot_be_revived(self):
        self.visit.transition_to(VisitStatus.CANCELLED, by_user=self.doctor)
        with self.assertRaises(InvalidTransition):
            self.visit.transition_to(VisitStatus.CONFIRMED, by_user=self.doctor)

    def test_every_status_has_a_transition_rule(self):
        for status in VisitStatus:
            self.assertIn(status, ALLOWED_TRANSITIONS)

    def test_transition_records_an_event_with_the_actor(self):
        self.visit.transition_to(VisitStatus.CONFIRMED, by_user=self.doctor)
        event = self.visit.status_events.get()
        self.assertEqual(event.from_status, VisitStatus.BOOKED)
        self.assertEqual(event.to_status, VisitStatus.CONFIRMED)
        self.assertEqual(event.changed_by, self.doctor)

    def test_arrival_and_cabin_timestamps_are_captured(self):
        self.visit.transition_to(VisitStatus.CONFIRMED, by_user=self.doctor)
        self.visit.transition_to(VisitStatus.ARRIVED, by_user=self.doctor)
        self.assertIsNotNone(self.visit.arrived_at)

        self.visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.assertIsNotNone(self.visit.entered_cabin_at)
        self.assertIsNotNone(self.visit.waiting_minutes)

    def test_transition_to_same_status_is_a_no_op(self):
        self.visit.transition_to(VisitStatus.BOOKED, by_user=self.doctor)
        self.assertEqual(self.visit.status_events.count(), 0)


class TestNoDoubleBooking(TestCase):
    """The exclusion constraint is the real guarantee, so test it at the database."""

    def setUp(self):
        self.doctor = make_doctor()
        self.start = timezone.now() + timedelta(days=1)

    def test_overlapping_visits_for_one_doctor_are_rejected(self):
        make_visit(make_patient(), self.doctor, start=self.start)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_visit(
                    make_patient(), self.doctor,
                    start=self.start + timedelta(minutes=10),
                )

    def test_back_to_back_visits_are_allowed(self):
        make_visit(make_patient(), self.doctor, start=self.start)
        make_visit(make_patient(), self.doctor, start=self.start + timedelta(minutes=20))
        self.assertEqual(Visit.objects.count(), 2)

    def test_cancelled_visit_frees_the_slot(self):
        first = make_visit(make_patient(), self.doctor, start=self.start)
        first.transition_to(VisitStatus.CANCELLED, by_user=self.doctor)
        make_visit(make_patient(), self.doctor, start=self.start)
        self.assertEqual(Visit.objects.count(), 2)

    def test_two_doctors_may_be_busy_at_the_same_time(self):
        other = make_doctor(username="dr2", email="dr2@example.in")
        make_visit(make_patient(), self.doctor, start=self.start)
        make_visit(make_patient(), other, start=self.start)
        self.assertEqual(Visit.objects.count(), 2)


class TestMeasurement(TestCase):
    def test_bmi_is_computed_from_height_and_weight(self):
        m = make_measurement(make_patient(), height_cm=Decimal("120"), weight_kg=Decimal("28.8"))
        self.assertEqual(m.bmi, Decimal("20.0"))

    def test_bmi_is_none_without_height(self):
        self.assertIsNone(make_measurement(make_patient(), height_cm=None).bmi)

    def test_mid_parental_height_adds_thirteen_for_a_boy(self):
        m = make_measurement(
            make_patient(sex="M"),
            mother_height_cm=Decimal("152"), father_height_cm=Decimal("165"),
        )
        self.assertEqual(m.mid_parental_height_cm, Decimal("165.0"))

    def test_mid_parental_height_subtracts_thirteen_for_a_girl(self):
        m = make_measurement(
            make_patient(sex="F"),
            mother_height_cm=Decimal("152"), father_height_cm=Decimal("165"),
        )
        self.assertEqual(m.mid_parental_height_cm, Decimal("152.0"))

    def test_age_is_taken_at_measurement_date_not_today(self):
        patient = make_patient()
        m = make_measurement(patient, measured_on=timezone.localdate() - timedelta(days=365))
        self.assertAlmostEqual(m.age_years, patient.age_years - 1, places=0)


class TestAuditLogIsAppendOnly(TestCase):
    def test_entries_cannot_be_modified(self):
        entry = AccessLog.objects.create(action=AuditAction.VIEW, username="someone")
        entry.description = "tampered"
        with self.assertRaises(ValueError):
            entry.save()

    def test_entries_cannot_be_deleted(self):
        entry = AccessLog.objects.create(action=AuditAction.VIEW, username="someone")
        with self.assertRaises(ValueError):
            entry.delete()
