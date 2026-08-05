"""
What the public page tells search engines and link previews.

Structured data is a set of claims made in public about a real medical
practice, so most of these tests are about honesty rather than completeness:
that nothing is asserted the clinic has not actually supplied, and that the
markup agrees with what a visitor can see on the page.

The one that matters most is TestItInventsNothing. Fabricated review markup is
a manual-action offence at Google and a lie to patients, and it is exactly the
sort of thing that gets added to a clinic site by somebody trying to be helpful.
"""

import json
import re

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import DoctorProfile, Specialisation
from website import seo
from website.views_seo import PRIVATE_PREFIXES

from .factories import make_doctor

LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


class SeoTestCase(TestCase):
    def setUp(self):
        self.url = reverse("website_home")
        self.doctor = make_doctor(
            username="dr-vrushali", email="drvrushali@example.in",
            first_name="Vrushali", last_name="Kulkarni", phone="7620351240",
        )
        DoctorProfile.objects.create(
            user=self.doctor,
            specialisation=Specialisation.objects.get(name="Paediatric Endocrinology"),
            qualification="MBBS, MD (Pediatrics)",
            bio="She treats children.\n\nShe trained in Manchester.",
            activated_at=timezone.now(),
        )

    def body(self):
        return self.client.get(self.url).content.decode()

    def graph(self):
        blocks = LD_JSON.findall(self.body())
        self.assertEqual(len(blocks), 1, "expected exactly one JSON-LD block")
        return json.loads(blocks[0])["@graph"]

    def node(self, kind):
        found = [n for n in self.graph() if n["@type"] == kind]
        self.assertTrue(found, f"no {kind} in the graph")
        return found[0]


# ── The head ─────────────────────────────────────────────────────────────────

class TestTheHead(SeoTestCase):
    def test_the_title_names_the_clinic_and_the_city(self):
        # A clinic is found by what it does and where it is.
        title = re.search(r"<title>(.*?)</title>", self.body(), re.S).group(1)
        self.assertIn("Centre for Endocrine", title)
        self.assertIn("Mumbai", title)

    def test_there_is_a_description(self):
        self.assertContains(self.client.get(self.url), 'name="description"')

    def test_the_description_is_a_usable_length(self):
        # Google truncates a snippet at roughly 160 characters. Much shorter
        # wastes the space; much longer is cut mid-sentence.
        content = re.search(
            r'<meta name="description" content="(.*?)">', self.body(), re.S
        ).group(1)
        self.assertGreater(len(content), 70)
        self.assertLess(len(content), 200)

    def test_it_is_the_one_page_that_may_be_indexed(self):
        self.assertContains(self.client.get(self.url), "index, follow")

    def test_it_asks_for_a_full_snippet_and_a_large_image(self):
        body = self.body()
        self.assertIn("max-snippet:-1", body)
        self.assertIn("max-image-preview:large", body)

    def test_there_is_one_canonical_address(self):
        # Without it the page reached with a ?utm_source, or over http, or with
        # a trailing slash, competes with itself.
        self.assertContains(self.client.get(self.url), 'rel="canonical"')

    def test_the_language_says_indian_english(self):
        self.assertContains(self.client.get(self.url), 'lang="en-IN"')

    def test_the_favicon_is_declared(self):
        # It was a 404 on every page load before this.
        body = self.body()
        self.assertIn("favicon.svg", body)
        self.assertIn("favicon.ico", body)
        self.assertIn("apple-touch-icon", body)


class TestTheSharingPreview(SeoTestCase):
    """
    This clinic asks patients to book over WhatsApp. A message carrying a bare
    link with no preview looks like spam, which is the opposite of what a clinic
    sending its own address wants.
    """

    def test_the_open_graph_basics_are_all_present(self):
        body = self.body()
        for tag in ("og:type", "og:title", "og:description", "og:url",
                    "og:image", "og:site_name", "og:locale"):
            with self.subTest(tag=tag):
                self.assertIn(f'property="{tag}"', body)

    def test_the_image_has_its_size_declared(self):
        # WhatsApp and Facebook will not render a preview large until they know
        # the dimensions, and they will not always fetch the image to find out.
        body = self.body()
        self.assertIn('property="og:image:width" content="1200"', body)
        self.assertIn('property="og:image:height" content="630"', body)

    def test_the_image_is_an_absolute_url(self):
        # A crawler has no page to resolve a relative path against.
        image = re.search(
            r'<meta property="og:image" content="(.*?)"', self.body()
        ).group(1)
        self.assertTrue(image.startswith("http"), image)

    def test_the_image_actually_exists(self):
        # An og:image pointing at a 404 is worse than none: the preview renders
        # as a broken frame.
        from django.contrib.staticfiles import finders
        self.assertIsNotNone(finders.find("og-image.png"))

    def test_twitter_gets_a_large_card(self):
        self.assertContains(self.client.get(self.url), 'name="twitter:card"')
        self.assertContains(self.client.get(self.url), "summary_large_image")


# ── Structured data ──────────────────────────────────────────────────────────

class TestTheStructuredData(SeoTestCase):
    def test_it_is_valid_json(self):
        self.graph()   # would raise otherwise

    def test_it_is_rendered_by_the_server_not_assembled_by_javascript(self):
        # Google usually renders a page before reading it; Bing and every social
        # crawler take the source as it arrives. Markup they never see does
        # nothing at all.
        body = self.body()
        self.assertIn('<script type="application/ld+json">', body)
        self.assertNotIn("application/ld+json\";", body)

    def test_the_clinic_is_described(self):
        clinic = self.node("MedicalClinic")
        self.assertEqual(clinic["name"], "Centre for Endocrine & Metabolic Health")
        self.assertEqual(clinic["telephone"], "7045032951")

    def test_the_address_is_broken_into_its_parts(self):
        address = self.node("MedicalClinic")["address"]
        self.assertEqual(address["addressLocality"], "Mumbai")
        self.assertEqual(address["postalCode"], "400092")
        self.assertEqual(address["addressCountry"], "IN")

    def test_the_opening_hours_match_what_the_page_prints(self):
        # Markup that contradicts the visible page is markup that gets ignored,
        # and it misleads anybody who reads it first.
        hours = self.node("MedicalClinic")["openingHoursSpecification"][0]
        self.assertEqual(hours["opens"], "10:00")
        self.assertEqual(hours["closes"], "18:00")

    def test_the_doctors_are_named_as_physicians(self):
        names = [e["name"] for e in self.node("MedicalClinic")["employee"]]
        self.assertIn("Dr. Vrushali Kulkarni", names)

    def test_a_doctor_who_has_left_is_not_claimed(self):
        self.doctor.is_active = False
        self.doctor.save()
        self.assertEqual(self.node("MedicalClinic")["employee"], [])

    def test_each_doctor_points_at_a_place_on_the_page(self):
        # An @id that resolves to nothing is a reference to nowhere.
        physician = self.node("MedicalClinic")["employee"][0]
        anchor = physician["@id"].split("#")[-1]
        self.assertIn(f'id="{anchor}"', self.body())

    def test_the_questions_are_marked_up_as_questions(self):
        faq = self.node("FAQPage")
        self.assertEqual(len(faq["mainEntity"]), 5)
        self.assertTrue(faq["mainEntity"][0]["acceptedAnswer"]["text"])

    def test_the_nodes_are_joined_up_rather_than_four_loose_claims(self):
        page = self.node("WebPage")
        clinic = self.node("MedicalClinic")
        self.assertEqual(page["about"]["@id"], clinic["@id"])

    def test_a_bio_cannot_break_out_of_the_script_tag(self):
        # The one way a doctor's own text could damage the page.
        profile = self.doctor.doctor_profile
        profile.bio = "Nasty </script><script>alert(1)</script> and an & ampersand."
        profile.save()

        body = self.body()
        self.assertEqual(len(LD_JSON.findall(body)), 1)
        self.assertNotIn("<script>alert(1)</script>", body)
        # And the text still survives as data.
        self.assertIn(
            "alert(1)", self.node("MedicalClinic")["employee"][0]["description"]
        )


class TestItInventsNothing(SeoTestCase):
    """
    The line this must not cross.

    Every one of these would improve how the listing looks and every one would
    be a claim nobody made. Ratings and reviews in particular are a manual-action
    offence at Google — and, more to the point, a lie told to a patient choosing
    a doctor.
    """

    def test_no_ratings_are_claimed(self):
        self.assertNotIn("aggregateRating", json.dumps(self.graph()))

    def test_no_reviews_are_claimed(self):
        self.assertNotIn('"review"', json.dumps(self.graph()))

    def test_no_award_or_accreditation_is_claimed(self):
        blob = json.dumps(self.graph())
        for invented in ("award", "hasCertification", "accreditation"):
            with self.subTest(claim=invented):
                self.assertNotIn(invented, blob)

    def test_no_map_pin_is_guessed(self):
        # Nobody has surveyed this building. A guessed coordinate is a pin
        # patients then drive to.
        self.assertNotIn("geo", self.node("MedicalClinic"))

    @override_settings()
    def test_a_configured_map_pin_is_used(self):
        # The other half: when somebody supplies real coordinates they appear.
        from django.conf import settings
        settings.CLINIC.CLINIC_LATITUDE = "19.2307"
        settings.CLINIC.CLINIC_LONGITUDE = "72.8567"
        try:
            geo = self.node("MedicalClinic")["geo"]
            self.assertEqual(geo["latitude"], 19.2307)
        finally:
            settings.CLINIC.CLINIC_LATITUDE = ""
            settings.CLINIC.CLINIC_LONGITUDE = ""

    def test_a_mistyped_coordinate_does_not_take_the_page_down(self):
        from django.conf import settings
        settings.CLINIC.CLINIC_LATITUDE = "nineteen point two"
        settings.CLINIC.CLINIC_LONGITUDE = "72.8567"
        try:
            self.assertEqual(self.client.get(self.url).status_code, 200)
            self.assertNotIn("geo", self.node("MedicalClinic"))
        finally:
            settings.CLINIC.CLINIC_LATITUDE = ""
            settings.CLINIC.CLINIC_LONGITUDE = ""

    def test_no_social_profiles_are_claimed_by_default(self):
        # Listing a page the clinic does not control attaches somebody else's
        # reputation to this one.
        self.assertNotIn("sameAs", self.node("MedicalClinic"))


# ── robots.txt and sitemap.xml ───────────────────────────────────────────────

class TestRobots(TestCase):
    def setUp(self):
        self.body = self.client.get("/robots.txt").content.decode()

    def test_it_is_served_from_the_root(self):
        # A crawler looks for /robots.txt and nowhere else.
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")

    def test_the_public_page_is_not_blocked(self):
        # The failure that would silently remove the clinic from search.
        self.assertNotIn("\nDisallow: /\n", self.body)
        self.assertNotIn("Disallow: /$", self.body)

    def test_every_staff_area_is_kept_out_of_search_results(self):
        for prefix in ("/reception/", "/doctor/", "/calendar/", "/print/"):
            with self.subTest(prefix=prefix):
                self.assertIn(f"Disallow: {prefix}", self.body)

    def test_the_real_admin_address_is_disallowed(self):
        from django.conf import settings
        self.assertIn(settings.ADMIN_URL.strip("/"), self.body)

    def test_the_stylesheet_and_images_stay_crawlable(self):
        # Google renders a page before judging it. One whose CSS it was refused
        # looks broken to the thing deciding how it ranks.
        self.assertIn("Allow: /static/", self.body)

    def test_it_points_at_the_sitemap(self):
        self.assertIn("Sitemap:", self.body)
        self.assertIn("/sitemap.xml", self.body)

    def test_the_prefix_list_covers_the_urls_that_exist(self):
        # Guards against a whole area being added later and quietly indexed.
        for name in ("reception_home", "doctor_home", "reception_calendar"):
            path = reverse(name)
            with self.subTest(url=path):
                self.assertTrue(
                    any(path.startswith(p) for p in PRIVATE_PREFIXES),
                    f"{path} is not covered by robots.txt",
                )


class TestSitemap(TestCase):
    def test_it_is_served_as_xml(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertIn("xml", response["Content-Type"])

    def test_it_lists_the_public_page(self):
        self.assertContains(self.client.get("/sitemap.xml"), "<loc>")

    def test_it_lists_nothing_behind_the_login(self):
        body = self.client.get("/sitemap.xml").content.decode()
        for prefix in ("/reception/", "/doctor/", "/calendar/"):
            with self.subTest(prefix=prefix):
                self.assertNotIn(prefix, body)

    def test_the_url_is_absolute(self):
        location = re.search(
            r"<loc>(.*?)</loc>", self.client.get("/sitemap.xml").content.decode()
        ).group(1)
        self.assertTrue(location.startswith("http"), location)


class TestTheConfiguredSiteAddress(TestCase):
    """
    Behind a proxy the Host header is whatever the proxy forwarded, so a
    canonical URL derived from it can point somewhere that is not the site.
    """

    def test_the_configured_address_wins_over_the_request(self):
        from django.conf import settings
        settings.CLINIC.SITE_URL = "https://cemhcare.com"
        try:
            self.assertContains(
                self.client.get(reverse("website_home")),
                'rel="canonical" href="https://cemhcare.com/"',
            )
        finally:
            settings.CLINIC.SITE_URL = ""

    def test_a_trailing_slash_in_configuration_does_not_double_up(self):
        from django.conf import settings
        settings.CLINIC.SITE_URL = "https://cemhcare.com/"
        try:
            self.assertNotContains(self.client.get("/sitemap.xml"), "com//")
        finally:
            settings.CLINIC.SITE_URL = ""

    def test_it_falls_back_to_the_request_when_unset(self):
        self.assertContains(self.client.get(reverse("website_home")), "http://testserver/")


class TestTheBrandImagesCanBeRegenerated(TestCase):
    def test_the_command_runs_and_writes_the_files(self):
        # The clinic's name and colours are configuration; the sharing card has
        # to be re-makeable when they change, or it silently shows the old ones.
        from io import StringIO
        from pathlib import Path

        from django.conf import settings
        from django.core.management import call_command

        call_command("make_brand_images", stdout=StringIO())
        static_dir = Path(settings.BASE_DIR) / "static"
        for name in ("og-image.png", "favicon.svg", "favicon.ico",
                     "apple-touch-icon.png"):
            with self.subTest(file=name):
                self.assertTrue((static_dir / name).is_file())


class TestEverythingElseStaysOutOfSearch(TestCase):
    """
    The other half of the job, and the half that matters.

    robots.txt is a request, not a boundary. The guarantee is that every page
    behind the login carries `noindex` and that an anonymous request never gets
    one in the first place.
    """

    def setUp(self):
        from .factories import make_receptionist
        self.receptionist = make_receptionist()

    def test_a_staff_page_tells_search_engines_to_stay_away(self):
        self.client.force_login(self.receptionist)
        for name in ("reception_home", "reception_bookings", "reception_calendar"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertContains(response, "noindex")

    def test_a_staff_page_is_not_archived_either(self):
        # A cached copy in a search index outlives the page itself.
        self.client.force_login(self.receptionist)
        self.assertContains(self.client.get(reverse("reception_home")), "noarchive")

    def test_only_the_public_page_invites_indexing(self):
        self.client.force_login(self.receptionist)
        self.assertNotContains(
            self.client.get(reverse("reception_home")), "index, follow"
        )

    def test_a_crawler_gets_nothing_but_the_login(self):
        # Nothing to index because nothing is served: the real protection.
        for name in ("reception_home", "doctor_home", "reception_calendar"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])
