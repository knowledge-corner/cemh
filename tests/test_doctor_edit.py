"""
Correcting a doctor's own details from the doctors list (task #20), and the
Calendar link that already sits in that page's nav bar (task #21) — pinned
here with a regression test rather than left to accident.
"""

from django.test import TestCase
from django.urls import reverse

from accounts.models import DoctorProfile, Role, Specialisation, User

from .factories import make_doctor, make_receptionist


class DoctorEditTestCase(TestCase):
    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor(
            username="drvrushali", email="vrushali@example.in",
            first_name="Vrushali", last_name="Kulkarni", phone="9820011111",
        )
        self.specialisation = Specialisation.objects.get(name="Paediatric Endocrinology")
        DoctorProfile.objects.create(
            user=self.doctor, specialisation=self.specialisation,
            registration_number="MMC-999", qualification="MBBS",
        )
        self.client.force_login(self.receptionist)

    def _payload(self, **overrides):
        payload = {
            "full_name": "Vrushali Kulkarni",
            "email": "vrushali@example.in",
            "phone": "9820011111",
            "specialisation": str(self.specialisation.pk),
            "new_specialisation": "",
            "registration_number": "MMC-999",
            "qualification": "MBBS",
        }
        payload.update(overrides)
        return payload

    def _edit(self, **overrides):
        return self.client.post(
            reverse("reception_edit_doctor", args=[self.doctor.pk]), self._payload(**overrides)
        )


class TestTheDoctorsListOffersEdit(DoctorEditTestCase):
    def test_the_list_links_to_edit(self):
        response = self.client.get(reverse("reception_doctors"))
        self.assertContains(response, reverse("reception_edit_doctor", args=[self.doctor.pk]))

    def test_the_list_still_links_to_the_calendar(self):
        # Already wired up via _nav.html's nav_active == "doctors" branch —
        # pinned so it cannot regress silently.
        response = self.client.get(reverse("reception_doctors"))
        self.assertContains(response, reverse("reception_calendar"))


class TestOpeningTheEditScreen(DoctorEditTestCase):
    def test_it_opens(self):
        response = self.client.get(reverse("reception_edit_doctor", args=[self.doctor.pk]))
        self.assertEqual(response.status_code, 200)

    def test_a_doctor_cannot_open_it(self):
        self.client.force_login(self.doctor)
        response = self.client.get(reverse("reception_edit_doctor", args=[self.doctor.pk]))
        self.assertEqual(response.status_code, 403)

    def test_the_form_is_pre_filled(self):
        response = self.client.get(reverse("reception_edit_doctor", args=[self.doctor.pk]))
        self.assertContains(response, "Vrushali Kulkarni")
        self.assertContains(response, "vrushali@example.in")
        self.assertContains(response, "MMC-999")

    def test_the_username_is_shown_but_not_as_an_editable_field(self):
        # Reception needs to be able to tell the doctor what they actually
        # sign in with — the username, not the email above it.
        response = self.client.get(reverse("reception_edit_doctor", args=[self.doctor.pk]))
        self.assertContains(response, self.doctor.username)
        self.assertNotContains(response, 'name="username"')

    def test_a_missing_doctor_profile_does_not_break_the_page(self):
        # A doctor added under an older flow, or mid-invitation, may not have
        # a profile row yet — the screen must still open.
        bare = make_doctor(username="drbare", email="bare@example.in")
        response = self.client.get(reverse("reception_edit_doctor", args=[bare.pk]))
        self.assertEqual(response.status_code, 200)


class TestSavingChanges(DoctorEditTestCase):
    def test_the_name_is_updated(self):
        self._edit(full_name="Vrushali Rao")
        self.doctor.refresh_from_db()
        self.assertEqual(self.doctor.first_name, "Vrushali")
        self.assertEqual(self.doctor.last_name, "Rao")

    def test_the_phone_is_updated(self):
        self._edit(phone="9820099999")
        self.doctor.refresh_from_db()
        self.assertEqual(self.doctor.phone, "9820099999")

    def test_the_qualification_and_registration_are_updated(self):
        self._edit(qualification="MBBS, MD", registration_number="MMC-2026")
        self.doctor.doctor_profile.refresh_from_db()
        self.assertEqual(self.doctor.doctor_profile.qualification, "MBBS, MD")
        self.assertEqual(self.doctor.doctor_profile.registration_number, "MMC-2026")

    def test_the_specialisation_can_be_changed(self):
        other = Specialisation.objects.create(name="Test Adult Endocrinology")
        self._edit(specialisation=str(other.pk))
        self.doctor.doctor_profile.refresh_from_db()
        self.assertEqual(self.doctor.doctor_profile.specialisation, other)

    def test_a_new_specialisation_can_be_added(self):
        self._edit(specialisation="__new__", new_specialisation="Test New Field")
        self.doctor.doctor_profile.refresh_from_db()
        self.assertEqual(self.doctor.doctor_profile.specialisation.name, "Test New Field")

    def test_the_email_can_be_changed(self):
        self._edit(email="vrushali.new@example.in")
        self.doctor.refresh_from_db()
        self.assertEqual(self.doctor.email, "vrushali.new@example.in")

    def test_changing_the_email_does_not_change_the_username(self):
        # The email is where the invitation and notifications go; the
        # username, generated once when the doctor was added, is what they
        # actually sign in with, and the two are not meant to track each
        # other — see the note on DoctorEditForm.
        username_before = self.doctor.username
        self._edit(email="vrushali.new@example.in")
        self.doctor.refresh_from_db()
        self.assertEqual(self.doctor.username, username_before)

    def test_a_duplicate_email_is_refused(self):
        other = make_doctor(username="drother", email="other@example.in")
        response = self._edit(email="other@example.in")
        self.assertContains(response, "already registered")
        self.doctor.refresh_from_db()
        self.assertEqual(self.doctor.email, "vrushali@example.in")

    def test_saving_without_touching_the_email_is_not_a_duplicate_of_itself(self):
        # The uniqueness check has to exclude this doctor's own row, or
        # saving any other field trips over the address already being theirs.
        response = self._edit(full_name="Vrushali Rao")
        self.assertRedirects(response, reverse("reception_doctors"))

    def test_it_redirects_to_the_doctors_list(self):
        response = self._edit()
        self.assertRedirects(response, reverse("reception_doctors"))

    def test_saving_is_audited(self):
        from audit.models import AccessLog, AuditAction
        self._edit(full_name="Vrushali Rao")
        entry = AccessLog.objects.filter(action=AuditAction.UPDATE).latest("id")
        self.assertEqual(entry.username, self.receptionist.username)

    def test_a_doctor_cannot_save_changes_either(self):
        self.client.force_login(self.doctor)
        response = self._edit(full_name="Somebody Else")
        self.assertEqual(response.status_code, 403)


class TestARetiredSpecialisationStaysVisible(DoctorEditTestCase):
    def test_the_current_choice_is_not_silently_dropped(self):
        self.specialisation.is_active = False
        self.specialisation.save()

        response = self.client.get(reverse("reception_edit_doctor", args=[self.doctor.pk]))
        self.assertContains(response, "retired")

        # And saving without touching the dropdown keeps it.
        self._edit()
        self.doctor.doctor_profile.refresh_from_db()
        self.assertEqual(self.doctor.doctor_profile.specialisation, self.specialisation)
