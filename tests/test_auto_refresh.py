"""
The calendar and bookings screens reload themselves periodically (KAN request:
left open on a desk all day, staff expect what they show to be current).
"""

from django.test import TestCase
from django.urls import reverse

from .factories import make_receptionist


class TestTheScreensReloadThemselves(TestCase):
    def setUp(self):
        self.client.force_login(make_receptionist())

    def test_bookings_carries_the_refresh_script(self):
        response = self.client.get(reverse("reception_bookings"))
        self.assertContains(response, "auto_refresh")
        self.assertContains(response, 'data-refresh-ms="120000"')

    def test_the_calendar_carries_the_refresh_script(self):
        response = self.client.get(reverse("reception_calendar"))
        self.assertContains(response, "auto_refresh")
        self.assertContains(response, 'data-refresh-ms="120000"')
