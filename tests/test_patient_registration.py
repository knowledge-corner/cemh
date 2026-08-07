"""
KAN-11 — the Register New Patient form.

The final field set, the guardian section that appears for a child, and the
duplicate check. The boundary cases the Definition of Done names by hand — a
date of birth exactly eighteen years ago, and one day short of it — are tested
explicitly, because "under 18" is the sort of rule that is written down as
``<=`` by accident and nobody notices for a year.
"""

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from patients import matching
from patients.models import Patient, Sex

from .factories import make_receptionist


def _dob_for_age(years, days_off=0):
    """A date of birth giving exactly ``years`` today, shifted by ``days_off``."""
    today = timezone.localdate()
    try:
        born = today.replace(year=today.year - years)
    except ValueError:                       # 29 February
        born = today.replace(year=today.year - years, day=28)
    return born + timedelta(days=days_off)


class TestTheFieldSet(TestCase):
    """FR-1 to FR-4, AC-1 and AC-2."""

    def setUp(self):
        self.client.force_login(make_receptionist())

    def _body(self):
        return self.client.get(reverse("reception_register_patient")).content.decode()

    def test_it_asks_for_the_five_things_it_needs(self):
        body = self._body()
        for name in ("first_name", "last_name", "date_of_birth", "sex", "phone"):
            self.assertIn(f'name="{name}"', body)

    def test_the_removed_fields_are_gone(self):
        # FR-3, and the rest of the trim.
        body = self._body()
        for name in ("alternate_phone", "guardian_phone", "blood_group",
                     "email", "address", "city", "pincode", "referred_by"):
            self.assertNotIn(f'name="{name}"', body, msg=name)

    def test_the_gender_field_is_labelled_gender(self):
        # FR-2. The column is still `sex`; renaming it would be a data
        # migration to change a word the database never shows anybody.
        self.assertIn("Gender", self._body())

    def test_prefer_not_to_say_is_offered(self):
        self.assertIn("Prefer not to say", self._body())

    def test_that_choice_actually_saves(self):
        self.client.post(reverse("reception_register_patient"), {
            "first_name": "Alex", "last_name": "Pereira",
            "date_of_birth": _dob_for_age(30).isoformat(),
            "sex": Sex.NOT_STATED, "phone": "9820011111",
            "guardian_name": "", "guardian_relation": "",
        })
        self.assertEqual(Patient.objects.get(first_name="Alex").sex, Sex.NOT_STATED)

    def test_there_is_one_cancel_and_it_is_at_the_foot(self):
        # FR-4 — the one at the top of the page is gone.
        self.assertEqual(self._body().count(">Cancel</a>"), 1)

    def test_the_full_record_is_still_editable_from_the_chart(self):
        # KAN-11 puts editing an existing patient out of scope, so trimming the
        # registration form must not trim the chart's form with it.
        from portal.forms import PatientForm

        for name in ("blood_group", "address", "email", "alternate_phone"):
            self.assertIn(name, PatientForm.Meta.fields, msg=name)


class TestTheGuardianSection(TestCase):
    """FR-5 to FR-8, AC-3 to AC-6, and the boundary cases in the DoD."""

    def setUp(self):
        self.client.force_login(make_receptionist())

    def _register(self, **overrides):
        payload = {
            "first_name": "Rohan", "last_name": "Kulkarni",
            "date_of_birth": _dob_for_age(10).isoformat(),
            "sex": Sex.MALE, "phone": "9820012345",
            "guardian_name": "", "guardian_relation": "",
        }
        payload.update(overrides)
        return self.client.post(reverse("reception_register_patient"), payload)

    def test_a_child_without_a_guardian_is_refused(self):
        # AC-5, and the API case: the section is hidden by the page, but the
        # rule lives on the server, so posting straight at it is still refused.
        response = self._register()
        self.assertEqual(Patient.objects.count(), 0)
        self.assertContains(response, "is needed")

    def test_a_child_with_a_guardian_is_registered(self):
        self._register(guardian_name="Meera Kulkarni", guardian_relation="Mother")
        patient = Patient.objects.get()
        self.assertEqual(patient.guardian_name, "Meera Kulkarni")
        self.assertEqual(patient.guardian_relation, "Mother")

    def test_an_adult_needs_no_guardian(self):
        self._register(first_name="Meera", date_of_birth=_dob_for_age(30).isoformat())
        self.assertEqual(Patient.objects.count(), 1)

    def test_guardian_details_sent_for_an_adult_are_discarded(self):
        # FR-8 / AC-6. The page disables the fields, but a value left over from
        # before the date of birth was corrected must not be stored either.
        self._register(
            first_name="Meera",
            date_of_birth=_dob_for_age(30).isoformat(),
            guardian_name="Somebody Stale", guardian_relation="Mother",
        )
        patient = Patient.objects.get()
        self.assertEqual(patient.guardian_name, "")
        self.assertEqual(patient.guardian_relation, "")

    def test_exactly_eighteen_today_needs_no_guardian(self):
        # The boundary the DoD names. Eighteen today is eighteen, not seventeen.
        self._register(first_name="Priya", date_of_birth=_dob_for_age(18).isoformat())
        self.assertEqual(Patient.objects.count(), 1)

    def test_one_day_short_of_eighteen_needs_one(self):
        response = self._register(
            first_name="Priya", date_of_birth=_dob_for_age(18, days_off=1).isoformat()
        )
        self.assertEqual(Patient.objects.count(), 0)
        self.assertContains(response, "is needed")

    def test_the_section_starts_hidden(self):
        # AC-3.
        body = self.client.get(reverse("reception_register_patient")).content.decode()
        self.assertIn('id="guardian-section"', body)
        self.assertIn("hidden", body)

    def test_a_future_date_of_birth_is_refused(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        response = self._register(date_of_birth=tomorrow.isoformat())
        self.assertEqual(Patient.objects.count(), 0)
        self.assertContains(response, "cannot be in the future")

    def test_an_implausible_date_of_birth_is_refused(self):
        response = self._register(date_of_birth=date(1850, 1, 1).isoformat())
        self.assertEqual(Patient.objects.count(), 0)
        self.assertContains(response, "Check the year")


class TestNormalisingBeforeComparing(TestCase):
    """The edge cases the ticket names for the duplicate key."""

    def test_a_name_is_compared_without_case_or_spacing(self):
        self.assertEqual(
            matching.normalise_name("  meera   KULKARNI "),
            matching.normalise_name("Meera Kulkarni"),
        )

    def test_a_phone_is_compared_without_its_decoration(self):
        for written in ("+91 98200 12345", "098200-12345", "9820012345",
                        "+919820012345", "98200 12345"):
            self.assertEqual(
                matching.normalise_phone(written), "9820012345", msg=written,
            )

    def test_a_short_number_is_not_padded_into_a_match(self):
        self.assertNotEqual(matching.normalise_phone("12345"), "9820012345")


class TestTheDuplicateCheck(TestCase):
    """FR-9, FR-10, AC-7 to AC-9 and T-10, T-11."""

    def setUp(self):
        self.client.force_login(make_receptionist())
        self.existing = Patient.objects.create(
            first_name="Meera", last_name="Kulkarni",
            date_of_birth=_dob_for_age(34), sex=Sex.FEMALE,
            phone="9820012345",
        )

    def _register(self, **overrides):
        payload = {
            "first_name": "Meera", "last_name": "Kulkarni",
            "date_of_birth": _dob_for_age(34).isoformat(),
            "sex": Sex.FEMALE, "phone": "9820012345",
            "guardian_name": "", "guardian_relation": "",
        }
        payload.update(overrides)
        return self.client.post(reverse("reception_register_patient"), payload)

    def test_an_exact_match_is_warned_about_before_creating(self):
        response = self._register()
        self.assertEqual(Patient.objects.count(), 1)
        self.assertContains(response, "already registered")
        self.assertContains(response, self.existing.patient_id)

    def test_a_difference_of_case_alone_still_matches(self):
        # T-10.
        self._register(first_name="MEERA", last_name="kulkarni")
        self.assertEqual(Patient.objects.count(), 1)

    def test_a_difference_of_spacing_alone_still_matches(self):
        self._register(first_name="  Meera ", last_name="Kulkarni")
        self.assertEqual(Patient.objects.count(), 1)

    def test_the_same_number_written_differently_still_matches(self):
        # T-11.
        for written in ("+91 98200 12345", "098200-12345", "98200 12345"):
            with self.subTest(phone=written):
                self._register(phone=written)
                self.assertEqual(Patient.objects.count(), 1, msg=written)

    def test_reception_can_confirm_it_is_a_different_person(self):
        # AC-8. Two people really can share a name and a number.
        self._register()
        self._register(confirm="1")
        self.assertEqual(Patient.objects.count(), 2)

    def test_a_different_number_is_not_a_duplicate(self):
        # AC-9.
        self._register(phone="9769087654")
        self.assertEqual(Patient.objects.count(), 2)

    def test_a_family_sharing_a_number_is_not_a_duplicate(self):
        # The reason the key is name *and* number. A mother's mobile sits on
        # three of her children's records.
        self._register(
            first_name="Rohan",
            date_of_birth=_dob_for_age(9).isoformat(),
            guardian_name="Meera Kulkarni", guardian_relation="Mother",
        )
        self.assertEqual(Patient.objects.count(), 2)

    def test_an_inactive_patient_is_not_offered_as_a_duplicate(self):
        self.existing.is_active = False
        self.existing.save()
        self._register(first_name="Meera")
        self.assertEqual(Patient.objects.filter(is_active=True).count(), 1)


class TestTheSurname(TestCase):
    """
    The ticket makes Last Name required, because it is part of the key the
    duplicate check compares on. It also asks, in its own Open Questions, how a
    patient with only one name is meant to be handled — which this does not
    answer. Pinned so the behaviour is deliberate rather than incidental.
    """

    def setUp(self):
        self.client.force_login(make_receptionist())

    def test_a_surname_is_required_at_registration(self):
        response = self.client.post(reverse("reception_register_patient"), {
            "first_name": "Meera", "last_name": "",
            "date_of_birth": _dob_for_age(30).isoformat(),
            "sex": Sex.FEMALE, "phone": "9820012345",
            "guardian_name": "", "guardian_relation": "",
        })
        self.assertEqual(Patient.objects.count(), 0)
        self.assertContains(response, "required")

    def test_the_model_still_allows_one_without(self):
        # Records already carrying no surname, and the admin, are untouched.
        Patient.objects.create(
            first_name="Lakshmi", last_name="",
            date_of_birth=_dob_for_age(40), sex=Sex.FEMALE, phone="9820011111",
        )
        self.assertEqual(Patient.objects.count(), 1)
