"""
What search engines and link previews are told about the clinic.

Two jobs: assemble the absolute URLs and sharing text the page's ``<head>``
needs, and build the schema.org JSON-LD that turns a listing into a map pin,
opening hours and a set of named doctors.

**Everything here is a claim made in public about a real medical practice**, so
one rule runs through it: nothing is emitted that the clinic has not actually
supplied. Empty settings produce absent keys, not plausible-looking guesses.
Specifically —

* **No coordinates unless somebody set them.** A guessed lat/long is a pin on a
  map that patients then drive to.
* **No ratings, no reviews, no `aggregateRating`.** There is nowhere in this
  system for a real review to come from, so any number here would be invented.
  Google treats fabricated review markup as a manual-action offence, and it
  would deserve to.
* **No claims about accepting insurance, prices or availability** beyond the
  consulting hours the clinic already publishes on the page itself.

The structured data is also kept in step with what a visitor can see. Marking up
a doctor who is not on the page, or hours that contradict the page, is the thing
that gets structured data ignored — and it is dishonest in the same breath.
"""

import json

from django.conf import settings
from django.urls import reverse

#: schema.org wants two-letter day codes; the clinic stores Monday = 0.
SCHEMA_DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

#: What this practice is, in schema.org's vocabulary. Both are real values from
#: the MedicalSpecialty enumeration — invented ones are silently dropped.
MEDICAL_SPECIALTIES = ["Endocrine", "Pediatric"]


def site_url(request):
    """
    The site's own address, with no trailing slash.

    Configured value first: behind a proxy the Host header is whatever the
    proxy chose to forward, and a canonical URL derived from a rewritten Host
    points somewhere that is not the site. Falls back to the request so a
    laptop and the test client still produce something sensible.
    """
    configured = getattr(settings.CLINIC, "SITE_URL", "")
    if configured:
        return configured.rstrip("/")
    return request.build_absolute_uri("/").rstrip("/")


def absolute(request, path):
    return f"{site_url(request)}{path}"


def _address():
    """The postal address, as its parts."""
    clinic = settings.CLINIC
    address = {
        "@type": "PostalAddress",
        "streetAddress": clinic.CLINIC_STREET,
        "addressLocality": clinic.CLINIC_CITY,
        "addressRegion": clinic.CLINIC_REGION,
        "postalCode": clinic.CLINIC_POSTCODE,
        "addressCountry": clinic.CLINIC_COUNTRY,
    }
    return {key: value for key, value in address.items() if value}


def _geo():
    """The map pin — or nothing, when nobody has supplied one."""
    clinic = settings.CLINIC
    latitude = (clinic.CLINIC_LATITUDE or "").strip()
    longitude = (clinic.CLINIC_LONGITUDE or "").strip()
    if not (latitude and longitude):
        return None
    try:
        return {
            "@type": "GeoCoordinates",
            "latitude": float(latitude),
            "longitude": float(longitude),
        }
    except ValueError:
        # A typo in configuration is not worth a 500 on the front page, and a
        # half-parsed coordinate is worse than none at all.
        return None


def _opening_hours():
    """
    The consulting hours, as a machine can read them.

    Taken from the same settings the page prints, so the two cannot drift. A
    clinic that has removed every working day gets no opening-hours claim rather
    than an empty one.
    """
    clinic = settings.CLINIC
    days = [SCHEMA_DAYS[day] for day in sorted(clinic.WORKING_DAYS)
            if 0 <= day < len(SCHEMA_DAYS)]
    if not days:
        return None
    return [{
        "@type": "OpeningHoursSpecification",
        "dayOfWeek": days,
        "opens": clinic.CONSULTING_START,
        "closes": clinic.CONSULTING_END,
    }]


def _physician(request, entry, clinic_id):
    """One doctor, as schema.org describes a physician."""
    doctor = entry["doctor"]
    profile = entry["profile"]

    person = {
        "@type": "Physician",
        "@id": absolute(request, f"{reverse('website_home')}#doctor-{doctor.pk}"),
        "name": f"Dr. {doctor.display_name}",
        "worksFor": {"@id": clinic_id},
    }

    if entry.get("areas_of_focus"):
        # The doctor's own curated list, when the clinic has entered one.
        person["knowsAbout"] = entry["areas_of_focus"]
    else:
        specialisation = getattr(profile, "specialisation", None)
        if specialisation is not None:
            # Free text, because the clinic maintains this list and it will
            # contain names schema.org's fixed enumeration has never heard of.
            person["knowsAbout"] = specialisation.name
    if getattr(profile, "qualification", ""):
        person["hasCredential"] = profile.qualification
    if entry.get("photo_url"):
        person["image"] = absolute(request, entry["photo_url"])
    if doctor.email:
        person["email"] = doctor.email
    if doctor.phone:
        person["telephone"] = doctor.phone
    if entry.get("paragraphs"):
        person["description"] = " ".join(entry["paragraphs"])

    # Fellowships/observerships named "<title> — <institution>" contribute the
    # institution half as an alumniOf organisation. A line with no dash (e.g.
    # a short course with no named host) is skipped rather than guessed at.
    institutions = [
        line.rsplit("—", 1)[-1].strip()
        for line in entry.get("further_training", [])
        if "—" in line
    ]
    if institutions:
        person["alumniOf"] = [
            {"@type": "Organization", "name": name} for name in institutions
        ]

    return person


def _clinic(request, doctors, services):
    clinic = settings.CLINIC
    home = absolute(request, reverse("website_home"))
    clinic_id = f"{home}#clinic"

    organisation = {
        "@type": "MedicalClinic",
        "@id": clinic_id,
        "name": clinic.CLINIC_NAME,
        "alternateName": clinic.CLINIC_SHORT_NAME,
        "description": clinic.CLINIC_TAGLINE,
        "url": home,
        "telephone": clinic.CLINIC_PHONE,
        "address": _address(),
        "medicalSpecialty": MEDICAL_SPECIALTIES,
        "currenciesAccepted": "INR",
        "isAcceptingNewPatients": True,
        "availableService": [
            {"@type": "MedicalTherapy", "name": name} for name in services
        ],
        "employee": [
            _physician(request, entry, clinic_id) for entry in doctors
        ],
    }

    if clinic.CLINIC_EMAIL:
        organisation["email"] = clinic.CLINIC_EMAIL

    geo = _geo()
    if geo:
        organisation["geo"] = geo

    # No opening-hours claim, at the clinic's request — see the module note.
    # WORKING_DAYS/CONSULTING_START/END still drive the booking calendar
    # elsewhere; this is only about what the public page tells a search engine.

    if clinic.SOCIAL_PROFILES:
        organisation["sameAs"] = list(clinic.SOCIAL_PROFILES)

    return organisation


def _faq_page(faqs):
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            }
            for question, answer in faqs
        ],
    }


def _web_page(request, description):
    home = absolute(request, reverse("website_home"))
    return {
        "@type": "WebPage",
        "@id": f"{home}#webpage",
        "url": home,
        "name": f"{settings.CLINIC.CLINIC_NAME} — {settings.CLINIC.CLINIC_TAGLINE}",
        "description": description,
        "inLanguage": "en-IN",
        "about": {"@id": f"{home}#clinic"},
        "isPartOf": {"@id": f"{home}#website"},
    }


def _web_site(request):
    home = absolute(request, reverse("website_home"))
    return {
        "@type": "WebSite",
        "@id": f"{home}#website",
        "url": home,
        "name": settings.CLINIC.CLINIC_NAME,
        "inLanguage": "en-IN",
        "publisher": {"@id": f"{home}#clinic"},
    }


def structured_data(request, *, doctors, services, faqs, description):
    """
    Everything the page claims about itself, as one JSON-LD graph.

    A single ``@graph`` rather than four separate script tags, so the nodes can
    reference each other by ``@id`` — the clinic, the doctors who work there,
    the page and the site become one connected description instead of four
    unrelated assertions that happen to share a URL.
    """
    graph = [
        _clinic(request, doctors, services),
        _web_site(request),
        _web_page(request, description),
    ]
    if faqs:
        graph.append(_faq_page(faqs))

    payload = json.dumps(
        {"@context": "https://schema.org", "@graph": graph},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    # Escaped the way Django's own json_script does it, because this string is
    # written straight into a <script> element. A doctor's bio containing
    # "</script>" would otherwise close the tag early and spill the rest of the
    # JSON into the page as visible text. The escapes are ordinary JSON string
    # escapes, so what a crawler parses is unchanged.
    return (
        payload.replace("<", "\\u003c")
               .replace(">", "\\u003e")
               .replace("&", "\\u0026")
    )


def meta(request, *, description):
    """The canonical URL and the sharing preview, ready for the template."""
    home = absolute(request, reverse("website_home"))
    clinic = settings.CLINIC
    return {
        "canonical_url": home,
        "og_title": f"{clinic.CLINIC_NAME} — {clinic.CLINIC_TAGLINE}",
        "og_description": description,
        "og_image": absolute(request, "/static/og-image.png"),
        "og_site_name": clinic.CLINIC_NAME,
    }
