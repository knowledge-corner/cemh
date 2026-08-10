"""
The standardized reception nav bar.

One shared component (``_nav.html``) on every reception screen: Today's Queue,
Add Patient, New Booking, Bookings, Calendar and Doctors, minus whichever one
of those the current page already is — a page never needs a link back to
itself.

The component is tested in isolation, rendered directly rather than through a
full page, so a page's own heading or button text (New Booking's own title is
literally "New booking", the same string as its nav label) can never produce a
false pass. Its actual presence on each real page is then checked separately,
one assertion per page, against links that page's own content cannot collide
with.
"""

from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .factories import make_receptionist

#: nav_active value -> the label that value's own page omits.
NAV_LABELS = {
    "queue": "Today's queue",
    "add_patient": "Add patient",
    "new_booking": "New booking",
    "bookings": "All bookings",
    "calendar": "Calendar",
    "doctors": "Doctors",
}


class TestTheNavBarComponent(TestCase):
    """``_nav.html`` rendered on its own, for every ``nav_active`` value."""

    def setUp(self):
        self.receptionist = make_receptionist()

    def _render(self, nav_active):
        request = RequestFactory().get("/reception/")
        request.user = self.receptionist
        return render_to_string(
            "portal/reception/_nav.html", {"nav_active": nav_active}, request=request,
        )

    def test_each_value_omits_only_its_own_link(self):
        for active, own_label in NAV_LABELS.items():
            with self.subTest(nav_active=active):
                html = self._render(active)
                self.assertNotIn(own_label, html)
                for other_active, other_label in NAV_LABELS.items():
                    if other_active == active:
                        continue
                    self.assertIn(other_label, html)

    def test_with_no_active_page_every_link_is_offered(self):
        # A page that has not adopted the bar yet — or a future one that
        # forgets to pass nav_active — gets every destination rather than one
        # silently missing.
        html = self._render(nav_active=None)
        for label in NAV_LABELS.values():
            self.assertIn(label, html)


class TestTheNavBarOnEveryPage(TestCase):
    """The bar actually reaches every screen it is meant to, real page by
    real page — the include, not just the component's own logic."""

    def setUp(self):
        self.client.force_login(make_receptionist())

    def test_todays_queue_offers_the_other_five(self):
        body = self.client.get(reverse("reception_home")).content.decode()
        for label in ("Add patient", "New booking", "All bookings", "Calendar", "Doctors"):
            self.assertIn(label, body)

    def test_add_patient_offers_the_other_five(self):
        body = self.client.get(reverse("reception_register_patient")).content.decode()
        for label in ("Today's queue", "New booking", "All bookings", "Calendar", "Doctors"):
            self.assertIn(label, body)
        self.assertNotIn("Add patient", body)

    def test_new_booking_offers_the_other_five(self):
        body = self.client.get(reverse("reception_new_booking")).content.decode()
        for label in ("Today's queue", "Add patient", "All bookings", "Calendar", "Doctors"):
            self.assertIn(label, body)

    def test_bookings_offers_the_other_five(self):
        body = self.client.get(reverse("reception_bookings")).content.decode()
        for label in ("Today's queue", "Add patient", "New booking", "Calendar", "Doctors"):
            self.assertIn(label, body)
        self.assertNotIn("All bookings", body)

    def test_calendar_offers_the_other_five(self):
        body = self.client.get(reverse("reception_calendar")).content.decode()
        for label in ("Today's queue", "Add patient", "New booking", "All bookings", "Doctors"):
            self.assertIn(label, body)

    def test_doctors_offers_the_other_five(self):
        body = self.client.get(reverse("reception_doctors")).content.decode()
        for label in ("Today's queue", "Add patient", "New booking", "All bookings", "Calendar"):
            self.assertIn(label, body)

    def test_a_doctor_sees_no_reception_nav_anywhere(self):
        # The bar is reception's own tool. A doctor reading the board (the
        # only reception screen they can reach at all) sees none of it.
        from .factories import make_doctor

        self.client.force_login(make_doctor())
        body = self.client.get(reverse("reception_home")).content.decode()
        self.assertNotIn('class="toolbar"', body)
