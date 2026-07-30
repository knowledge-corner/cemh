"""
Users and roles.

Everyone who logs in — doctor, receptionist, patient — is a single ``User``
row distinguished by ``role``. One user table keeps authentication, password
reset and session handling uniform; the role decides which dashboard they land
on and what they are allowed to reach.
"""

from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models

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
    def display_title(self):
        parts = [f"Dr. {self.user.display_name}"]
        if self.qualification:
            parts.append(self.qualification)
        return ", ".join(parts)
