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
                doctor = form.save(added_by=request.user)
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
def edit_doctor(request, pk):
    """Correct a doctor's own details — name, phone, specialisation, and
    what is printed on their prescriptions."""
    doctor = get_object_or_404(
        User.objects.select_related("doctor_profile__specialisation"),
        pk=pk, role=Role.DOCTOR,
    )

    if request.method == "POST":
        form = clinic_forms.DoctorEditForm(request.POST, doctor=doctor)
        if form.is_valid():
            form.save()
            record(
                request, AuditAction.UPDATE, obj=doctor,
                description=f"Doctor {doctor.display_name}'s details updated",
            )
            messages.success(request, f"{doctor.display_name}'s details have been updated.")
            return redirect("reception_doctors")
    else:
        form = clinic_forms.DoctorEditForm(doctor=doctor)

    return render(request, "portal/reception/edit_doctor.html", {
        "form": form, "doctor": doctor,
    })


@role_required(Role.RECEPTIONIST)
def resend_invitation(request, pk):
    """
    Issue a fresh link (FR-6, AC-8) — and the same mechanism doubles as a
    password reset once the doctor has already activated.

    A doctor who has forgotten their password is not in a different state
    from one who never set one: both need a single-use link to choose one,
    and KAN-21's rule — never a password reception generates, shows or
    emails — applies exactly the same either way. So this is the same view
    and the same underlying invitation either way; only the wording (and,
    for an inactive doctor, the refusal) differs.

    Issuing revokes whatever came before it, which is what makes a mistyped
    address safe to correct: the link that went to the wrong inbox stops
    working the moment the new one is made. The same property makes a reset
    safe too — an old, unused reset link cannot be used after a newer one is
    sent.
    """
    doctor = get_object_or_404(
        User.objects.select_related("doctor_profile"), pk=pk, role=Role.DOCTOR
    )
    profile = getattr(doctor, "doctor_profile", None)

    if request.method != "POST":
        return redirect("reception_doctors")

    is_pending = profile is None or profile.is_pending

    # A doctor who has left is not offered a fresh way back in from here —
    # that is a decision about their account, not a forgotten password.
    if not is_pending and not doctor.is_active:
        messages.error(
            request,
            f"{doctor.display_name} is inactive, so cannot be sent a "
            f"password link.",
        )
        return redirect("reception_doctors")

    try:
        invitations.send_invitation(
            request, doctor, by=request.user, is_reset=not is_pending
        )
    except (BadHeaderError, OSError, ValueError) as exc:
        messages.error(request, f"The link could not be sent ({exc}).")
    else:
        if is_pending:
            record(
                request, AuditAction.UPDATE, obj=doctor,
                description="Doctor invitation re-sent",
            )
            messages.success(
                request,
                f"A new invitation has been sent to {doctor.email}. Any "
                f"earlier link no longer works.",
            )
        else:
            record(
                request, AuditAction.UPDATE, obj=doctor,
                description="Doctor password reset link sent",
            )
            messages.success(
                request,
                f"A password reset link has been sent to {doctor.email}. "
                f"Their current password keeps working until they use it.",
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
                # Only set the first time. This same screen now also serves a
                # password reset, and activated_at means *first* activation —
                # overwriting it on every reset would lose that.
                if profile.activated_at is None:
                    profile.activated_at = timezone.now()
                    profile.save(update_fields=["activated_at"])

                invitation.consume()

            # Straight in, rather than to a login screen they would immediately
            # fill in with the password they have just this second chosen.
            login(request, doctor)
            messages.success(
                request,
                f"Your password is set. You'll sign in as {doctor.username}. Welcome.",
            )
            return redirect("doctor_home")
    else:
        form = SetPasswordForm(doctor)

    return render(request, "accounts/activate.html", {
        "form": form, "doctor": doctor,
    })
