"""
The doctor's own Bookings screen — upcoming and completed appointments, in
one place instead of only ever seeing today. Mirrors the "still to come" /
"nothing left owing" rules reception's own Bookings screen uses, scoped to
one doctor.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import VisitStatus
from billing.models import Charge, Payment

from .factories import make_doctor, make_patient, make_receptionist, make_visit


def _seen_and_billed(
    patient, doctor, receptionist, *,
    paid=Decimal("800.00"), fee=Decimal("800.00"),
    start=None, **visit_kwargs,
):
    """A visit taken all the way to BILLED, with a charge and a payment against it."""
    visit = make_visit(
        patient, doctor, start=start or timezone.now() - timedelta(days=1), **visit_kwargs
    )
    for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.IN_CABIN, VisitStatus.CONSULTED):
        visit.transition_to(status, by_user=doctor)
    charge = Charge.objects.create(
        visit=visit, patient=visit.patient, consultation_fee=fee, set_by=doctor,
    )
    if paid:
        Payment.objects.create(charge=charge, amount=paid, received_by=receptionist)
    visit.transition_to(VisitStatus.BILLED, by_user=receptionist)
    return visit


class TestDoctorBookings(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.other_doctor = make_doctor(username="drother", email="other@example.in", phone="9820000009")
        self.receptionist = make_receptionist()
        self.client.force_login(self.doctor)

    def url(self, tab=None):
        base = reverse("doctor_bookings")
        return f"{base}?tab={tab}" if tab else base

    def test_a_receptionist_is_refused(self):
        self.client.force_login(self.receptionist)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_defaults_to_the_completed_tab(self):
        response = self.client.get(self.url())
        self.assertContains(response, 'href="?tab=completed"')
        self.assertContains(response, 'aria-selected="true"')

    def test_bookings_is_offered_in_the_nav(self):
        response = self.client.get(reverse("doctor_home"))
        self.assertContains(response, reverse("doctor_bookings"))

    def test_a_booking_today_appears_under_today(self):
        patient = make_patient()
        make_visit(patient, self.doctor, start=timezone.now() + timedelta(hours=2))
        response = self.client.get(self.url("upcoming"))
        content = response.content.decode()
        self.assertIn(patient.full_name, content)
        # Falls in the "Today" group, which carries the row; "Ahead" is
        # empty and shows its own hint instead of a table.
        self.assertIn("Nothing booked beyond today.", content)

    def test_a_booking_tomorrow_appears_under_ahead(self):
        patient = make_patient()
        make_visit(patient, self.doctor, start=timezone.now() + timedelta(days=1))
        response = self.client.get(self.url("upcoming"))
        content = response.content.decode()
        self.assertIn(patient.full_name, content)
        self.assertIn("Nothing else booked for today.", content)

    def test_upcoming_date_filter_excludes_a_booking_outside_the_range(self):
        in_range = make_patient(first_name="InRange")
        out_of_range = make_patient(first_name="OutOfRange", phone="9820011111")
        make_visit(in_range, self.doctor, start=timezone.now() + timedelta(days=2))
        make_visit(out_of_range, self.doctor, start=timezone.now() + timedelta(days=10))

        today = timezone.localdate()
        response = self.client.get(
            self.url("upcoming")
            + f"&from={today.isoformat()}&to={(today + timedelta(days=3)).isoformat()}"
        )
        content = response.content.decode()
        self.assertIn(in_range.full_name, content)
        self.assertNotIn(out_of_range.full_name, content)

    def test_another_doctors_upcoming_booking_does_not_appear(self):
        patient = make_patient()
        make_visit(patient, self.other_doctor, start=timezone.now() + timedelta(hours=2))
        response = self.client.get(self.url("upcoming"))
        self.assertNotContains(response, patient.full_name)

    def test_a_fully_paid_visit_appears_on_completed(self):
        patient = make_patient()
        _seen_and_billed(patient, self.doctor, self.receptionist)
        response = self.client.get(self.url("completed"))
        self.assertContains(response, patient.full_name)
        self.assertContains(response, "Appointment")

    def test_a_partially_paid_visit_does_not_appear_on_completed(self):
        # Mirrors reception's own rule: something is still owed, so it is not
        # finished yet, whatever stage it is sitting at.
        patient = make_patient()
        _seen_and_billed(patient, self.doctor, self.receptionist, paid=Decimal("300.00"))
        response = self.client.get(self.url("completed"))
        self.assertNotContains(response, patient.full_name)

    def test_completed_date_filter_excludes_a_visit_outside_the_range(self):
        recent = make_patient(first_name="Recent")
        old = make_patient(first_name="LongAgo", phone="9820022222")
        _seen_and_billed(recent, self.doctor, self.receptionist, start=timezone.now() - timedelta(days=2))
        _seen_and_billed(old, self.doctor, self.receptionist, start=timezone.now() - timedelta(days=60))

        today = timezone.localdate()
        response = self.client.get(
            self.url("completed") + f"&from={(today - timedelta(days=5)).isoformat()}&to={today.isoformat()}"
        )
        content = response.content.decode()
        self.assertIn(recent.full_name, content)
        self.assertNotIn(old.full_name, content)

    def test_a_direct_consultation_appears_on_completed_with_its_tag(self):
        patient = make_patient()
        _seen_and_billed(patient, self.doctor, self.receptionist, is_direct=True)
        response = self.client.get(self.url("completed"))
        self.assertContains(response, patient.full_name)
        self.assertContains(response, "Direct")

    def test_a_walk_in_appears_on_completed_with_its_tag(self):
        patient = make_patient()
        _seen_and_billed(patient, self.doctor, self.receptionist, is_walk_in=True)
        response = self.client.get(self.url("completed"))
        self.assertContains(response, patient.full_name)
        self.assertContains(response, "Walk-in")

    def test_another_doctors_completed_visit_does_not_appear(self):
        patient = make_patient()
        _seen_and_billed(patient, self.other_doctor, self.receptionist)
        response = self.client.get(self.url("completed"))
        self.assertNotContains(response, patient.full_name)

    def test_reprinting_a_completed_row(self):
        patient = make_patient()
        visit = _seen_and_billed(patient, self.doctor, self.receptionist)
        response = self.client.get(self.url("completed"))
        self.assertContains(
            response, reverse("reception_settled_visit", args=[visit.pk])
        )

    def test_the_patient_or_uhid_filter_narrows_completed(self):
        found = make_patient(phone="9820055501")
        other = make_patient(first_name="Other", phone="9820055502")
        now = timezone.now()
        _seen_and_billed(found, self.doctor, self.receptionist, start=now - timedelta(days=1))
        _seen_and_billed(other, self.doctor, self.receptionist, start=now - timedelta(days=2))
        response = self.client.get(
            f"{self.url('completed')}&patient_id={found.patient_id}"
        )
        self.assertContains(response, found.full_name)
        self.assertNotContains(response, other.full_name)

    def test_the_appointment_type_filter_narrows_completed(self):
        walk_in = make_patient(phone="9820055503")
        appointment = make_patient(first_name="Booked", phone="9820055504")
        now = timezone.now()
        _seen_and_billed(
            walk_in, self.doctor, self.receptionist, is_walk_in=True,
            start=now - timedelta(days=1),
        )
        _seen_and_billed(
            appointment, self.doctor, self.receptionist,
            start=now - timedelta(days=2),
        )
        response = self.client.get(f"{self.url('completed')}&visit_type=WALK_IN")
        self.assertContains(response, walk_in.full_name)
        self.assertNotContains(response, appointment.full_name)

    def test_the_collected_total_only_counts_what_is_shown(self):
        patient = make_patient()
        _seen_and_billed(
            patient, self.doctor, self.receptionist,
            fee=Decimal("650.00"), paid=Decimal("650.00"),
        )
        response = self.client.get(self.url("completed"))
        self.assertContains(response, "650.00 collected")

    def test_the_csv_export_matches_the_screen(self):
        patient = make_patient()
        _seen_and_billed(patient, self.doctor, self.receptionist)
        other_patient = make_patient(first_name="NotMine", phone="9820055505")
        _seen_and_billed(
            other_patient, self.other_doctor, self.receptionist,
            start=timezone.now() - timedelta(days=2),
        )

        response = self.client.get(reverse("doctor_export_bookings"))
        body = response.content.decode()
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn(patient.patient_id, body)
        self.assertNotIn(other_patient.patient_id, body)
        # No Doctor column — every row is already this doctor's own.
        self.assertNotIn("Doctor", body.splitlines()[0])
