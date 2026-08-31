"""
The clinic's public page.

One page, no login. Patients do not book themselves in and nothing about a
patient's record is online — the page exists to explain what the clinic does and
then produce a telephone call, a WhatsApp message, or a request to be rung back.

Kept as its own app because the public face is the thing that differs most
between clinics; another clinic replaces this app and touches nothing clinical.
"""

from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render

from accounts.models import Role, User

from . import photos, seo
from .forms import CallbackForm

#: The conditions each half of the practice treats, as the public page groups
#: them. Here rather than in config/clinic.py because these are this page's
#: copy — the clinical suggestion list the booking form offers is a different
#: list for a different job, and one list serving both made both wrong.
ADULT_SERVICES = [
    "Diabetes and metabolic disorders",
    "Thyroid disorders",
    "Obesity and lipid disorders",
    "Pituitary and adrenal disorders",
    "Polycystic ovarian syndrome (PCOS)",
    "Reproductive endocrine disorders",
    "Calcium, vitamin D and bone disorders",
    "Endocrine hypertension",
    "Endocrine oncology",
]

PAEDIATRIC_SERVICES = [
    "Growth and short stature",
    "Early or delayed puberty",
    "Childhood diabetes",
    "Thyroid disorders in children",
    "Childhood obesity",
    "Bone and mineral disorders",
    "Adrenal and pituitary disorders",
    "Differences in sex development",
    "Late endocrine effects in cancer survivors",
]

FAQS = [
    (
        "Do I need a referral to book an appointment?",
        "No referral is required. You can telephone the clinic directly or "
        "send a WhatsApp message, and our team will find you a time.",
    ),
    (
        "What should I bring to my first visit?",
        "Please carry any previous prescriptions, lab reports, imaging, and a "
        "list of current medications. This helps the doctor build a complete "
        "picture of your history from the first consultation.",
    ),
    (
        "Do you treat both children and adults for diabetes and thyroid conditions?",
        "Yes. The clinic has adult and paediatric endocrinology together, "
        "covering diabetes, thyroid disorders, and growth and puberty concerns "
        "in children.",
    ),
    (
        "Where exactly is the clinic located?",
        "CEMH is on the 2nd Floor of Seatherny Hospital, Borivali West, Mumbai "
        "- 400092, giving you access to the hospital's labs and diagnostic "
        "facilities alongside your consultation.",
    ),
    (
        "How do I reach the clinic for appointments?",
        "Telephone the clinic on the number at the top of this page, or send "
        "a WhatsApp message. You can also ring either doctor directly on the "
        "numbers beside their names.",
    ),
]


def _whatsapp_link(number, message=None):
    """
    A wa.me link with the message pre-typed.

    wa.me expects the number in international format with no punctuation, so a
    locally-written number is normalised here rather than in configuration.
    Shared by the clinic-wide button and each doctor's own contact card.
    """
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    if not digits:
        return ""
    if len(digits) == 10:  # A bare Indian mobile number.
        digits = f"91{digits}"
    text = message if message is not None else settings.CLINIC.WHATSAPP_MESSAGE
    return f"https://wa.me/{digits}?text={quote(text)}"


def _doctors():
    """
    The doctors to introduce, each with their photograph if one exists.

    Doctors who have not yet set their password are included. Somebody who has
    been appointed is a doctor at this clinic whether or not they have answered
    an email, and dropping them off the front page until they do would be an
    odd thing for the public to be told.
    """
    people = (
        User.objects.filter(role=Role.DOCTOR, is_active=True)
        .select_related("doctor_profile", "doctor_profile__specialisation")
        .order_by("first_name")
    )

    introduced = []
    for doctor in people:
        profile = getattr(doctor, "doctor_profile", None)
        bio = (getattr(profile, "bio", "") or "").strip()
        qualification = (getattr(profile, "qualification", "") or "").strip()
        areas = (getattr(profile, "areas_of_focus", "") or "").strip()
        training = (getattr(profile, "further_training", "") or "").strip()
        introduced.append({
            "doctor": doctor,
            "profile": profile,
            "photo_url": photos.photo_url(doctor),
            "initials": photos.initials(doctor),
            # Split on blank lines so a bio reads as paragraphs rather than one
            # unbroken wall, without the template needing to know how many.
            "paragraphs": [p.strip() for p in bio.split("\n\n") if p.strip()],
            # The single "MBBS · DNB (Medicine)" line is kept for the summary
            # under the doctor's name; split here too so the detail card can
            # list the same credentials as bullets without a second field.
            "qualification_list": [q.strip() for q in qualification.split("·") if q.strip()],
            "areas_of_focus": [a.strip() for a in areas.splitlines() if a.strip()],
            "further_training": [t.strip() for t in training.splitlines() if t.strip()],
            "whatsapp_url": _whatsapp_link(doctor.phone),
        })
    return introduced


def home(request):
    if request.method == "POST":
        form = CallbackForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Thank you — we have your number and will ring you back. "
                "If it is urgent, please telephone the clinic directly.",
            )
            # Redirect rather than re-render, so a refresh cannot send the same
            # request twice and leave reception ringing one person over and over.
            return redirect(f"{request.path}#contact")
    else:
        form = CallbackForm()

    doctors = _doctors()

    # One sentence, written once, used as the meta description, the sharing
    # preview and the page's structured description. Three copies of nearly the
    # same sentence is how they end up contradicting each other.
    description = (
        f"{settings.CLINIC.CLINIC_TAGLINE} in {settings.CLINIC.CLINIC_CITY}. "
        f"Specialist care for diabetes, thyroid, growth, puberty, obesity and "
        f"bone health, for adults and children. "
        f"Call {settings.CLINIC.CLINIC_PHONE} to book."
    )

    return render(request, "website/home.html", {
        "doctors": doctors,
        "adult_services": ADULT_SERVICES,
        "paediatric_services": PAEDIATRIC_SERVICES,
        "faqs": FAQS,
        "form": form,
        "meta_description": description,
        "structured_data": seo.structured_data(
            request,
            doctors=doctors,
            services=ADULT_SERVICES + PAEDIATRIC_SERVICES,
            faqs=FAQS,
            description=description,
        ),
        **seo.meta(request, description=description),
        "whatsapp_url": _whatsapp_link(settings.CLINIC.WHATSAPP_NUMBER),
        "maps_url": (
            "https://www.google.com/maps/search/?api=1&query="
            + quote(settings.CLINIC.CLINIC_ADDRESS)
        ),
        # An inline embed, not just the link-out above — a visitor sees exactly
        # where the clinic is without leaving the page. output=embed is the
        # key-free Maps embed; no API key to manage or bill for.
        "maps_embed_url": (
            "https://www.google.com/maps?q="
            + quote(settings.CLINIC.CLINIC_ADDRESS)
            + "&output=embed"
        ),
        # Never on a laptop running the dev server or the test suite — that
        # traffic is not a real visitor, and it would sit in the same GA4
        # property as the clinic's actual numbers with no way to tell them apart.
        "ga_measurement_id": "" if settings.DEBUG else settings.CLINIC.GA_MEASUREMENT_ID,
    })
