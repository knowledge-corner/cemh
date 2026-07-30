"""
Per-clinic customisation.

This is the ONLY Python file a clinic-specific copy of this codebase is expected
to change. Everything here is branding, identity or feature selection — never
application logic. Keeping clinic differences confined to this file (plus
``static/css/theme.css`` and ``templates/branding/``) is what allows a clinic
deployment to keep merging fixes from the core repository.

Values may be overridden by environment variables so a single image can be
re-pointed without a rebuild.
"""

import os

# ── Identity ──────────────────────────────────────────────────────────────────

CLINIC_NAME = os.environ.get("CLINIC_NAME", "Centre for Endocrine & Metabolic Health")
CLINIC_SHORT_NAME = os.environ.get("CLINIC_SHORT_NAME", "CEMH")
CLINIC_TAGLINE = os.environ.get(
    "CLINIC_TAGLINE", "Adult & Paediatric Endocrinology"
)
CLINIC_ADDRESS = os.environ.get(
    "CLINIC_ADDRESS",
    "2nd Floor, Seatherny Hospital, Sodawala Lane, Nutan Nagar, "
    "Borivali West, Mumbai - 400092",
)
CLINIC_PHONE = os.environ.get("CLINIC_PHONE", "7045032951")
CLINIC_EMAIL = os.environ.get("CLINIC_EMAIL", "")

# ── Patient identifiers ───────────────────────────────────────────────────────

# The clinic's paper file calls this the UHID (Unique Health Identification
# number), so that is the label used throughout the interface. The database
# field is still `patient_id`.
PATIENT_ID_LABEL = os.environ.get("PATIENT_ID_LABEL", "UHID")

# UHIDs are formatted "<PREFIX>-<YY>-<NNNNN>", e.g. "CEMH-26-00137".
# Changing the prefix affects only IDs issued from that point on; existing
# UHIDs are immutable.
PATIENT_ID_PREFIX = os.environ.get("PATIENT_ID_PREFIX", "CEMH")

# ── Optional feature modules ──────────────────────────────────────────────────
#
# Speciality features are self-contained Django apps. A clinic that does not
# need one simply drops it from this tuple: the app's models, URLs, admin and
# dashboard tab all disappear together, with no other edits required.
#
#   "growth" — anthropometry and paediatric growth charts.
#              Essential for this clinic's paediatric endocrinology practice;
#              irrelevant to, say, an orthopaedic clinic.
#
OPTIONAL_APPS = tuple(
    app.strip()
    for app in os.environ.get("OPTIONAL_APPS", "growth").split(",")
    if app.strip()
)

# ── Clinical defaults ─────────────────────────────────────────────────────────

# A patient is offered growth charts below this age, in years.
PAEDIATRIC_AGE_LIMIT = int(os.environ.get("PAEDIATRIC_AGE_LIMIT", "18"))

# Growth reference standard: "WHO", "IAP" or "CDC".
# See growth/reference/SOURCES.md — WHO data ships for 0-5 years and CDC for
# 2-20 years. IAP 2015 (Indian Academy of Paediatrics) tables must be added
# before selecting "IAP".
GROWTH_REFERENCE = os.environ.get("GROWTH_REFERENCE", "WHO")

# Conditions this clinic treats, taken from its own services list. Used to
# offer sensible diagnosis choices rather than a free-text box every time.
CONDITION_SUGGESTIONS = {
    "Adult endocrinology": [
        "Diabetes and metabolic disorders",
        "Thyroid disorders",
        "Obesity and lipid disorders",
        "Pituitary and adrenal disorders",
        "Polycystic ovarian syndrome (PCOS)",
        "Reproductive endocrine disorders",
        "Calcium, vitamin D and bone disorders",
        "Endocrine hypertension",
        "Endocrine oncology",
    ],
    "Paediatric endocrinology": [
        "Growth and short stature",
        "Early or delayed puberty",
        "Childhood diabetes",
        "Thyroid disorders in children",
        "Childhood obesity",
        "Bone and mineral disorders",
        "Adrenal and pituitary disorders",
        "Differences in sex development",
        "Late endocrine effects of cancer survivors",
    ],
}
