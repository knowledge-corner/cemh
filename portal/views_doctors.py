"""
Adding doctors, and the invitation that lets them set their own password.

The rule the whole flow is built around: reception creates the record, the
doctor creates the password. Nothing here generates, displays, emails or logs a
credential — see :mod:`accounts.invitations`.
"""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import SetPasswordForm
from django.core.mail import BadHeaderError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts import invitations
from accounts.models import DoctorInvitation, DoctorProfile, Role, User
from accounts.permissions import role_required
from audit.models import AuditAction
from audit.services import record

from . import forms as clinic_forms


@role_required(Role.RECEPTIONIST)
def doctor_list(request):
    """Every doctor, and where each one is in registration (FR-5)."""
    doctors = (
        User.objects.filter(role=Role.DOCTOR)
        .select_related("doctor_profile")
        .order_by("first_name", "last_name")
    )
    return render(request, "portal/reception/doctors.html", {"doctors": doctors})


@role_required(Role.RECEPTIONIST)
def add_doctor(request):
    """
    Create the doctor and send the invitation.

    The record is written before the email is attempted, and kept if the email
    fails. A doctor who exists but could not be reached is a problem reception
    can fix by re-sending; a doctor who was silently not created because the
    mail server was down is one they find out about a week later.
    """
    if request.method == "POST":
        form = clinic_forms.DoctorForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                doctor = form.save()
                record(
                    request, AuditAction.CREATE, obj=doctor,
                    description=f"Doctor {doctor.display_name} added, pending activation",
                )

            try:
                invitations.send_invitation(request, doctor, by=request.user)
            except (BadHeaderError, OSError, ValueError) as exc:
                messages.warning(
                    request,
                    f"{doctor.display_name} was added, but the invitation email "
                    f"could not be sent ({exc}). Check the address and use "
                    f"Resend invitation.",
                )
            else:
                messages.success(
                    request,
                    f"{doctor.display_name} added. An invitation to set their "
                    f"own password has been sent to {doctor.email}.",
                )
            return redirect("reception_doctors")
    else:
        form = clinic_forms.DoctorForm()

    return render(request, "portal/reception/add_doctor.html", {"form": form})


@role_required(Role.RECEPTIONIST)
def resend_invitation(request, pk):
    """
    Issue a fresh link (FR-6, AC-8).

    Issuing revokes whatever came before it, which is what makes a mistyped
    address safe to correct: the link that went to the wrong inbox stops
    working the moment the new one is made.
    """
    doctor = get_object_or_404(
        User.objects.select_related("doctor_profile"), pk=pk, role=Role.DOCTOR
    )
    profile = getattr(doctor, "doctor_profile", None)

    if request.method != "POST":
        return redirect("reception_doctors")

    if profile is None or not profile.is_pending:
        messages.info(
            request, f"{doctor.display_name} has already set a password."
        )
        return redirect("reception_doctors")

    try:
        invitations.send_invitation(request, doctor, by=request.user)
    except (BadHeaderError, OSError, ValueError) as exc:
        messages.error(request, f"The invitation could not be sent ({exc}).")
    else:
        record(
            request, AuditAction.UPDATE, obj=doctor,
            description="Doctor invitation re-sent",
        )
        messages.success(
            request,
            f"A new invitation has been sent to {doctor.email}. Any earlier "
            f"link no longer works.",
        )
    return redirect("reception_doctors")


def activate_doctor(request, token):
    """
    The doctor's own screen: follow the link, choose a password.

    Deliberately reachable without signing in — the whole point is that they
    cannot sign in yet. The token is the only thing that identifies them, so
    every reason it might not be good enough is checked before a form is shown,
    and again before anything is saved.
    """
    invitation = DoctorInvitation.for_token(token)

    if invitation is None or invitation.revoked_at:
        return render(request, "accounts/activation_problem.html", {
            "heading": "This link is no longer valid",
            "detail": "A newer invitation was sent, so this one stopped working. "
                      "Use the most recent email, or ask reception to send another.",
        }, status=400)

    if invitation.used_at:
        # AC-5.
        return render(request, "accounts/activation_problem.html", {
            "heading": "This link has already been used",
            "detail": "The password for this account has been set. Sign in with "
                      "it, or use 'Forgotten password' if you cannot remember it.",
            "show_login": True,
        }, status=400)

    if invitation.is_expired:
        # AC-6.
        return render(request, "accounts/activation_problem.html", {
            "heading": "This link has expired",
            "detail": "Invitations are only good for a few days. Ask reception "
                      "to send you a new one.",
        }, status=400)

    doctor = invitation.user

    if request.method == "POST":
        form = SetPasswordForm(doctor, request.POST)
        if form.is_valid():
            with transaction.atomic():
                form.save()

                doctor.is_active = True
                doctor.save(update_fields=["is_active"])

                profile, _ = DoctorProfile.objects.get_or_create(user=doctor)
                profile.activated_at = timezone.now()
                profile.save(update_fields=["activated_at"])

                invitation.consume()

            # Straight in, rather than to a login screen they would immediately
            # fill in with the password they have just this second chosen.
            login(request, doctor)
            messages.success(request, "Your password is set. Welcome.")
            return redirect("doctor_home")
    else:
        form = SetPasswordForm(doctor)

    return render(request, "accounts/activate.html", {
        "form": form, "doctor": doctor,
    })
