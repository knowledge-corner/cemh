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

CLINIC_NAME = os.environ.get("CLINIC_NAME", "Endocrine & Diabetes Clinic")
CLINIC_TAGLINE = os.environ.get(
    "CLINIC_TAGLINE", "Adult & Paediatric Endocrinology"
)
CLINIC_ADDRESS = os.environ.get("CLINIC_ADDRESS", "")
CLINIC_PHONE = os.environ.get("CLINIC_PHONE", "")
CLINIC_EMAIL = os.environ.get("CLINIC_EMAIL", "")

# ── Patient identifiers ───────────────────────────────────────────────────────

# Patient IDs are formatted "<PREFIX>-<YY>-<NNNNN>", e.g. "KEC-26-00137".
# Changing the prefix affects only IDs issued from that point on; existing
# patient IDs are immutable.
PATIENT_ID_PREFIX = os.environ.get("PATIENT_ID_PREFIX", "KEC")

# ── Optional feature modules ──────────────────────────────────────────────────
#
# Speciality features are self-contained Django apps. A clinic that does not
# need one simply drops it from this tuple: the app's models, URLs, admin and
# dashboard tab all disappear together, with no other edits required.
#
#   "growth" — anthropometry and paediatric growth charts.
#              Wanted by paediatricians and endocrinologists; irrelevant to,
#              say, an orthopaedic clinic.
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
# WHO ships with this repository. IAP 2015 (Indian Academy of Paediatrics)
# tables must be added to growth/reference/ before selecting "IAP".
GROWTH_REFERENCE = os.environ.get("GROWTH_REFERENCE", "WHO")
