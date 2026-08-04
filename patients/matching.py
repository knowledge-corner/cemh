"""
Deciding whether two patient records are the same person.

A second record for somebody already registered does not merely make a mess: it
splits their history in two, and the doctor then reads half of it. For a clinic
following growth or thyroid control over years, that is a clinical risk rather
than a tidiness one.

The comparison is on first name, last name and phone together — the key the
ticket names. All three are normalised first, because the ways the same person
gets typed in twice are entirely predictable: a capital letter, a double space,
a country code, a leading zero.
"""

import re

from django.db.models import CharField, F, Func, Value

from .models import Patient

#: How many digits of a phone number identify it. Indian mobile numbers are ten;
#: anything before that is a country code or a trunk prefix, and the same phone
#: reaches the same person whichever of them was typed.
SIGNIFICANT_DIGITS = 10


def normalise_name(value):
    """
    Casefolded, with runs of whitespace collapsed.

    ``"  meera   KULKARNI "`` and ``"Meera Kulkarni"`` are one person.
    """
    return " ".join((value or "").split()).casefold()


def normalise_phone(value):
    """
    The digits that identify the number, without the decoration.

    ``+91 98200 12345``, ``098200-12345`` and ``9820012345`` are one telephone.
    Everything that is not a digit goes, then the last ten digits are what is
    compared — which drops a country code or a leading trunk zero without having
    to know which of the two it was.
    """
    digits = re.sub(r"\D", "", value or "")
    return digits[-SIGNIFICANT_DIGITS:] if digits else ""


def find_duplicates(first_name, last_name, phone, *, exclude_pk=None, limit=5):
    """
    Active patients who look like the person being registered.

    Name *and* number together, never either alone. Families share a mobile
    constantly here — a mother's number sits on three of her children's records
    — so matching on the number by itself would interrupt the ordinary case
    rather than the mistaken one.

    Narrowed in the database on the phone's digits, then the names compared in
    Python. That order matters: the phone is by far the more selective of the
    two, and an SQL name comparison cannot be made to ignore internal spacing
    without a second stored column — ``iexact`` would miss "Meera  Kulkarni"
    against "Meera Kulkarni", which the ticket names as a case to catch.

    ``regexp_replace`` is Postgres', which this project already requires for the
    exclusion constraint that prevents double-booking.
    """
    wanted = normalise_phone(phone)
    first = normalise_name(first_name)

    if not first or not wanted:
        return []

    candidates = Patient.objects.filter(is_active=True).annotate(
        phone_digits=Func(
            F("phone"), Value(r"\D"), Value(""), Value("g"),
            function="regexp_replace", output_field=CharField(),
        )
    ).filter(phone_digits__endswith=wanted)

    if exclude_pk is not None:
        candidates = candidates.exclude(pk=exclude_pk)

    last = normalise_name(last_name)
    matches = [
        patient for patient in candidates.order_by("patient_id")
        if normalise_name(patient.first_name) == first
        and normalise_name(patient.last_name) == last
    ]
    return matches[:limit]
