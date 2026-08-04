"""
KAN-37 — Doctor category becomes Specialisation.

An edit to KAN-21, which shipped three hard-coded values: Adult, Paediatric,
Adult & paediatric. Those were a division of the patient list rather than
specialisations, and reception could not add to them without a release.

Now a table, seeded with the standard list, that reception extends from the
same dropdown they pick from.
"""

from django.test import TestCase
from django.urls import reverse

from accounts.models import DoctorProfile, Specialisation, User

from .factories import make_doctor, make_receptionist


def _form(**overrides):
    payload = {
        "full_name": "Nikhil Sharma",
        "email": "nikhil@example.in",
        "phone": "9820055555",
        "specialisation": str(Specialisation.objects.get(name="Cardiology").pk),
        "new_specialisation": "",
        "registration_number": "MMC-99887",
        "qualification": "MBBS, MD",
    }
    payload.update(overrides)
    return payload


class TestTheStandardList(TestCase):
    """"Add all standard specializations to the list.\""""

    def test_the_list_is_seeded(self):
        self.assertGreaterEqual(Specialisation.objects.count(), 20)

    def test_it_carries_the_ones_this_clinic_needs(self):
        # An endocrine clinic that cannot pick endocrinology would be a poor
        # standard list.
        for name in ("Endocrinology", "Paediatric Endocrinology", "Diabetology"):
            self.assertTrue(
                Specialisation.objects.filter(name=name).exists(), msg=name
            )

    def test_it_carries_the_ordinary_ones_too(self):
        for name in ("Cardiology", "Paediatrics", "General Medicine",
                     "Dermatology", "Orthopaedics"):
            self.assertTrue(
                Specialisation.objects.filter(name=name).exists(), msg=name
            )

    def test_the_old_category_values_are_gone(self):
        # They were never specialisations. If they had been carried across,
        # a doctor would be showing "Adult" where a discipline belongs.
        for name in ("Adult", "Paediatric", "Adult & paediatric"):
            self.assertFalse(
                Specialisation.objects.filter(name=name).exists(), msg=name
            )

    def test_the_profile_no_longer_has_a_category(self):
        self.assertFalse(hasattr(DoctorProfile(), "category"))


class TestTheDropdown(TestCase):
    def setUp(self):
        self.client.force_login(make_receptionist())

    def _page(self):
        return self.client.get(reverse("reception_add_doctor"))

    def test_the_field_is_called_specialisation(self):
        body = self._page().content.decode()
        self.assertIn("Specialisation", body)
        self.assertNotIn("Doctor category", body)

    def test_every_active_specialisation_is_offered(self):
        choices = dict(self._page().context["form"].fields["specialisation"].choices)
        for name in ("Endocrinology", "Cardiology", "Neurology"):
            self.assertIn(name, choices.values(), msg=name)

    def test_the_dropdown_offers_adding_a_new_one(self):
        # "In the same dropdown, provide an option to add a new specialization."
        choices = dict(self._page().context["form"].fields["specialisation"].choices)
        self.assertIn("__new__", choices)
        self.assertIn("Add a new specialisation", choices["__new__"])

    def test_a_retired_specialisation_is_not_offered(self):
        Specialisation.objects.filter(name="Cardiology").update(is_active=False)
        choices = dict(self._page().context["form"].fields["specialisation"].choices)
        self.assertNotIn("Cardiology", choices.values())

    def test_one_added_a_moment_ago_appears_without_a_restart(self):
        # The choices are read per request, not at import.
        Specialisation.objects.create(name="Andrology")
        choices = dict(self._page().context["form"].fields["specialisation"].choices)
        self.assertIn("Andrology", choices.values())


class TestPickingOne(TestCase):
    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)

    def _add(self, **overrides):
        return self.client.post(reverse("reception_add_doctor"), _form(**overrides))

    def test_the_chosen_one_is_stored(self):
        self._add()
        profile = DoctorProfile.objects.get(user__email="nikhil@example.in")
        self.assertEqual(profile.specialisation.name, "Cardiology")

    def test_it_is_required(self):
        response = self._add(specialisation="")
        self.assertEqual(User.objects.filter(email="nikhil@example.in").count(), 0)
        self.assertEqual(response.status_code, 200)


class TestAddingANewOne(TestCase):
    """"The newly added specialization should be stored in the database.\""""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)

    def _add(self, **overrides):
        return self.client.post(reverse("reception_add_doctor"), _form(**overrides))

    def test_it_is_created_and_attached_to_the_doctor(self):
        self._add(specialisation="__new__", new_specialisation="Reproductive Endocrinology")

        created = Specialisation.objects.get(name="Reproductive Endocrinology")
        profile = DoctorProfile.objects.get(user__email="nikhil@example.in")
        self.assertEqual(profile.specialisation, created)

    def test_it_survives_for_the_next_doctor(self):
        self._add(specialisation="__new__", new_specialisation="Andrology")

        choices = dict(
            self.client.get(reverse("reception_add_doctor"))
            .context["form"].fields["specialisation"].choices
        )
        self.assertIn("Andrology", choices.values())

    def test_who_added_it_is_recorded(self):
        self._add(specialisation="__new__", new_specialisation="Andrology")
        self.assertEqual(
            Specialisation.objects.get(name="Andrology").created_by, self.receptionist
        )

    def test_choosing_add_new_without_typing_one_is_refused(self):
        response = self._add(specialisation="__new__", new_specialisation="")
        self.assertEqual(User.objects.filter(email="nikhil@example.in").count(), 0)
        self.assertContains(response, "Type the specialisation")

    def test_the_name_is_tidied_before_it_is_stored(self):
        self._add(specialisation="__new__", new_specialisation="  Sports   Medicine ")
        self.assertTrue(Specialisation.objects.filter(name="Sports Medicine").exists())

    def test_a_name_that_already_exists_is_reused_not_duplicated(self):
        # Otherwise the list grows a second Cardiology and the filter shows two
        # of everything.
        self._add(specialisation="__new__", new_specialisation="cardiology")

        self.assertEqual(Specialisation.objects.filter(name__iexact="cardiology").count(), 1)
        profile = DoctorProfile.objects.get(user__email="nikhil@example.in")
        self.assertEqual(profile.specialisation.name, "Cardiology")

    def test_the_same_name_differing_only_in_spacing_is_reused_too(self):
        self._add(specialisation="__new__", new_specialisation="General  Medicine")
        self.assertEqual(
            Specialisation.objects.filter(name__iexact="general medicine").count(), 1
        )

    def test_re_adding_a_retired_one_says_what_to_do(self):
        Specialisation.objects.filter(name="Cardiology").update(is_active=False)
        response = self._add(specialisation="__new__", new_specialisation="Cardiology")
        self.assertContains(response, "has been retired")

    def test_a_typed_name_is_ignored_when_one_was_picked_from_the_list(self):
        # Left over from changing your mind. It must not create a stray row.
        self._add(new_specialisation="Something Abandoned")
        self.assertFalse(
            Specialisation.objects.filter(name="Something Abandoned").exists()
        )

    def test_a_doctor_cannot_add_specialisations(self):
        self.client.force_login(make_doctor())
        self._add(specialisation="__new__", new_specialisation="Sneaky")
        self.assertFalse(Specialisation.objects.filter(name="Sneaky").exists())


class TestTheListItself(TestCase):
    def test_two_rows_cannot_share_a_name(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Specialisation.objects.create(name="cardiology")

    def test_a_specialisation_in_use_cannot_be_deleted(self):
        # PROTECT. Removing it would rewrite the records of every doctor on it
        # in order to tidy a dropdown.
        from django.db.models import ProtectedError

        doctor = make_doctor(username="dr-spec", email="spec@example.in")
        cardiology = Specialisation.objects.get(name="Cardiology")
        DoctorProfile.objects.create(user=doctor, specialisation=cardiology)

        with self.assertRaises(ProtectedError):
            cardiology.delete()

    def test_an_unused_one_can_be_deleted(self):
        Specialisation.objects.create(name="Temporary").delete()
        self.assertFalse(Specialisation.objects.filter(name="Temporary").exists())


class TestTheDoctorsScreen(TestCase):
    def setUp(self):
        self.client.force_login(make_receptionist())

    def test_it_shows_the_specialisation(self):
        doctor = make_doctor(username="dr-card", email="card@example.in")
        DoctorProfile.objects.create(
            user=doctor, specialisation=Specialisation.objects.get(name="Cardiology"),
        )
        self.assertContains(self.client.get(reverse("reception_doctors")), "Cardiology")

    def test_a_doctor_without_one_is_flagged_rather_than_left_blank(self):
        # Nothing carried across from the old Adult/Paediatric values, so this
        # is what an existing doctor looks like until somebody picks.
        doctor = make_doctor(username="dr-none", email="none@example.in")
        DoctorProfile.objects.create(user=doctor)

        response = self.client.get(reverse("reception_doctors"))
        self.assertContains(response, "Not set")

    def test_the_column_is_headed_specialisation(self):
        # There has to be a doctor, or the page shows its empty state and no
        # table header at all — which would pass the "not Category" half while
        # proving nothing.
        doctor = make_doctor(username="dr-head", email="head@example.in")
        DoctorProfile.objects.create(user=doctor)

        response = self.client.get(reverse("reception_doctors"))
        self.assertContains(response, "<th>Specialisation</th>", html=False)
        self.assertNotContains(response, "<th>Category</th>")
