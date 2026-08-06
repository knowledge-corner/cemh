"""
KAN-48 and KAN-49 — closing a clinic day, and being made to.

The two tickets are one mechanism. KAN-48 wants the day sheet emailed; KAN-49
wants the next morning held up until the day before was closed. Both need the
same fact recorded — *was that day signed off* — which cannot be derived from
the visits: a day where nothing needed billing and a day nobody closed look
identical in the visit table, and only one of them is a problem.

The rule these tests care about most is the one that is easy to get backwards.
A consultation nobody billed must **not** be swept away. It is the only record
that money is owed, and sweeping it is how the money stops being looked for —
which is the thing KAN-48 exists to recover.
"""

from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from appointments import signoff
from appointments.models import DaySignOff, Visit, VisitStatus
from billing.models import Charge, Payment
from portal import services

from .factories import make_doctor, make_patient, make_receptionist, make_visit


def MID_MORNING():
    """
    A fixed time of day, well after the five-o'clock cutoff.

    is_due() consults the real clock, so a test that let it do so would fail
    for anybody running the suite between midnight and 05:00 — and would fail
    as though the feature were broken rather than as a test that told the time.
    """
    return timezone.localtime().replace(hour=10, minute=0)


def _yesterday_at(hour, minute=0):
    day = timezone.localdate() - timedelta(days=1)
    return timezone.make_aware(
        timezone.datetime.combine(day, timezone.datetime.min.time()).replace(
            hour=hour, minute=minute
        ),
        timezone.get_current_timezone(),
    )


class SignOffTestCase(TestCase):
    def setUp(self):
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)
        self.yesterday = timezone.localdate() - timedelta(days=1)

    def _visit(self, hour=10, phone=None, upto=None):
        patient = make_patient(phone=phone) if phone else make_patient()
        visit = make_visit(patient, self.doctor, start=_yesterday_at(hour))
        for status in (upto or []):
            visit.transition_to(status, by_user=self.receptionist)
        return visit

    def _consulted(self, hour=10, phone=None, fee="800"):
        visit = self._visit(hour, phone, upto=[
            VisitStatus.CONFIRMED, VisitStatus.ARRIVED,
            VisitStatus.IN_CABIN, VisitStatus.CONSULTED,
        ])
        Charge.objects.create(
            visit=visit, patient=visit.patient,
            consultation_fee=Decimal(fee), set_by=self.doctor,
        )
        return visit


# ── KAN-48: the day sheet ────────────────────────────────────────────────────

@override_settings()
class TestTheDaySheet(SignOffTestCase):

    def setUp(self):
        super().setUp()
        signoff.settings.CLINIC.SIGN_OFF_EMAILS = "owner@example.in"
        self.addCleanup(setattr, signoff.settings.CLINIC, "SIGN_OFF_EMAILS", "")

    def test_signing_off_sends_one_email(self):
        self._visit(upto=[VisitStatus.CONFIRMED])
        signoff.sign_off(self.yesterday, by_user=self.receptionist)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["owner@example.in"])

    def test_it_carries_the_three_sheets(self):
        # The ticket names them: billed, cancelled, no-show.
        self._visit(upto=[VisitStatus.CONFIRMED])
        signoff.sign_off(self.yesterday, by_user=self.receptionist)
        names = [name for name, _content, _type in mail.outbox[0].attachments]
        self.assertEqual(len(names), 3)
        for sheet in ("billed", "cancelled", "no_show"):
            self.assertTrue(
                any(sheet in name for name in names), f"{sheet} sheet missing",
            )

    def test_a_settled_visit_lands_on_the_billed_sheet(self):
        visit = self._consulted()
        charge = visit.charge
        Payment.objects.create(
            charge=charge, amount=Decimal("800"), received_by=self.receptionist,
        )
        visit.transition_to(VisitStatus.BILLED, by_user=self.receptionist)

        signoff.sign_off(self.yesterday, by_user=self.receptionist)
        billed = dict(
            (name, content) for name, content, _t in mail.outbox[0].attachments
        )
        sheet = next(c for n, c in billed.items() if "billed" in n)
        self.assertIn(visit.patient.patient_id, sheet)

    def test_a_no_show_lands_on_the_no_show_sheet(self):
        visit = self._visit(upto=[VisitStatus.CONFIRMED])
        signoff.sign_off(self.yesterday, by_user=self.receptionist)

        sheets = {n: c for n, c, _t in mail.outbox[0].attachments}
        no_show = next(c for n, c in sheets.items() if "no_show" in n)
        self.assertIn(visit.patient.patient_id, no_show)

    def test_the_totals_are_recorded_on_the_sign_off(self):
        self._visit(upto=[VisitStatus.CONFIRMED])
        record, _ = signoff.sign_off(self.yesterday, by_user=self.receptionist)
        self.assertEqual(record.no_show_count, 1)
        self.assertEqual(record.sent_by, self.receptionist)

    def test_the_money_taken_is_totalled(self):
        visit = self._consulted()
        Payment.objects.create(
            charge=visit.charge, amount=Decimal("800"), received_by=self.receptionist,
        )
        visit.transition_to(VisitStatus.BILLED, by_user=self.receptionist)

        record, _ = signoff.sign_off(self.yesterday, by_user=self.receptionist)
        self.assertEqual(record.collected, Decimal("800"))

    def test_a_day_is_not_signed_off_twice(self):
        # Two receptionists at the end of a shift both press the button. The
        # accountant receiving the day twice is worse than one of them being
        # told it is already done.
        self._visit(upto=[VisitStatus.CONFIRMED])
        first, created_first = signoff.sign_off(self.yesterday)
        second, created_second = signoff.sign_off(self.yesterday)

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(len(mail.outbox), 1)


class TestTheReportIsNotBlockingWhenMailFails(SignOffTestCase):

    def test_no_address_configured_still_closes_the_day(self):
        # A clinic that cannot open tomorrow because nobody set an email
        # address would be a worse system than one that says so and carries on.
        self._visit(upto=[VisitStatus.CONFIRMED])
        record, created = signoff.sign_off(self.yesterday)

        self.assertTrue(created)
        self.assertTrue(DaySignOff.objects.filter(date=self.yesterday).exists())
        self.assertIn("SIGN_OFF_EMAILS", record.delivery_error)

    def test_a_send_failure_is_recorded_rather_than_raised(self):
        signoff.settings.CLINIC.SIGN_OFF_EMAILS = "owner@example.in"
        self.addCleanup(setattr, signoff.settings.CLINIC, "SIGN_OFF_EMAILS", "")
        self._visit(upto=[VisitStatus.CONFIRMED])

        with self.settings(EMAIL_BACKEND="django.core.mail.backends.dummy.DummyBackend"):
            # Dummy accepts everything, so force a real failure instead.
            with self.assertLogs("appointments.signoff", level="ERROR"):
                original = signoff._send

                def explode(report, to):
                    raise OSError("mail server refused the connection")

                signoff._send = explode
                try:
                    record, created = signoff.sign_off(self.yesterday)
                finally:
                    signoff._send = original

        self.assertTrue(created)
        self.assertIn("refused", record.delivery_error)


# ── The sweep ────────────────────────────────────────────────────────────────

class TestWhatTheSweepTouches(SignOffTestCase):

    def test_a_booking_nobody_confirmed_lapses(self):
        visit = self._visit()
        signoff.sign_off(self.yesterday)
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.CANCELLED)

    def test_a_confirmed_booking_the_patient_missed_is_a_no_show(self):
        visit = self._visit(upto=[VisitStatus.CONFIRMED])
        signoff.sign_off(self.yesterday)
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.NO_SHOW)

    def test_an_unbilled_consultation_is_never_swept_away(self):
        # The rule this whole module exists to protect. Sweeping it destroys
        # the only record that money is owed.
        visit = self._consulted()
        signoff.sign_off(self.yesterday)
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.CONSULTED)

    def test_the_sign_off_reports_what_is_still_owed(self):
        self._consulted()
        report = signoff.build_report(self.yesterday)
        self.assertEqual(len(report["outstanding"]), 1)


# ── KAN-49: the next morning ─────────────────────────────────────────────────

class TestTheNextMorningIsHeldUp(SignOffTestCase):

    def test_an_unsigned_day_with_visits_is_due(self):
        self._visit()
        self.assertEqual(signoff.is_due(now=MID_MORNING()), self.yesterday)

    def test_a_signed_off_day_is_not_due(self):
        self._visit()
        signoff.sign_off(self.yesterday)
        self.assertIsNone(signoff.is_due(now=MID_MORNING()))

    def test_a_day_with_no_clinic_at_all_is_not_due(self):
        # A clinic shut on Sunday must not be nagged every Monday.
        self.assertIsNone(signoff.is_due(now=MID_MORNING()))

    def test_nothing_is_due_before_five_in_the_morning(self):
        # An evening list finishes after midnight. Nagging at 00:05 would be
        # nagging somebody who is still working.
        self._visit()
        four_am = timezone.localtime().replace(hour=4, minute=0)
        self.assertIsNone(signoff.is_due(now=four_am))

    def test_it_is_due_once_five_has_passed(self):
        self._visit()
        six_am = timezone.localtime().replace(hour=6, minute=0)
        self.assertEqual(signoff.is_due(now=six_am), self.yesterday)

    def test_the_board_says_so(self):
        self._visit()
        response = self.client.get(reverse("reception_home"))
        self.assertContains(response, "has not been signed off")

    def test_the_board_names_what_is_left_to_bill(self):
        visit = self._consulted()
        response = self.client.get(reverse("reception_home"))
        self.assertContains(response, visit.patient.patient_id)
        self.assertContains(response, "still to be billed")

    def test_marking_a_patient_arrived_is_refused(self):
        self._visit()
        today = make_visit(
            make_patient(phone="9820099999"), self.doctor,
            start=timezone.now() + timedelta(hours=2),
        )
        self.client.post(
            reverse("reception_move_visit", args=[today.pk, "ARRIVED"]),
            headers={"HX-Request": "true"},
        )
        today.refresh_from_db()
        self.assertEqual(today.status, VisitStatus.BOOKED)

    def test_the_refusal_names_the_day(self):
        self._visit()
        today = make_visit(
            make_patient(phone="9820099999"), self.doctor,
            start=timezone.now() + timedelta(hours=2),
        )
        response = self.client.post(
            reverse("reception_move_visit", args=[today.pk, "ARRIVED"]),
            headers={"HX-Request": "true"},
        )
        said = " ".join(str(m) for m in response.wsgi_request._messages)
        self.assertIn(f"{self.yesterday:%d %b}", said)

    def test_cancelling_still_works_while_blocked(self):
        # Only arrivals are held. A patient ringing to cancel must not be
        # refused because of yesterday's paperwork.
        self._visit()
        today = make_visit(
            make_patient(phone="9820099999"), self.doctor,
            start=timezone.now() + timedelta(hours=2),
        )
        self.client.post(
            reverse("reception_move_visit", args=[today.pk, "CANCELLED"]),
            headers={"HX-Request": "true"},
        )
        today.refresh_from_db()
        self.assertEqual(today.status, VisitStatus.CANCELLED)

    def test_arrivals_resume_once_the_day_is_signed_off(self):
        self._visit()
        today = make_visit(
            make_patient(phone="9820099999"), self.doctor,
            start=timezone.now() + timedelta(hours=2),
        )
        self.client.post(reverse("reception_close_day"),
                         {"date": self.yesterday.isoformat()})
        self.client.post(
            reverse("reception_move_visit", args=[today.pk, "ARRIVED"]),
            headers={"HX-Request": "true"},
        )
        today.refresh_from_db()
        self.assertEqual(today.status, VisitStatus.ARRIVED)


class TestSigningOffFromTheBoard(SignOffTestCase):

    def test_an_unbilled_consultation_blocks_the_sign_off(self):
        # KAN-49: the pending ones must be cleared first.
        visit = self._consulted()
        response = self.client.post(
            reverse("reception_close_day"),
            {"date": self.yesterday.isoformat()}, follow=True,
        )
        self.assertFalse(DaySignOff.objects.exists())
        self.assertContains(response, "still")
        self.assertContains(response, visit.patient.patient_id)

    def test_billing_it_lets_the_day_close(self):
        visit = self._consulted()
        self.client.post(
            reverse("reception_generate_receipt", args=[visit.pk]),
            {"amount": "800", "method": "CASH", "reference": "", "notes": ""},
        )
        self.client.post(reverse("reception_close_day"),
                         {"date": self.yesterday.isoformat()})
        self.assertTrue(DaySignOff.objects.filter(date=self.yesterday).exists())

    def test_today_cannot_be_signed_off(self):
        # Sweeping today would close patients who are still in the building.
        self.client.post(reverse("reception_close_day"),
                         {"date": timezone.localdate().isoformat()})
        self.assertFalse(DaySignOff.objects.exists())

    def test_a_doctor_may_not_sign_the_day_off(self):
        self.client.force_login(self.doctor)
        self.assertEqual(
            self.client.post(reverse("reception_close_day")).status_code, 403
        )


# ── KAN-49: the doctor's side ────────────────────────────────────────────────

class TestTheSendButtonWaitsForTheCabin(TestCase):

    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.first = make_visit(
            make_patient(), self.doctor, start=timezone.now() + timedelta(hours=1),
        )
        self.second = make_visit(
            make_patient(phone="9820088888"), self.doctor,
            start=timezone.now() + timedelta(hours=3),
        )
        for visit in (self.first, self.second):
            visit.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)

    def test_nothing_is_blocked_while_the_cabin_is_empty(self):
        queue = services.todays_queue(self.doctor)
        self.assertTrue(all(v.send_blocked_by is None for v in queue))

    def test_the_others_are_blocked_once_somebody_is_in(self):
        self.first.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        queue = {v.pk: v for v in services.todays_queue(self.doctor)}
        self.assertEqual(queue[self.second.pk].send_blocked_by, self.first)

    def test_the_patient_in_the_cabin_does_not_block_themselves(self):
        self.first.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        queue = {v.pk: v for v in services.todays_queue(self.doctor)}
        self.assertIsNone(queue[self.first.pk].send_blocked_by)

    def test_the_doctors_queue_greys_the_button_rather_than_offering_it(self):
        self.first.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.client.force_login(self.doctor)
        body = self.client.get(reverse("doctor_home")).content.decode()
        self.assertIn("disabled", body)
        self.assertIn(self.first.patient.full_name, body)

    def test_the_rule_is_still_enforced_underneath(self):
        # The greying is presentation. This is the rule.
        self.first.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        self.client.force_login(self.doctor)
        self.client.post(reverse("doctor_send_for_patient", args=[self.second.pk]))
        self.second.refresh_from_db()
        self.assertEqual(self.second.status, VisitStatus.ARRIVED)
