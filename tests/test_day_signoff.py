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

from .factories import (
    make_doctor, make_patient, make_receptionist, make_visit, today_at,
)


#: What config/clinic.py ships with. Read rather than repeated, so a clinic
#: changing its own address does not have to change a test to match.
DEFAULT_SIGN_OFF = signoff.settings.CLINIC.SIGN_OFF_EMAILS


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
    """
    Sign-off is switched off for this clinic, so every test of it turns it on.

    Deliberately not left on for the suite. The switch is the thing most likely
    to be got wrong — honoured by the alert and forgotten by the rule
    underneath, so a "disabled" feature still blocks somebody's morning — and a
    suite that ran with it permanently on would never notice.
    """

    def setUp(self):
        signoff.settings.CLINIC.DAY_SIGN_OFF_ENABLED = True
        self.addCleanup(
            setattr, signoff.settings.CLINIC, "DAY_SIGN_OFF_ENABLED", False,
        )
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
        signoff.settings.CLINIC.SIGN_OFF_EMAILS = ""
        signoff.settings.CLINIC.CLINIC_EMAIL = ""
        self.addCleanup(
            setattr, signoff.settings.CLINIC, "SIGN_OFF_EMAILS", DEFAULT_SIGN_OFF,
        )
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

    def test_the_board_counts_what_is_left_and_links_to_the_list(self):
        # Superseded. The alert used to list the patients and put a receipt
        # button on each, inside a warning strip on a board that reloads every
        # thirty seconds. The clinic asked for the list to live under All
        # bookings instead, so the alert now says how many and sends her there.
        self._consulted()
        response = self.client.get(reverse("reception_home"))
        self.assertContains(response, "still to close")
        self.assertContains(response, "Show all unclosed appointments")
        self.assertContains(response, "tab=unclosed")

    def test_marking_a_patient_arrived_is_refused(self):
        self._visit()
        today = make_visit(
            make_patient(phone="9820099999"), self.doctor,
            start=today_at(15),
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
            start=today_at(15),
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
            start=today_at(15),
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
            start=today_at(15),
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

    def test_today_can_be_signed_off_once_the_clinic_has_finished(self):
        # Superseded. This used to refuse today outright, on the grounds that
        # sweeping it would close patients still in the building. The clinic
        # asked for the button on the board, so today is allowed — guarded by
        # can_close() instead, which is the condition that was really meant.
        self.client.post(reverse("reception_close_day"),
                         {"date": timezone.localdate().isoformat()})
        self.assertTrue(
            DaySignOff.objects.filter(date=timezone.localdate()).exists()
        )

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
        # Fixed hours of the day, not offsets from the clock: an offset run in
        # the evening lands tomorrow, which takes the visit off today's queue
        # and switches off the one-per-cabin rule under test.
        self.first = make_visit(
            make_patient(), self.doctor, start=today_at(9),
        )
        self.second = make_visit(
            make_patient(phone="9820088888"), self.doctor, start=today_at(11),
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


class TestTheClinicIsToldWhenNothingActuallyLeft(SignOffTestCase):
    """
    The most useful-sounding lie the system could tell.

    The clinic's own docker-compose.yml runs the development settings, whose
    mail backend prints to the container log. Django reports that as a
    successful send, so the receptionist would be told "report sent to
    contact@cemhcare.com" while nothing left the building — and nobody would
    find out until somebody asked the accountant why no day sheets ever
    arrived.
    """

    def test_the_console_backend_is_not_reported_as_delivery(self):
        self._visit(upto=[VisitStatus.CONFIRMED])
        with self.settings(
            EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"
        ):
            record, _ = signoff.sign_off(self.yesterday)
        self.assertIn("server log", record.delivery_error)
        self.assertIn("cemhcare", record.delivery_error)

    def test_the_day_is_still_signed_off(self):
        # Saying so must not become another way to block the next morning.
        self._visit(upto=[VisitStatus.CONFIRMED])
        with self.settings(
            EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"
        ):
            signoff.sign_off(self.yesterday)
        self.assertTrue(DaySignOff.objects.filter(date=self.yesterday).exists())
        self.assertIsNone(signoff.is_due(now=MID_MORNING()))

    def test_a_real_backend_is_reported_as_delivery(self):
        self._visit(upto=[VisitStatus.CONFIRMED])
        with self.settings(
            EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"
        ):
            self.assertTrue(signoff.is_really_delivered())

    def test_the_configured_address_is_the_clinics_own(self):
        # It carries patient names against amounts, so this is not a detail.
        self.assertEqual(signoff.recipients(), ["contact@cemhcare.com"])


class TestSignOffCanBeSwitchedOff(TestCase):
    """
    Off at the clinic's request, until there is a mail server for the report.

    The switch has to reach every part of it at once. Half-disabled — no alert
    but arrivals still held, or no button but the morning still blocked — is
    worse than either state, because nothing on the screen explains what is
    stopping the receptionist.
    """

    def setUp(self):
        # The shipped default. Set explicitly so this reads as the case under
        # test rather than as whatever config happens to say today.
        signoff.settings.CLINIC.DAY_SIGN_OFF_ENABLED = False
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)
        self.yesterday = timezone.localdate() - timedelta(days=1)

        visit = make_visit(make_patient(), self.doctor, start=_yesterday_at(10))
        visit.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
        self.stale = visit

    def test_the_default_is_off(self):
        from config import clinic

        self.assertFalse(clinic.DAY_SIGN_OFF_ENABLED)

    def test_nothing_is_ever_due(self):
        self.assertIsNone(signoff.is_due(now=MID_MORNING()))

    def test_the_board_carries_no_sign_off_alert(self):
        body = self.client.get(reverse("reception_home")).content.decode()
        self.assertNotIn("has not been signed off", body)
        self.assertNotIn("Sign off", body)
        self.assertNotIn("day sheet", body)

    def test_arrivals_are_not_held_up(self):
        # The half-disabled case: no alert on screen, but the rule underneath
        # still refusing. Nothing would explain the refusal.
        today = make_visit(
            make_patient(phone="9820077777"), self.doctor,
            start=today_at(15),
        )
        self.client.post(
            reverse("reception_move_visit", args=[today.pk, "ARRIVED"]),
            headers={"HX-Request": "true"},
        )
        today.refresh_from_db()
        self.assertEqual(today.status, VisitStatus.ARRIVED)

    def test_no_sign_off_is_recorded_and_no_report_sent(self):
        self.client.post(reverse("reception_close_day"))
        self.assertFalse(DaySignOff.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_the_stale_sweep_still_clears_the_board(self):
        # Older than the sign-off and not dependent on it. Without this the
        # board can never be cleared, and it goes on lying about who the doctor
        # is seeing.
        self.client.post(reverse("reception_close_day"))
        self.stale.refresh_from_db()
        self.assertEqual(self.stale.status, VisitStatus.NO_SHOW)

    def test_the_sweep_says_nothing_about_signing_off(self):
        response = self.client.post(reverse("reception_close_day"), follow=True)
        said = " ".join(str(m) for m in response.context["messages"])
        self.assertNotIn("sign", said.lower())

    def test_an_unbilled_consultation_is_still_not_swept_away(self):
        visit = make_visit(
            make_patient(phone="9820066666"), self.doctor, start=_yesterday_at(12),
        )
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED,
                       VisitStatus.IN_CABIN, VisitStatus.CONSULTED):
            visit.transition_to(status, by_user=self.receptionist)

        self.client.post(reverse("reception_close_day"))
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.CONSULTED)

    def test_the_sweep_clears_every_open_day_not_just_one(self):
        older = make_visit(
            make_patient(phone="9820055555"), self.doctor,
            start=timezone.now() - timedelta(days=4),
        )
        older.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)

        self.client.post(reverse("reception_close_day"))
        older.refresh_from_db()
        self.stale.refresh_from_db()
        self.assertEqual(older.status, VisitStatus.NO_SHOW)
        self.assertEqual(self.stale.status, VisitStatus.NO_SHOW)

    def test_the_scheduled_command_refuses_rather_than_mailing(self):
        # A cron entry left in place after the switch was thrown would keep
        # posting patient names and amounts to an address nobody expects them
        # at any more.
        from django.core.management import call_command
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("sign_off_day", verbosity=0)
        self.assertEqual(len(mail.outbox), 0)

    def test_force_still_runs_it_for_a_one_off(self):
        from django.core.management import call_command

        call_command("sign_off_day", "--force",
                     f"--date={self.yesterday:%Y-%m-%d}", verbosity=0)
        self.assertTrue(DaySignOff.objects.filter(date=self.yesterday).exists())


class TestTheSweepCanActuallyClearTheBoard(TestCase):
    """
    A visit that was paid for and never marked complete.

    The sweep had no answer for BILLED, so such a visit stayed on the
    "still open" strip for ever, and every press of Close them off reported it
    as "a consultation that has not been billed" — which it plainly is not. The
    board could never be cleared and the reason given for that was untrue.

    Predates the sign-off; it was simply hidden behind the sign-off panel until
    that was switched off.
    """

    def setUp(self):
        signoff.settings.CLINIC.DAY_SIGN_OFF_ENABLED = False
        self.receptionist = make_receptionist()
        self.doctor = make_doctor()
        self.client.force_login(self.receptionist)

        self.paid = make_visit(
            make_patient(), self.doctor, start=_yesterday_at(9),
        )
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED,
                       VisitStatus.IN_CABIN, VisitStatus.CONSULTED,
                       VisitStatus.BILLED):
            self.paid.transition_to(status, by_user=self.receptionist)

    def test_a_paid_visit_from_a_previous_day_is_closed(self):
        self.client.post(reverse("reception_close_day"))
        self.paid.refresh_from_db()
        self.assertEqual(self.paid.status, VisitStatus.COMPLETED)

    def test_the_board_clears(self):
        self.client.post(reverse("reception_close_day"))
        self.assertFalse(Visit.objects.unfinished_before().exists())

    def test_what_is_left_behind_is_described_truthfully(self):
        owed = make_visit(
            make_patient(phone="9820033333"), self.doctor, start=_yesterday_at(11),
        )
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED,
                       VisitStatus.IN_CABIN, VisitStatus.CONSULTED):
            owed.transition_to(status, by_user=self.receptionist)

        response = self.client.post(reverse("reception_close_day"), follow=True)
        said = " ".join(str(m) for m in response.context["messages"])
        self.assertIn("not been billed", said)
        # One left, not two: the paid one went.
        self.assertIn("1 still", said)


class TestTheUnclosedWorklist(SignOffTestCase):
    """
    The alert points at one place; that place has to be able to do the work.

    Billing inside a warning strip on a board that reloads every thirty seconds
    was the reason the feature was switched off. The list lives under All
    bookings now, as a tab that exists only while it has rows on it.
    """

    def test_the_tab_appears_only_when_something_is_open(self):
        clean = self.client.get(reverse("reception_bookings"))
        self.assertNotIn(
            "unclosed", [key for key, _label in clean.context["tabs"]],
        )

        self._consulted()
        with_work = self.client.get(reverse("reception_bookings"))
        self.assertIn("unclosed", [key for key, _label in with_work.context["tabs"]])

    def test_it_lists_the_open_appointment_with_a_receipt_button(self):
        visit = self._consulted()
        response = self.client.get(reverse("reception_bookings"), {"tab": "unclosed"})
        self.assertContains(response, visit.patient.patient_id)
        self.assertContains(
            response, reverse("reception_generate_receipt", args=[visit.pk]),
        )

    def test_it_covers_every_earlier_day_not_only_yesterday(self):
        # A day missed on Friday is still owed on Monday.
        old = make_visit(
            make_patient(phone="9820012121"), self.doctor,
            start=timezone.now() - timedelta(days=5),
        )
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED,
                       VisitStatus.IN_CABIN, VisitStatus.CONSULTED):
            old.transition_to(status, by_user=self.receptionist)

        response = self.client.get(reverse("reception_bookings"), {"tab": "unclosed"})
        self.assertContains(response, old.patient.patient_id)

    def test_taking_the_fee_is_the_whole_job(self):
        # The receipt is what locks the appointment. Asking for a second button
        # afterwards would make two steps out of the one the clinic described,
        # and the sweep marks paid visits finished on its own.
        visit = self._consulted()
        self.client.post(
            reverse("reception_generate_receipt", args=[visit.pk]),
            {"amount": "800", "method": "CASH", "reference": "", "notes": ""},
        )
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.BILLED)

        response = self.client.get(reverse("reception_bookings"))
        self.assertEqual(response.context["unclosed_count"], 0)

    def test_billing_it_takes_it_off_the_list(self):
        visit = self._consulted()
        self.client.post(
            reverse("reception_generate_receipt", args=[visit.pk]),
            {"amount": "800", "method": "CASH", "reference": "", "notes": ""},
        )
        response = self.client.get(reverse("reception_bookings"))
        self.assertEqual(response.context["unclosed_count"], 0)

    def test_the_sign_off_button_appears_once_the_list_is_empty(self):
        self._consulted()
        busy = self.client.get(reverse("reception_bookings"), {"tab": "unclosed"})
        self.assertFalse(busy.context["can_sign_off"])

        clear = self.client.get(reverse("reception_bookings"), {"tab": "unclosed"})
        self.assertIsNotNone(clear)

    def test_the_alert_sends_her_to_the_list_rather_than_billing_in_place(self):
        self._consulted()
        board = self.client.get(reverse("reception_home"))
        self.assertContains(board, "Show all unclosed appointments")
        self.assertContains(board, "tab=unclosed")


class TestTheSignOffButtonOnTheBoard(SignOffTestCase):
    """
    Visible only when the clinic has finished: Stage 3 empty, nothing owed in
    Stage 4. It is also the receptionist's answer to "has the doctor signed
    everyone off?" — she does not have to go and ask, because the button
    appearing is the answer.
    """

    def _today(self, hour=10, phone=None, upto=None):
        # A fixed hour of the day, not an offset from the clock. An offset runs
        # past midnight when the suite runs in the evening, which moves the
        # visit off today's board and quietly switches off the very rule under
        # test.
        visit = make_visit(
            make_patient(phone=phone) if phone else make_patient(),
            self.doctor, start=today_at(hour),
        )
        for status in (upto or []):
            visit.transition_to(status, by_user=self.receptionist)
        return visit

    def _board(self):
        return self.client.get(reverse("reception_home"))

    def test_hidden_while_somebody_is_in_a_cabin(self):
        self._today(upto=[VisitStatus.ARRIVED, VisitStatus.IN_CABIN])
        response = self._board()
        self.assertFalse(response.context["can_sign_off_today"])
        self.assertNotContains(response, "Today's clinic is finished")

    def test_hidden_while_a_consultation_is_still_to_be_billed(self):
        self._today(upto=[VisitStatus.ARRIVED, VisitStatus.IN_CABIN,
                          VisitStatus.CONSULTED])
        self.assertFalse(self._board().context["can_sign_off_today"])

    def test_shown_once_the_cabin_is_empty_and_everything_is_billed(self):
        visit = self._today(upto=[VisitStatus.ARRIVED, VisitStatus.IN_CABIN,
                                  VisitStatus.CONSULTED])
        Charge.objects.create(
            visit=visit, patient=visit.patient,
            consultation_fee=Decimal("800"), set_by=self.doctor,
        )
        self.client.post(
            reverse("reception_generate_receipt", args=[visit.pk]),
            {"amount": "800", "method": "CASH", "reference": "", "notes": ""},
        )
        response = self._board()
        self.assertTrue(response.context["can_sign_off_today"])
        self.assertContains(response, "Today's clinic is finished")

    def test_a_booking_nobody_arrived_for_does_not_hold_it_back(self):
        # Stages 1 and 2 are not checked. Somebody who never turned up is an
        # ordinary end to a day, and demanding it be cleared first would make
        # the button unreachable whenever a patient went home.
        self._today()
        self.assertTrue(self._board().context["can_sign_off_today"])

    def test_signing_today_off_does_not_cancel_this_evenings_bookings(self):
        # The sweep is right for yesterday and quite wrong at four o'clock.
        later = self._today(hour=17)
        self.client.post(reverse("reception_close_day"),
                         {"date": timezone.localdate().isoformat()})
        later.refresh_from_db()
        self.assertEqual(later.status, VisitStatus.BOOKED)
        self.assertTrue(DaySignOff.objects.filter(date=timezone.localdate()).exists())

    def test_signing_today_off_is_refused_while_a_cabin_is_busy(self):
        # The button is hidden, so reaching here means going round the screen.
        self._today(upto=[VisitStatus.ARRIVED, VisitStatus.IN_CABIN])
        self.client.post(reverse("reception_close_day"),
                         {"date": timezone.localdate().isoformat()})
        self.assertFalse(DaySignOff.objects.exists())

    def test_tomorrow_cannot_be_signed_off(self):
        ahead = timezone.localdate() + timedelta(days=1)
        self.client.post(reverse("reception_close_day"),
                         {"date": ahead.isoformat()})
        self.assertFalse(DaySignOff.objects.exists())


class TestAConsultationWithNoFeeCanStillBeClosed(SignOffTestCase):
    """
    The dead end that would have made the whole thing unusable.

    A doctor can finish a consultation without setting a fee — a free follow-up,
    or simply forgetting. The receipt dialog has nothing to collect, so it shows
    "no fee has been set" and a Close button, and the visit stays CONSULTED for
    ever: on the unclosed list, blocking the sign-off, holding up every
    following morning's arrivals. The receptionist cannot invent a fee, and
    nothing on the screen offers a way out.

    So there is one, and it is explicit and recorded rather than quiet. Writing
    off a fee is a decision somebody makes, and the trail has to say that
    nothing was collected and who decided it.
    """

    def setUp(self):
        super().setUp()
        self.visit = self._visit(upto=[
            VisitStatus.CONFIRMED, VisitStatus.ARRIVED,
            VisitStatus.IN_CABIN, VisitStatus.CONSULTED,
        ])   # deliberately no Charge

    def test_it_is_on_the_unclosed_list(self):
        self.assertIn(self.visit, signoff.unclosed_before())

    def test_the_list_offers_a_way_to_close_it(self):
        response = self.client.get(reverse("reception_bookings"), {"tab": "unclosed"})
        self.assertContains(response, "No fee set")
        self.assertContains(
            response, reverse("reception_close_without_fee", args=[self.visit.pk]),
        )

    def test_closing_it_takes_it_off_the_list(self):
        self.client.post(
            reverse("reception_close_without_fee", args=[self.visit.pk]),
            {"next": reverse("reception_bookings")},
        )
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, VisitStatus.COMPLETED)
        self.assertEqual(signoff.unclosed_before(), [])

    def test_the_trail_says_nothing_was_collected(self):
        self.client.post(reverse("reception_close_without_fee", args=[self.visit.pk]))
        note = self.visit.status_events.latest("created_at").note
        self.assertIn("no fee", note.lower())

    def test_it_is_refused_when_there_is_actually_money_owed(self):
        # Otherwise this becomes a one-click way to write off a real bill.
        owed = self._consulted(hour=14, phone="9820031313")
        self.client.post(reverse("reception_close_without_fee", args=[owed.pk]))
        owed.refresh_from_db()
        self.assertEqual(owed.status, VisitStatus.CONSULTED)

    def test_the_day_can_then_be_signed_off(self):
        self.client.post(reverse("reception_close_without_fee", args=[self.visit.pk]))
        self.client.post(reverse("reception_close_day"),
                         {"date": self.yesterday.isoformat()})
        self.assertTrue(DaySignOff.objects.filter(date=self.yesterday).exists())


class TestUnarrivedAppointmentsCloseThemselves(SignOffTestCase):
    """
    The clinic's rule: a patient who did not turn up needs nothing pressed.

    Cancel is gone from the Stage 1 card, so this is the only thing that clears
    them — which makes "it must not touch a consultation" matter more here than
    anywhere else. This is the function whose whole job is closing things, and
    an unbilled consultation swept away is the money nobody looks for again.
    """

    def test_a_booking_nobody_arrived_for_is_cancelled(self):
        visit = self._visit()
        signoff.auto_cancel_stale(now=MID_MORNING())
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.CANCELLED)

    def test_a_confirmed_booking_the_patient_missed_is_a_no_show(self):
        # Both are "not arrived". They are reported separately because the day
        # sheet has a no-show tab, which one bucket would leave always empty.
        visit = self._visit(upto=[VisitStatus.CONFIRMED])
        signoff.auto_cancel_stale(now=MID_MORNING())
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.NO_SHOW)

    def test_nothing_happens_before_five_in_the_morning(self):
        # An evening list running past midnight would otherwise have its
        # waiting patients cancelled out from under it.
        visit = self._visit()
        four_am = timezone.localtime().replace(hour=4, minute=0)
        self.assertEqual(signoff.auto_cancel_stale(now=four_am), 0)
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.BOOKED)

    def test_an_unbilled_consultation_is_never_touched(self):
        visit = self._consulted()
        signoff.auto_cancel_stale(now=MID_MORNING())
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.CONSULTED)

    def test_todays_bookings_are_left_alone(self):
        today = make_visit(
            make_patient(phone="9820045454"), self.doctor, start=today_at(15),
        )
        signoff.auto_cancel_stale(now=MID_MORNING())
        today.refresh_from_db()
        self.assertEqual(today.status, VisitStatus.BOOKED)

    def test_opening_the_board_is_what_triggers_it(self):
        visit = self._visit()
        self.client.get(reverse("reception_home"))
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.CANCELLED)

    def test_the_trail_says_why(self):
        visit = self._visit()
        signoff.auto_cancel_stale(now=MID_MORNING())
        note = visit.status_events.latest("created_at").note
        self.assertIn("not marked arrived", note.lower())

    def test_it_is_cheap_to_repeat(self):
        self._visit()
        self.assertEqual(signoff.auto_cancel_stale(now=MID_MORNING()), 1)
        self.assertEqual(signoff.auto_cancel_stale(now=MID_MORNING()), 0)


class TestTheCancelButtonIsGoneFromTheCard(SignOffTestCase):
    """
    It sat one button from Mark arrived, on the screen used most under
    pressure, and took no reason. Cancelling now happens on the booking itself,
    where a reason is asked for and recorded.
    """

    def test_the_stage_one_card_offers_only_mark_arrived(self):
        make_visit(make_patient(phone="9820067676"), self.doctor, start=today_at(15))
        body = self.client.get(reverse("reception_home")).content.decode()
        self.assertIn("Mark arrived", body)
        self.assertNotIn(">Cancel<", body)

    def test_a_booking_can_still_be_cancelled_with_a_reason(self):
        # The capability must not have gone with the button.
        visit = make_visit(
            make_patient(phone="9820078787"), self.doctor, start=today_at(16),
        )
        self.client.post(
            reverse("reception_edit_booking", args=[visit.pk]),
            {"action": "cancel", "reason": "Patient rang to cancel"},
        )
        visit.refresh_from_db()
        self.assertEqual(visit.status, VisitStatus.CANCELLED)
