"""
Inviting a doctor to set their own password.

The one rule this module exists to keep: **the clinic never handles a doctor's
password.** Reception does not choose one, no screen displays one, no email
carries one, and nothing here writes one to a log. What is shared is a link
carrying a random token, valid once and not for long, and following it is the
only way a password gets set.

Kept in one place rather than spread across the views, so "does anything here
send a credential" is a question with one file for an answer.
"""

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from .models import DoctorInvitation


def activation_url(request, raw_token):
    """The absolute link that goes in the email."""
    path = reverse("doctor_activate", args=[raw_token])
    return request.build_absolute_uri(path)


def send_invitation(request, user, *, by=None, is_reset=False):
    """
    Issue a fresh invitation and email it.

    The same link does two jobs, depending on whether the doctor has ever
    finished it before: setting a password for the first time, or choosing a
    new one because the old one is forgotten. Nothing about the mechanism
    differs — it is still a single-use, time-limited link and never a
    password — so this is one function with a change of wording, not two.

    Returns the invitation. Any earlier unused link for this doctor stops
    working, which is what makes correcting a mistyped address safe: the
    invitation that went to the wrong inbox is dead before the new one is sent.

    Delivery failure is not hidden. The account and the invitation are already
    written by the time the mail is attempted, so the caller can tell reception
    the record exists and the email did not arrive — which is the behaviour the
    edge-case table asks for.
    """
    invitation, raw = DoctorInvitation.issue(user, by=by)

    context = {
        "doctor": user,
        "clinic_name": settings.CLINIC.CLINIC_NAME,
        "url": activation_url(request, raw),
        "days": settings.CLINIC.INVITATION_DAYS,
        "is_reset": is_reset,
    }

    subject = (
        f"Reset your password for {settings.CLINIC.CLINIC_NAME}" if is_reset
        else f"Set your password for {settings.CLINIC.CLINIC_NAME}"
    )

    send_mail(
        subject=subject,
        message=render_to_string("accounts/email/invitation.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        # Deliberately not silenced: a failure has to reach the receptionist,
        # who is standing next to the doctor and can read the address back.
        fail_silently=False,
    )
    return invitation
