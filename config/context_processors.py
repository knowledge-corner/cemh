"""Makes clinic branding available to every template without per-view plumbing."""

from django.conf import settings


def clinic_branding(request):
    clinic = settings.CLINIC
    return {
        "clinic_name": clinic.CLINIC_NAME,
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
