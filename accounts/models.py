"""
Users and roles.

Everyone who logs in — doctor, receptionist, patient — is a single ``User``
row distinguished by ``role``. One user table keeps authentication, password
reset and session handling uniform; the role decides which dashboard they land
on and what they are allowed to reach.
"""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

# Indian mobile numbers, optionally with a +91 / 0 prefix.
phone_validator = RegexValidator(
    regex=r"^(\+91[\-\s]?)?[0]?[6-9]\d{9}$",
    message="Enter a valid 10-digit Indian mobile number.",
)


class Role(models.TextChoices):
    DOCTOR = "DOCTOR", "Doctor"
    RECEPTIONIST = "RECEPTIONIST", "Receptionist"
    PATIENT = "PATIENT", "Patient"
    ADMIN = "ADMIN", "Administrator"


class Specialisation(models.Model):
    """
    What a doctor specialises in.

    A table rather than a fixed list of choices, because reception has to be
    able to add one without waiting for a release. KAN-21 shipped this as three
    hard-coded values — Adult, Paediatric, Adult & paediatric — which were not
    specialisations at all but a division of the patient list; KAN-37 replaced
    them with the real thing.

    Uniqueness is case-insensitive. Otherwise the first person to type
    "cardiology" creates a second entry beside "Cardiology", and from then on
    the filter shows two of everything.
    """

    name = models.CharField(max_length=100)

    #: Retired rather than deleted. A specialisation in use cannot be removed
    #: without orphaning the doctors on it, and the clinic's history should not
    #: change because a list was tidied.
    is_active = models.BooleanField(
        default=True,
        help_text="Unticked, this stays on existing doctors but is not offered "
                  "for new ones.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="specialisations_added",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"), name="specialisation_name_unique_ci",
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Collapse the whitespace before the uniqueness check sees it, so
        # "Cardiology " and "Cardiology" cannot both exist.
        self.name = " ".join((self.name or "").split())
        super().save(*args, **kwargs)


class User(AbstractUser):
    """
    Clinic user account.

    Inherits username, password, first/last name and the permission flags from
    ``AbstractUser``; adds the contact number and role the clinic asked for.
    """

    email = models.EmailField(
        "email address",
        unique=True,
        help_text="Used for password reset and notifications. Must be unique.",
    )
    phone = models.CharField(
        "contact number",
        max_length=15,
        blank=True,
        validators=[phone_validator],
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.PATIENT,
        help_text="Decides which dashboard this user sees after logging in.",
    )

    class Meta:
        ordering = ["first_name", "last_name", "username"]

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def is_doctor(self):
        return self.role == Role.DOCTOR

    @property
    def is_receptionist(self):
        return self.role == Role.RECEPTIONIST

    @property
    def is_patient(self):
        return self.role == Role.PATIENT


class DoctorProfile(models.Model):
    """
    Extra detail a doctor needs that no other role does.

    Registration number and signature appear on printed prescriptions, so this
    is clinical record-keeping, not decoration.
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="doctor_profile"
    )
    registration_number = models.CharField(
        max_length=50,
        blank=True,
        help_text="State medical council registration number, printed on prescriptions.",
    )
    qualification = models.CharField(
        max_length=200, blank=True, help_text="e.g. MBBS, MD, DM (Endocrinology)"
    )
    speciality = models.CharField(max_length=200, blank=True)

    #: The doctor's introduction on the public website.
    #:
    #: A field rather than paragraphs typed into the template, because a doctor
    #: joining or leaving should not need a developer — and a template cannot be
    #: right about a doctor it has never heard of. Blank means the paragraphs
    #: are simply not rendered.
    bio = models.TextField(
        blank=True,
        help_text="Shown on the public website. Leave a blank line between "
                  "paragraphs.",
    )

    #: What the calendar filters on (KAN-21 FR-9, as revised by KAN-37).
    #:
    #: PROTECT, so a specialisation cannot be deleted out from under the
    #: doctors who hold it. Retire it instead — see Specialisation.is_active.
    specialisation = models.ForeignKey(
        "accounts.Specialisation",
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name="doctors",
        help_text="What this doctor specialises in. Drives the calendar filter.",
    )

    #: When the doctor followed their invitation and set a password.
    #:
    #: ``None`` means Pending, and a pending doctor cannot be scheduled. Kept
    #: separate from ``user.is_active`` because the two say different things: a
    #: doctor who has left is inactive but was never pending, and reading one
    #: as the other would put a departed doctor back on the invitation list.
    activated_at = models.DateTimeField(null=True, blank=True)
    signature = models.ImageField(
        upload_to="signatures/",
        blank=True,
        null=True,
        help_text="Scanned signature placed on generated prescriptions.",
    )

    class Meta:
        verbose_name = "doctor profile"
        verbose_name_plural = "doctor profiles"

    def __str__(self):
        return f"Dr. {self.user.display_name}"

    @property
    def is_pending(self):
        """Invited but not yet activated, so not schedulable (FR-7)."""
        return self.activated_at is None

    @property
    def status_label(self):
        if self.activated_at is None:
            return "Pending"
        return "Active" if self.user.is_active else "Inactive"

    @property
    def display_title(self):
        parts = [f"Dr. {self.user.display_name}"]
        if self.qualification:
            parts.append(self.qualification)
        return ", ".join(parts)



class DoctorInvitation(models.Model):
    """
    An emailed invitation for a doctor to set their own password.

    The clinic never chooses a doctor's password, never sees one and never
    sends one. What goes in the email is a link carrying a random token, and
    following it is the only way to set a password.

    The token is stored **hashed**. A stolen backup or a leaked log then yields
    nothing usable: the raw token exists once, in the email, and nowhere else.
    Same reason a password is hashed, for exactly the same kind of secret.
    """

    #: Long enough that guessing is not a strategy.
    TOKEN_BYTES = 32

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="invitations",
    )
    token_hash = models.CharField(max_length=64, unique=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    #: Set the moment the link is followed successfully — single use.
    used_at = models.DateTimeField(null=True, blank=True)
    #: Set when a newer invitation supersedes this one.
    revoked_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="invitations_sent",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "doctor invitation"

    def __str__(self):
        return f"Invitation for {self.user.display_name} ({self.state})"

    # ── Issuing ──────────────────────────────────────────────────────────────

    @staticmethod
    def hash_token(raw):
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def issue(cls, user, *, by=None, valid_for=None):
        """
        Create an invitation and return ``(invitation, raw_token)``.

        The raw token is returned once and never stored. Any invitation this
        user already had is revoked first: re-sending has to invalidate the
        previous link, or a doctor who was invited to the wrong address still
        has a working way in.
        """
        cls.objects.filter(
            user=user, used_at__isnull=True, revoked_at__isnull=True,
        ).update(revoked_at=timezone.now())

        raw = secrets.token_urlsafe(cls.TOKEN_BYTES)
        window = valid_for or timedelta(days=settings.CLINIC.INVITATION_DAYS)
        invitation = cls.objects.create(
            user=user,
            token_hash=cls.hash_token(raw),
            expires_at=timezone.now() + window,
            created_by=by,
        )
        return invitation, raw

    # ── Reading ──────────────────────────────────────────────────────────────

    @classmethod
    def for_token(cls, raw):
        """The invitation a raw token names, whatever state it is in."""
        if not raw:
            return None
        return cls.objects.filter(token_hash=cls.hash_token(raw)).first()

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_usable(self):
        return self.used_at is None and self.revoked_at is None and not self.is_expired

    @property
    def state(self):
        if self.used_at:
            return "used"
        if self.revoked_at:
            return "replaced"
        if self.is_expired:
            return "expired"
        return "open"

    def consume(self):
        """Mark the invitation spent. Called once the password is actually set."""
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])
