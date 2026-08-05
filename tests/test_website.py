"""
The public website.

The clinic's shopfront, and the only page in the system meant to be found by a
stranger. Two things matter more than the rest:

* It must never leak anything about a patient. It is unauthenticated and
  indexable, so a mistake here is a mistake in public.
* The callback form must actually reach somebody. A form that silently does
  nothing is worse than no form at all — the visitor has been told the clinic
  will ring, and it will not.
"""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import DoctorProfile, Specialisation
from website import photos
from website.models import CallbackRequest, CallbackStatus

from .factories import (
    make_doctor, make_history, make_patient, make_receptionist, make_visit,
)


def a_valid_request(**overrides):
    payload = {
        "name": "Meera Kulkarni",
        "phone": "9820012345",
        "preferred_doctor": "",
        "concern": "Thyroid follow-up",
    }
    payload.update(overrides)
    return payload


class TestPublicPage(TestCase):
    def setUp(self):
        self.doctor = make_doctor(first_name="Vrushali", last_name="Kulkarni")

    def test_renders_for_a_signed_out_visitor(self):
        response = self.client.get(reverse("website_home"))
        self.assertEqual(response.status_code, 200)

    def test_root_url_is_the_public_page_not_a_login_redirect(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

    def test_shows_the_clinic_identity_and_address(self):
        response = self.client.get(reverse("website_home"))
        self.assertContains(response, "Centre for Endocrine")
        self.assertContains(response, "Borivali West")

    def test_lists_the_doctors(self):
        response = self.client.get(reverse("website_home"))
        self.assertContains(response, "Vrushali Kulkarni")

    def test_lists_what_the_clinic_treats(self):
        response = self.client.get(reverse("website_home"))
        self.assertContains(response, "Thyroid disorders")
        self.assertContains(response, "Growth and short stature")

    def test_offers_a_telephone_link(self):
        response = self.client.get(reverse("website_home"))
        self.assertContains(response, 'href="tel:')

    def test_offers_a_whatsapp_link_in_international_format(self):
        response = self.client.get(reverse("website_home"))
        # wa.me needs the country code and no punctuation.
        self.assertContains(response, "https://wa.me/91")

    def test_is_indexable_unlike_every_other_page(self):
        response = self.client.get(reverse("website_home"))
        self.assertContains(response, "index, follow")

    def test_offers_staff_a_way_in(self):
        response = self.client.get(reverse("website_home"))
        self.assertContains(response, reverse("login"))

    def test_every_section_the_navigation_points_at_exists(self):
        # Five links in the header, five anchors. A nav item that scrolls
        # nowhere is the kind of thing nobody notices until a patient does.
        body = self.client.get(reverse("website_home")).content.decode()
        for anchor in ("about", "doctors", "services", "faqs", "contact"):
            with self.subTest(anchor=anchor):
                self.assertIn(f'href="#{anchor}"', body)
                self.assertIn(f'id="{anchor}"', body)

    def test_it_does_not_load_the_application_stylesheet(self):
        # The inside-the-application theme is a working tool built for a
        # receptionist looking at it all day. A shopfront is not.
        body = self.client.get(reverse("website_home")).content.decode()
        self.assertIn("css/website.css", body)
        self.assertNotIn("css/app.css", body)


class TestPublicPageLeaksNothing(TestCase):
    """The page is public, so it must contain no trace of any patient."""

    def setUp(self):
        self.doctor = make_doctor()
        self.patient = make_patient(first_name="Aarav", last_name="Deshpande")
        make_history(self.patient, allergies="Sulfa drugs")
        make_visit(self.patient, self.doctor)

    def test_no_patient_name_uhid_or_number_appears(self):
        response = self.client.get(reverse("website_home"))
        for secret in (self.patient.first_name, self.patient.last_name,
                       self.patient.patient_id, self.patient.phone):
            self.assertNotContains(response, secret, msg_prefix=f"leaked {secret!r}:")

    def test_no_clinical_detail_appears(self):
        response = self.client.get(reverse("website_home"))
        self.assertNotContains(response, "Sulfa drugs")

    def test_somebody_elses_callback_request_is_not_shown_back(self):
        # It is stored, and only reception may see it.
        CallbackRequest.objects.create(name="Somebody Else", phone="9820011111")
        self.assertNotContains(
            self.client.get(reverse("website_home")), "Somebody Else"
        )


class TestEverythingElseStillNeedsALogin(TestCase):
    def test_dashboard_redirects_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_reception_and_doctor_areas_are_not_public(self):
        for name in ("reception_home", "doctor_home"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, msg=name)


# ── The doctors, introduced ──────────────────────────────────────────────────

class TestTheDoctorsAreIntroduced(TestCase):
    def setUp(self):
        self.url = reverse("website_home")
        self.doctor = make_doctor(
            username="dr-vrushali", email="drvrushali@example.in",
            first_name="Vrushali", last_name="Kulkarni", phone="7620351240",
        )
        self.profile = DoctorProfile.objects.create(
            user=self.doctor,
            specialisation=Specialisation.objects.get(name="Paediatric Endocrinology"),
            qualification="MBBS, MD (Pediatrics)",
            bio="First paragraph about her.\n\nSecond paragraph about her.",
            activated_at=timezone.now(),
        )

    def test_their_specialisation_is_shown(self):
        self.assertContains(self.client.get(self.url), "Paediatric Endocrinology")

    def test_their_qualification_is_shown(self):
        self.assertContains(self.client.get(self.url), "MBBS, MD (Pediatrics)")

    def test_the_bio_is_broken_into_paragraphs(self):
        response = self.client.get(self.url)
        self.assertContains(response, "First paragraph about her.")
        self.assertContains(response, "Second paragraph about her.")
        # Two paragraphs, not one blob with a blank line inside it.
        self.assertEqual(len(response.context["doctors"][0]["paragraphs"]), 2)

    def test_a_doctor_with_no_bio_is_still_listed(self):
        self.profile.bio = ""
        self.profile.save()
        self.assertContains(self.client.get(self.url), "Vrushali Kulkarni")

    def test_a_doctor_who_has_left_is_not_listed(self):
        self.doctor.is_active = False
        self.doctor.save()
        self.assertNotContains(self.client.get(self.url), "Vrushali Kulkarni")

    def test_a_doctor_who_has_not_activated_is_still_listed(self):
        # Being appointed is what makes somebody a doctor at this clinic.
        # Whether they have answered an invitation email is not the public's
        # business, and hiding them until they do would be an odd rule.
        self.profile.activated_at = None
        self.profile.save()
        self.assertContains(self.client.get(self.url), "Vrushali Kulkarni")

    def test_the_page_holds_up_with_no_doctors_at_all(self):
        self.doctor.delete()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please telephone")


# ── Photographs come from the photos folder ──────────────────────────────────

class TestThePhotosFolder(TestCase):
    """
    The clinic drops a file into ``photos/`` named after the doctor. Nothing
    else should be needed, and nothing should break when the file is not there.
    """

    def setUp(self):
        self.url = reverse("website_home")
        self.doctor = make_doctor(
            username="dr-vrushali", email="v@example.in",
            first_name="Vrushali", last_name="Kulkarni",
        )
        DoctorProfile.objects.create(user=self.doctor)

    def test_the_folder_is_not_empty(self):
        # Guards the guard: every assertion below would pass vacuously against
        # a folder that was never wired into the static files settings.
        self.assertTrue(photos.available_photos())

    def test_a_photo_named_after_the_doctor_is_found(self):
        self.assertIsNotNone(photos.photo_url(self.doctor))

    def test_it_is_served_from_the_photos_folder(self):
        self.assertIn("photos/vrushali-kulkarni", photos.photo_url(self.doctor))

    def test_the_page_shows_it(self):
        self.assertContains(self.client.get(self.url), "vrushali-kulkarni")

    def test_a_doctor_with_no_photo_gets_initials_not_a_broken_image(self):
        # A broken image on a clinic's front page reads as a broken clinic.
        nameless = make_doctor(username="dr-new", email="new@example.in",
                               first_name="Nikhil", last_name="Sharma")
        DoctorProfile.objects.create(user=nameless)

        self.assertIsNone(photos.photo_url(nameless))
        response = self.client.get(self.url)
        self.assertNotContains(response, "nikhil-sharma")
        self.assertContains(response, "NS")

    def test_the_username_is_tried_as_well_as_the_name(self):
        # Whoever saves the file uses whichever of the two they have to hand.
        renamed = make_doctor(username="adway-kulkarni", email="a@example.in",
                              first_name="Adway", last_name="Kulkarni")
        self.assertIn("adway-kulkarni", photos.photo_url(renamed))


# ── The callback form ────────────────────────────────────────────────────────

class TestAskingForACallback(TestCase):
    def setUp(self):
        self.url = reverse("website_home")

    def submit(self, **overrides):
        return self.client.post(self.url, a_valid_request(**overrides))

    def test_the_form_is_on_the_page(self):
        self.assertContains(self.client.get(self.url), 'name="phone"')

    def test_a_valid_request_is_stored(self):
        self.submit()
        stored = CallbackRequest.objects.get()
        self.assertEqual((stored.name, stored.phone), ("Meera Kulkarni", "9820012345"))

    def test_it_starts_as_something_still_to_do(self):
        self.submit()
        self.assertTrue(CallbackRequest.objects.get().is_outstanding)

    def test_the_visitor_is_told_it_worked(self):
        response = self.client.post(self.url, a_valid_request(), follow=True)
        self.assertContains(response, "will ring you back")

    def test_it_redirects_so_a_refresh_does_not_send_it_twice(self):
        # Otherwise reception rings the same person once per refresh.
        self.assertEqual(self.submit().status_code, 302)

    def test_a_missing_name_is_refused(self):
        self.submit(name="")
        self.assertEqual(CallbackRequest.objects.count(), 0)

    def test_a_missing_number_is_refused(self):
        # There would be nothing to ring.
        self.submit(phone="")
        self.assertEqual(CallbackRequest.objects.count(), 0)

    def test_a_number_that_cannot_be_rung_is_refused_with_a_reason(self):
        response = self.submit(phone="12")
        self.assertEqual(CallbackRequest.objects.count(), 0)
        self.assertContains(response, "valid 10-digit Indian mobile number")

    def test_the_concern_is_optional(self):
        self.submit(concern="")
        self.assertEqual(CallbackRequest.objects.count(), 1)

    def test_no_preference_is_a_real_answer(self):
        self.submit(preferred_doctor="")
        self.assertEqual(CallbackRequest.objects.get().preferred_doctor, "")

    def test_a_named_doctor_is_recorded(self):
        doctor = make_doctor(username="dr-a", email="a@example.in",
                             first_name="Adway", last_name="Kulkarni")
        DoctorProfile.objects.create(user=doctor, activated_at=timezone.now())

        self.submit(preferred_doctor="Adway Kulkarni")
        self.assertEqual(
            CallbackRequest.objects.get().preferred_doctor, "Adway Kulkarni"
        )

    def test_the_name_is_tidied(self):
        self.submit(name="  Meera   Kulkarni ")
        self.assertEqual(CallbackRequest.objects.get().name, "Meera Kulkarni")

    def test_a_doctor_who_has_left_is_not_offered(self):
        gone = make_doctor(username="dr-gone", email="gone@example.in",
                           first_name="Departed", last_name="Doctor")
        DoctorProfile.objects.create(user=gone, activated_at=timezone.now())
        gone.is_active = False
        gone.save()

        offered = dict(
            self.client.get(self.url).context["form"].fields["preferred_doctor"].choices
        )
        self.assertNotIn("Departed Doctor", offered)


# ── Reception has to be able to see them ─────────────────────────────────────

class TestReceptionSeesTheRequests(TestCase):
    """
    The form promises a telephone call. Without a screen where somebody can see
    what is outstanding, that promise is made to a table nobody opens.
    """

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.request_row = CallbackRequest.objects.create(
            name="Meera Kulkarni", phone="9820012345", concern="Thyroid follow-up",
        )
        self.url = reverse("reception_callbacks")

    def _close(self, status):
        return self.client.post(
            reverse("reception_close_callback", args=[self.request_row.pk]),
            {"status": status},
        )

    def test_reception_can_open_the_list(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_an_outstanding_request_is_shown(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Meera Kulkarni")
        self.assertContains(response, "9820012345")

    def test_the_number_is_a_link_that_dials(self):
        self.assertContains(self.client.get(self.url), 'href="tel:9820012345"')

    def test_marking_it_called_records_who_and_when(self):
        self._close("DONE")
        self.request_row.refresh_from_db()
        self.assertEqual(self.request_row.status, CallbackStatus.DONE)
        self.assertEqual(self.request_row.handled_by, self.receptionist)
        self.assertIsNotNone(self.request_row.handled_at)

    def test_it_then_leaves_the_to_call_list(self):
        self._close("DONE")
        self.assertEqual(len(self.client.get(self.url).context["outstanding"]), 0)

    def test_a_nonsense_status_changes_nothing(self):
        self._close("SOMETHING_ELSE")
        self.request_row.refresh_from_db()
        self.assertEqual(self.request_row.status, CallbackStatus.NEW)

    def test_a_get_changes_nothing(self):
        self.client.get(reverse("reception_close_callback", args=[self.request_row.pk]))
        self.request_row.refresh_from_db()
        self.assertEqual(self.request_row.status, CallbackStatus.NEW)

    def test_the_bookings_bar_says_somebody_is_waiting(self):
        self.assertContains(
            self.client.get(reverse("reception_bookings")), "Callbacks (1)"
        )

    def test_it_says_nothing_when_nobody_is_waiting(self):
        # A permanently visible zero is a link nobody clicks, and then the day
        # it matters it looks exactly as it did yesterday.
        self.request_row.close(CallbackStatus.DONE)
        self.assertNotContains(
            self.client.get(reverse("reception_bookings")), "Callbacks ("
        )

    def test_a_doctor_cannot_see_other_peoples_callback_requests(self):
        self.client.force_login(make_doctor())
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_a_stranger_cannot_see_them(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


# ── Services and FAQs ────────────────────────────────────────────────────────

class TestTheServices(TestCase):
    def setUp(self):
        self.url = reverse("website_home")

    def test_both_practices_are_in_the_page(self):
        # Both panels render, so somebody without JavaScript sees every
        # condition the clinic treats rather than half of them.
        response = self.client.get(self.url)
        self.assertContains(response, "Endocrine oncology")          # adult
        self.assertContains(response, "Growth and short stature")    # paediatric

    def test_the_tabs_are_real_tabs(self):
        body = self.client.get(self.url).content.decode()
        self.assertIn('role="tablist"', body)
        self.assertIn('role="tabpanel"', body)

    def test_each_panel_is_named_for_when_there_are_no_tabs(self):
        # Without JavaScript the tabs are hidden and both lists show. Unlabelled
        # they would run together with nothing saying which is which.
        response = self.client.get(self.url)
        self.assertContains(response, "Adult endocrinology")
        self.assertContains(response, "Paediatric endocrinology")


class TestTheFaqs(TestCase):
    def test_the_answers_are_in_the_markup_not_only_after_a_click(self):
        # <details>, so an answer is in the page whether or not it is open —
        # which is what lets find-in-page and a search engine read it.
        response = self.client.get(reverse("website_home"))
        self.assertContains(response, "Do I need a referral")
        self.assertContains(response, "No referral is required")

    def test_they_open_without_javascript(self):
        self.assertContains(self.client.get(reverse("website_home")), "<details")
