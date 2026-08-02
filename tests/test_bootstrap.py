"""
The automatic start-up steps.

These run on every container start, because nobody is going to open a terminal
to run them. That makes "safe to run repeatedly" the whole requirement: the
second start must not undo the first day's work.

The dangerous one is the demo data. ``seed_demo`` deletes what it finds before
it writes — right for a developer resetting a laptop, catastrophic on a Tuesday
morning at the clinic.
"""

import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from patients.models import Patient

from .factories import make_patient

User = get_user_model()


class TestTheAdministratorAccount(TestCase):
    def test_an_administrator_is_created_on_a_fresh_install(self):
        call_command("bootstrap", "--no-demo", verbosity=0)
        self.assertTrue(User.objects.filter(is_superuser=True).exists())

    def test_the_username_and_password_come_from_the_environment(self):
        with mock.patch.dict(os.environ, {
            "ADMIN_USERNAME": "drkulkarni", "ADMIN_PASSWORD": "a-real-password-here",
        }):
            call_command("bootstrap", "--no-demo", verbosity=0)
        admin = User.objects.get(is_superuser=True)
        self.assertEqual(admin.username, "drkulkarni")
        self.assertTrue(admin.check_password("a-real-password-here"))

    def test_running_it_again_does_not_create_a_second_administrator(self):
        call_command("bootstrap", "--no-demo", verbosity=0)
        call_command("bootstrap", "--no-demo", verbosity=0)
        self.assertEqual(User.objects.filter(is_superuser=True).count(), 1)

    def test_a_password_someone_has_changed_is_not_reset_on_restart(self):
        # The container restarts with ADMIN_PASSWORD still set to the default.
        # Overwriting the real password every morning would be worse than
        # useless — it would look like the account had been compromised.
        call_command("bootstrap", "--no-demo", verbosity=0)
        admin = User.objects.get(is_superuser=True)
        admin.set_password("chosen-by-the-clinic")
        admin.save()

        call_command("bootstrap", "--no-demo", verbosity=0)

        admin.refresh_from_db()
        self.assertTrue(admin.check_password("chosen-by-the-clinic"))


class TestTheDemoDataGuard(TestCase):
    def test_an_empty_database_gets_demo_patients(self):
        call_command("bootstrap", verbosity=0)
        self.assertTrue(Patient.objects.exists())

    def test_a_database_with_real_patients_is_left_completely_alone(self):
        # The one that matters. A restart must never delete a clinic's records.
        mine = make_patient(first_name="Real", last_name="Patient")
        call_command("bootstrap", verbosity=0)

        self.assertEqual(Patient.objects.count(), 1)
        self.assertTrue(Patient.objects.filter(pk=mine.pk).exists())

    def test_a_second_start_does_not_reseed_over_the_demo_data(self):
        call_command("bootstrap", verbosity=0)
        first = set(Patient.objects.values_list("patient_id", flat=True))

        call_command("bootstrap", verbosity=0)
        second = set(Patient.objects.values_list("patient_id", flat=True))

        self.assertEqual(first, second)

    def test_seeding_can_be_switched_off_for_a_real_clinic(self):
        with mock.patch.dict(os.environ, {"SEED_DEMO": "0"}):
            call_command("bootstrap", verbosity=0)
        self.assertFalse(Patient.objects.exists())
        # The way in must still be created, or nobody could sign in at all.
        self.assertTrue(User.objects.filter(is_superuser=True).exists())

    def test_the_no_demo_flag_also_switches_it_off(self):
        call_command("bootstrap", "--no-demo", verbosity=0)
        self.assertFalse(Patient.objects.exists())


class TestItSurvivesBeingRunOnAWorkingSystem(TestCase):
    """
    A restart in the middle of a clinic day is the realistic case: somebody
    reboots the machine at lunchtime. Nothing about the day should change.
    """

    def test_visits_and_users_are_untouched(self):
        from appointments.models import Visit

        from .factories import make_doctor, make_receptionist, make_visit, later_today

        doctor = make_doctor()
        make_receptionist()
        patient = make_patient()
        visit = make_visit(patient, doctor, start=later_today())

        call_command("bootstrap", verbosity=0)

        self.assertTrue(Visit.objects.filter(pk=visit.pk).exists())
        self.assertTrue(Patient.objects.filter(pk=patient.pk).exists())
        self.assertTrue(User.objects.filter(pk=doctor.pk).exists())
