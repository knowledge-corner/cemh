"""
The 404/403/400/500 pages a visitor sees instead of Django's bare defaults.

DEBUG is off in production, so these are what a real crash or a bad link
actually shows a patient or a receptionist — not a stack trace, and not the
plain "Server Error (500)" heading Django falls back to with no template.
"""

from django.template import loader
from django.test import TestCase, override_settings

from config.views import server_error


@override_settings(DEBUG=False)
class TestTheNotFoundPage(TestCase):
    def test_an_unknown_url_shows_the_styled_page_not_a_default_one(self):
        response = self.client.get("/this-page-does-not-exist/")
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "404.html")

    def test_it_carries_the_clinics_own_name(self):
        response = self.client.get("/this-page-does-not-exist/")
        self.assertContains(response, "Centre for Endocrine", status_code=404)


class TestTheServerErrorPage(TestCase):
    """
    Exercised directly, not through a real 500 — Django's test client re-raises
    a view's exception rather than rendering the handler, by design, so this is
    the only way to prove the handler itself renders without one.
    """

    def test_it_renders_without_needing_a_request_context(self):
        # request=None on purpose — the point of this view is that it must not
        # depend on one, since the request itself might be what is unusable.
        response = server_error(request=None)
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"Something went wrong", response.content)

    def test_it_shows_the_clinics_phone_number(self):
        response = server_error(request=None)
        self.assertIn(b"7045032951", response.content)

    def test_the_template_renders_with_no_context_at_all(self):
        # The same guarantee Django's own default server_error view makes:
        # this cannot be the thing that fails while handling a failure.
        template = loader.get_template("500.html")
        html = template.render()
        self.assertIn("Something went wrong", html)
