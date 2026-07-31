"""
The clinic's public page.

One page, no login, no forms. Patients do not book online — they read what the
clinic does and then telephone or send a WhatsApp message, which is exactly what
this page is built to produce.

Kept as its own app because the public face is the thing that differs most
between clinics; another clinic replaces this app and touches nothing clinical.
"""

from urllib.parse import quote

from django.conf import settings
from django.shortcuts import render

from accounts.models import Role, User


def _whatsapp_link():
    """
    A wa.me link with the message pre-typed.

    wa.me expects the number in international format with no punctuation, so a
    locally-written number is normalised here rather than in configuration.
    """
    number = "".join(ch for ch in settings.CLINIC.WHATSAPP_NUMBER if ch.isdigit())
    if not number:
        return ""
    if len(number) == 10:  # A bare Indian mobile number.
        number = f"91{number}"
    return f"https://wa.me/{number}?text={quote(settings.CLINIC.WHATSAPP_MESSAGE)}"


def home(request):
    doctors = (
        User.objects.filter(role=Role.DOCTOR, is_active=True)
        .select_related("doctor_profile")
        .order_by("first_name")
    )

    return render(request, "website/home.html", {
        "doctors": doctors,
        "services": settings.CLINIC.CONDITION_SUGGESTIONS,
        "whatsapp_url": _whatsapp_link(),
        "consulting_hours": settings.CLINIC.CONSULTING_HOURS_DISPLAY,
        "working_days": settings.CLINIC.WORKING_DAYS_DISPLAY,
    })
