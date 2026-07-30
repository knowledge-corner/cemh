"""
Test object builders.

Plain functions with a defaults-dict rather than a factory library — one less
dependency, and the call sites read clearly.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import Role
from appointments.models import Visit
from patients.models import Patient, PatientHistory

User = get_user_model()


def make_user(**kwargs):
    defaults = dict(
        username="user1",
        email="user1@example.in",
        password="testpass12345",
        first_name="Test",
        last_name="User",
        phone="9820000001",
        role=Role.PATIENT,
    )
    defaults.update(kwargs)
    password = defaults.pop("password")
    user = User(**defaults)
    user.set_password(password)
    user.save()
    return user


def make_doctor(**kwargs):
    defaults = dict(
        username="drtest", email="drtest@example.in", first_name="Asha",
        last_name="Rao", role=Role.DOCTOR, phone="9820000002",
    )
    defaults.update(kwargs)
    return make_user(**defaults)


def make_receptionist(**kwargs):
    defaults = dict(
        username="recep", email="recep@example.in", first_name="Sunita",
        last_name="Rane", role=Role.RECEPTIONIST, phone="9820000003",
    )
    defaults.update(kwargs)
    return make_user(**defaults)


def make_patient(**kwargs):
    defaults = dict(
        first_name="Aarav",
        last_name="Deshpande",
        date_of_birth=timezone.localdate() - timedelta(days=int(9 * 365.25)),
        sex="M",
        phone="9820012345",
    )
    defaults.update(kwargs)
    return Patient.objects.create(**defaults)


def make_adult_patient(**kwargs):
    defaults = dict(
        first_name="Ramesh",
        last_name="Iyer",
        date_of_birth=timezone.localdate() - timedelta(days=int(45 * 365.25)),
        sex="M",
        phone="9820012346",
    )
    defaults.update(kwargs)
    return make_patient(**defaults)


def make_history(patient, **kwargs):
    defaults = dict(allergies="", family_history="")
    defaults.update(kwargs)
    return PatientHistory.objects.create(patient=patient, **defaults)


def make_visit(patient, doctor, **kwargs):
    start = kwargs.pop("start", timezone.now() + timedelta(hours=1))
    defaults = dict(
        patient=patient,
        doctor=doctor,
        scheduled_start=start,
        scheduled_end=start + timedelta(minutes=20),
        reason="Follow-up",
    )
    defaults.update(kwargs)
    return Visit.objects.create(**defaults)


def make_measurement(patient, **kwargs):
    from django.apps import apps

    Measurement = apps.get_model("growth", "Measurement")
    defaults = dict(
        patient=patient,
        measured_on=timezone.localdate(),
        height_cm=Decimal("123.1"),
        weight_kg=Decimal("23.6"),
    )
    defaults.update(kwargs)
    return Measurement.objects.create(**defaults)
