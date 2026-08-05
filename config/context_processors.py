"""Makes clinic branding available to every template without per-view plumbing."""

from django.conf import settings

from accounts.models import Role


def clinic_branding(request):
    clinic = settings.CLINIC
    return {
        "clinic_name": clinic.CLINIC_NAME,
        # The initials the clinic uses about itself. The public page gives the
        # full name once and then this, which is how the clinic actually writes.
        "clinic_short_name": clinic.CLINIC_SHORT_NAME,
        "clinic_tagline": clinic.CLINIC_TAGLINE,
        "clinic_address": clinic.CLINIC_ADDRESS,
        "clinic_phone": clinic.CLINIC_PHONE,
        "clinic_email": clinic.CLINIC_EMAIL,
        # The clinic calls the patient identifier a UHID; templates use this
        # label so another clinic can call it something else.
        "patient_id_label": clinic.PATIENT_ID_LABEL,
        "clinic_currency": clinic.CURRENCY_SYMBOL,
        # Lets templates hide UI belonging to a speciality app this clinic
        # has not enabled, without importing the app.
        "growth_enabled": "growth" in settings.INSTALLED_APPS,
    }


def outstanding_callbacks(request):
    """
    How many people are waiting for the clinic to ring them back.

    Counted only for a signed-in receptionist. The public page has no use for
    it and would be paying for a query on every visit, and it is not a doctor's
    job — so neither is asked.
    """
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return {}
    if not (user.is_superuser or user.role == Role.RECEPTIONIST):
        return {}

    from website.models import CallbackRequest, CallbackStatus

    return {
        "outstanding_callbacks": CallbackRequest.objects.filter(
            status=CallbackStatus.NEW
        ).count(),
    }
