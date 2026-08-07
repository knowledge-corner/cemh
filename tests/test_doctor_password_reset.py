"""
"Forgotten password" for a doctor who has already activated — the same
single-use, time-limited link KAN-21 built for the first-time invitation,
reused rather than duplicated. The rule is identical either way: reception
never generates, sees or emails a password, only a link that lets the doctor
choose their own.

Also covers the username now being told to the doctor — in both emails, and
on screen the moment they finish setting a password — since without it there
was previously no way for anyone, including reception, to know what to type
at the login screen.
"""

from django.core import mail
from django.test import TestCase
from django.urls import reverse

from accounts.models import Specialisation, User

from .factories import make_receptionist


def _form(**overrides):
    payload = {
        "full_name": "Vrushali Kulkarni",
        "email": "vrushali@example.in",
        "phone": "9820012345",
        "specialisation": str(
            Specialisation.objects.get(name="Paediatric Endocrinology").pk
        ),
        "new_specialisation": "",
        "registration_number": "MMC-12345",
        "qualification": "MBBS, MD, DM",
    }
    payload.update(overrides)
    return payload


def _token_from_email(index=0):
    return mail.outbox[index].body.split("/activate/")[1].split("/")[0].strip()


class PasswordResetTestCase(TestCase):
    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.client.post(reverse("reception_add_doctor"), _form())
        self.doctor = User.objects.get(email="vrushali@example.in")

        # Activate: follow the invitation link and set a password.
        token = _token_from_email()
        self.client.logout()
        self.client.post(
            reverse("doctor_activate", args=[token]),
            {"new_password1": "the-original-passphrase-1",
             "new_password2": "the-original-passphrase-1"},
        )
        self.client.logout()
        mail.outbox.clear()

        # Setting a password changes the row this object was fetched before —
        # force_login-ing the stale instance later would carry the old
        # (unusable) password's session-auth-hash, which Django's own session
        # check would then correctly treat as stale and log straight back out.
        self.doctor.refresh_from_db()

        self.client.force_login(self.receptionist)

    def _reset(self):
        return self.client.post(
            reverse("reception_resend_invitation", args=[self.doctor.pk])
        )


class TestSendingAResetLink(PasswordResetTestCase):
    def test_an_email_is_sent(self):
        self._reset()
        self.assertEqual(len(mail.outbox), 1)

    def test_the_subject_says_reset_not_set(self):
        self._reset()
        self.assertIn("Reset your password", mail.outbox[0].subject)

    def test_it_tells_them_their_username(self):
        self._reset()
        self.assertIn(self.doctor.username, mail.outbox[0].body)

    def test_it_does_not_say_they_were_just_added(self):
        # Wrong copy for somebody who has had an account for a while.
        self._reset()
        self.assertNotIn("You have been added", mail.outbox[0].body)

    def test_it_says_the_current_password_still_works(self):
        self._reset()
        self.assertIn("current password", mail.outbox[0].body)

    def test_it_is_audited_distinctly_from_an_invitation(self):
        from audit.models import AccessLog, AuditAction
        self._reset()
        entry = AccessLog.objects.filter(action=AuditAction.UPDATE).latest("id")
        self.assertIn("reset", entry.description.lower())

    def test_a_doctor_cannot_trigger_their_own_reset(self):
        self.client.force_login(self.doctor)
        response = self._reset()
        self.assertEqual(response.status_code, 403)

    def test_an_earlier_unused_reset_link_is_revoked_by_a_newer_one(self):
        self._reset()
        first_token = _token_from_email()
        self._reset()
        self.client.logout()
        response = self.client.get(reverse("doctor_activate", args=[first_token]))
        self.assertContains(response, "no longer valid", status_code=400)


class TestUsingTheResetLink(PasswordResetTestCase):
    def test_the_new_password_works(self):
        self._reset()
        token = _token_from_email()
        self.client.logout()
        self.client.post(
            reverse("doctor_activate", args=[token]),
            {"new_password1": "a-brand-new-passphrase-2",
             "new_password2": "a-brand-new-passphrase-2"},
        )
        self.client.logout()

        self.assertTrue(
            self.client.login(
                username=self.doctor.username, password="a-brand-new-passphrase-2"
            )
        )

    def test_the_old_password_stops_working(self):
        self._reset()
        token = _token_from_email()
        self.client.logout()
        self.client.post(
            reverse("doctor_activate", args=[token]),
            {"new_password1": "a-brand-new-passphrase-2",
             "new_password2": "a-brand-new-passphrase-2"},
        )
        self.client.logout()

        self.assertFalse(
            self.client.login(
                username=self.doctor.username, password="the-original-passphrase-1"
            )
        )

    def test_the_original_activation_date_is_kept(self):
        # activated_at means *first* activation. A reset must not overwrite
        # it — that would misreport when the doctor actually first signed in.
        original = self.doctor.doctor_profile.activated_at

        self._reset()
        token = _token_from_email()
        self.client.logout()
        self.client.post(
            reverse("doctor_activate", args=[token]),
            {"new_password1": "a-brand-new-passphrase-2",
             "new_password2": "a-brand-new-passphrase-2"},
        )

        self.doctor.doctor_profile.refresh_from_db()
        self.assertEqual(self.doctor.doctor_profile.activated_at, original)

    def test_setting_the_password_tells_them_their_username(self):
        self._reset()
        token = _token_from_email()
        self.client.logout()
        response = self.client.post(
            reverse("doctor_activate", args=[token]),
            {"new_password1": "a-brand-new-passphrase-2",
             "new_password2": "a-brand-new-passphrase-2"},
            follow=True,
        )
        self.assertContains(response, self.doctor.username)


class TestTheDoctorsListOffersTheRightAction(PasswordResetTestCase):
    def test_an_active_doctor_gets_reset_password_not_resend_invitation(self):
        response = self.client.get(reverse("reception_doctors"))
        self.assertContains(response, "Reset password")
        self.assertNotContains(response, "Resend invitation")

    def test_a_pending_doctor_still_gets_resend_invitation(self):
        self.client.post(reverse("reception_add_doctor"), _form(
            full_name="Nikhil Sharma", email="nikhil@example.in",
        ))
        response = self.client.get(reverse("reception_doctors"))
        self.assertContains(response, "Resend invitation")

    def test_an_inactive_doctor_gets_neither(self):
        self.doctor.is_active = False
        self.doctor.save(update_fields=["is_active"])
        response = self.client.get(reverse("reception_doctors"))
        self.assertNotContains(response, "Reset password")
        self.assertNotContains(response, "Resend invitation")


class TestAnInactiveDoctorCannotBeSentALink(PasswordResetTestCase):
    def setUp(self):
        super().setUp()
        self.doctor.is_active = False
        self.doctor.save(update_fields=["is_active"])

    def test_the_request_is_refused(self):
        self._reset()
        self.assertEqual(len(mail.outbox), 0)

    def test_it_says_why(self):
        self._reset()
        response = self.client.get(reverse("reception_doctors"))
        self.assertContains(response, "inactive")
