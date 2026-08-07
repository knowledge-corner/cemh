"""
A doctor registering a patient who has never attended before.

Reception's own registration screen already exists and is unchanged (see
test_patient_registration.py); this is the doctor's own version — same form,
same duplicate check, but landing on the new chart instead of a booking
screen, since booking is not something a doctor's screen offers.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AccessLog, AuditAction
from patients.models import Patient, Sex

from .factories import make_doctor, make_receptionist


def _dob_for_age(years):
    today = timezone.localdate()
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


class DoctorRegisterTestCase(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.client.force_login(self.doctor)

    def _payload(self, **overrides):
        payload = {
            "first_name": "Aarav", "last_name": "Deshpande",
            "date_of_birth": _dob_for_age(30).isoformat(),
            "sex": Sex.MALE, "phone": "9820055555",
            "guardian_name": "", "guardian_relation": "",
        }
        payload.update(overrides)
        return payload

    def _register(self, **overrides):
        return self.client.post(
            reverse("doctor_register_patient"), self._payload(**overrides)
        )


class TestRegisteringFromTheDoctorPortal(DoctorRegisterTestCase):
    def test_the_page_opens(self):
        self.assertEqual(
            self.client.get(reverse("doctor_register_patient")).status_code, 200
        )

    def test_a_receptionist_cannot_use_the_doctors_registration_screen(self):
        self.client.force_login(self.receptionist)
        self.assertEqual(
            self.client.get(reverse("doctor_register_patient")).status_code, 403
        )

    def test_registering_creates_the_patient(self):
        self._register()
        patient = Patient.objects.get()
        self.assertEqual(patient.first_name, "Aarav")
        self.assertEqual(patient.sex, Sex.MALE)

    def test_it_lands_on_the_new_patients_own_chart(self):
        response = self._register()
        patient = Patient.objects.get()
        self.assertRedirects(
            response, reverse("doctor_patient_dashboard", args=[patient.patient_id]),
        )

    def test_it_does_not_redirect_to_a_booking_screen(self):
        # The whole point of this being a different screen from reception's.
        response = self._register()
        self.assertNotIn("booking", response.get("Location", ""))

    def test_the_registration_is_audited(self):
        self._register()
        entry = AccessLog.objects.filter(action=AuditAction.CREATE).get()
        self.assertEqual(entry.username, self.doctor.username)
        self.assertIn("doctor", entry.description.lower())

    def test_a_minor_needs_a_guardian(self):
        response = self._register(
            date_of_birth=_dob_for_age(10).isoformat(),
            guardian_name="", guardian_relation="",
        )
        self.assertEqual(Patient.objects.count(), 0)
        self.assertContains(response, "field__error")

    def test_a_minor_with_a_guardian_is_registered(self):
        self._register(
            date_of_birth=_dob_for_age(10).isoformat(),
            guardian_name="Rina Deshpande", guardian_relation="Mother",
        )
        self.assertEqual(Patient.objects.count(), 1)


class TestTheDoctorsDuplicateCheck(DoctorRegisterTestCase):
    def setUp(self):
        super().setUp()
        self.existing = Patient.objects.create(
            first_name="Meera", last_name="Kulkarni",
            date_of_birth=_dob_for_age(34), sex=Sex.FEMALE,
            phone="9820012345",
        )

    def test_a_likely_duplicate_is_warned_about_before_creating(self):
        response = self._register(
            first_name="Meera", last_name="Kulkarni",
            date_of_birth=_dob_for_age(34).isoformat(), sex=Sex.FEMALE,
            phone="9820012345",
        )
        self.assertEqual(Patient.objects.count(), 1)
        self.assertContains(response, "already registered")
        self.assertContains(response, self.existing.patient_id)

    def test_the_warning_offers_to_open_the_existing_chart_not_a_booking(self):
        response = self._register(
            first_name="Meera", last_name="Kulkarni",
            date_of_birth=_dob_for_age(34).isoformat(), sex=Sex.FEMALE,
            phone="9820012345",
        )
        self.assertContains(
            response,
            reverse("doctor_patient_dashboard", args=[self.existing.patient_id]),
        )

    def test_confirming_registers_the_second_person_anyway(self):
        self._register(
            first_name="Meera", last_name="Kulkarni",
            date_of_birth=_dob_for_age(34).isoformat(), sex=Sex.FEMALE,
            phone="9820012345", confirm="1",
        )
        self.assertEqual(Patient.objects.count(), 2)


class TestTheDoctorNavBar(DoctorRegisterTestCase):
    def test_todays_clinic_links_to_new_patient(self):
        response = self.client.get(reverse("doctor_home"))
        self.assertContains(response, reverse("doctor_register_patient"))

    def test_todays_clinic_links_to_the_calendar(self):
        response = self.client.get(reverse("doctor_home"))
        self.assertContains(response, reverse("reception_calendar"))

    def test_the_calendar_does_not_offer_a_doctor_receptions_bookings_screen(self):
        # Pre-existing bug this work also fixed: a doctor viewing the
        # calendar was shown a "Bookings" link only reception may open.
        response = self.client.get(reverse("reception_calendar"))
        self.assertNotContains(response, reverse("reception_bookings"))

    def test_the_calendar_links_back_to_todays_clinic_and_new_patient(self):
        response = self.client.get(reverse("reception_calendar"))
        self.assertContains(response, reverse("doctor_home"))
        self.assertContains(response, reverse("doctor_register_patient"))
