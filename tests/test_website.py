"""
The public page.

It is the only page in the application that an anonymous visitor may see, and
the only one that should be indexed. Its whole job is to produce a telephone
call or a WhatsApp message, so those links are what matter most here.
"""

from django.test import TestCase
from django.urls import reverse

from .factories import make_doctor, make_history, make_patient, make_visit


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


class TestEverythingElseStillNeedsALogin(TestCase):
    def test_dashboard_redirects_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_reception_and_doctor_areas_are_not_public(self):
        for name in ("reception_home", "doctor_home"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, msg=name)
