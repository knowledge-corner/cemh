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

# ── The same address, in parts ────────────────────────────────────────────────
#
# CLINIC_ADDRESS above is what a human reads. A search engine wants the pieces
# separately, and splitting the one-line version on commas guesses wrongly the
# moment a clinic writes its address any other way. So the parts are stated,
# and the one-line version stays exactly as the clinic writes it.

CLINIC_STREET = os.environ.get(
    "CLINIC_STREET",
    "2nd Floor, Seatherny Hospital, Sodawala Lane, Nutan Nagar, Borivali West",
)
CLINIC_CITY = os.environ.get("CLINIC_CITY", "Mumbai")
CLINIC_REGION = os.environ.get("CLINIC_REGION", "Maharashtra")
CLINIC_POSTCODE = os.environ.get("CLINIC_POSTCODE", "400092")
CLINIC_COUNTRY = os.environ.get("CLINIC_COUNTRY", "IN")

# Map pin, for the "where is this" panel Google shows beside a local business.
#
# **Deliberately empty.** Nobody has surveyed this building, and a guessed
# coordinate is worse than none: it puts a pin on a map that patients then drive
# to. Read them off Google Maps for the real doorway and set them here.
CLINIC_LATITUDE = os.environ.get("CLINIC_LATITUDE", "")
CLINIC_LONGITUDE = os.environ.get("CLINIC_LONGITUDE", "")

# ── Public site ───────────────────────────────────────────────────────────────

# The address the public page lives at, with no trailing slash — used for the
# canonical link, the sharing preview and the sitemap, all of which need an
# absolute URL rather than a path.
#
# Empty means "work it out from the request", which is right on a laptop and
# wrong behind a proxy that rewrites Host. Set it in production.
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")

# Profiles that are unambiguously this clinic — a Google Business listing, a
# Practo page, Instagram. Emitted as schema.org `sameAs`, which is how a search
# engine ties them to one another. One per line or comma-separated.
#
# Empty by default. Listing a page the clinic does not control would attach
# somebody else's reputation to this one.
SOCIAL_PROFILES = tuple(
    url.strip()
    for url in os.environ.get("SOCIAL_PROFILES", "").replace("\n", ",").split(",")
    if url.strip()
)

# Where the public page's WhatsApp button sends people. Defaults to the clinic
# number rather than either doctor's mobile — change if appointment requests
# should reach a doctor directly.
WHATSAPP_NUMBER = os.environ.get("WHATSAPP_NUMBER", CLINIC_PHONE)
WHATSAPP_MESSAGE = os.environ.get(
    "WHATSAPP_MESSAGE",
    "Hello, I would like to book an appointment at the "
    "Centre for Endocrine & Metabolic Health.",
)

# The public page's GA4 property. Blank means no tracking script is written at
# all, not a script that fires with no destination — a clinic without its own
# property should not be sending traffic into someone else's.
#
# Never fires when DEBUG is on (see website/views.py), so a laptop running the
# test suite or a developer's local server cannot pollute production analytics.
GA_MEASUREMENT_ID = os.environ.get("GA_MEASUREMENT_ID", "G-VXWDCFRJHN")

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

# ── Consulting hours ──────────────────────────────────────────────────────────
#
# Not consulted for scheduling any more — a doctor's hours are read straight
# off the calendar (appointments.models.DoctorSchedule); a day they have no
# entry for is a day they are not working, full stop. What is left here feeds
# only the public website's SEO structured data (website/seo.py's
# OpeningHoursSpecification), which needs a machine-readable day list and
# times rather than the free text in *_DISPLAY below.
WORKING_DAYS = tuple(
    int(d) for d in os.environ.get("WORKING_DAYS", "0,1,2,3,4,5,6").split(",") if d.strip()
)
CONSULTING_START = os.environ.get("CONSULTING_START", "10:00")
CONSULTING_END = os.environ.get("CONSULTING_END", "18:00")

# Length of one appointment slot, in minutes.
SLOT_MINUTES = int(os.environ.get("SLOT_MINUTES", "20"))

# How many days ahead reception may book.
BOOKING_HORIZON_DAYS = int(os.environ.get("BOOKING_HORIZON_DAYS", "45"))

# How long a doctor's set-password invitation stays usable.
#
# Seven days rather than twenty-four hours: a doctor invited on a Friday should
# not find the link dead on Monday, and reception can always re-send. Short
# enough that an old link in a mailbox is not a standing way in. The ticket
# leaves this open — see the note on KAN-21.
INVITATION_DAYS = int(os.environ.get("INVITATION_DAYS", "7"))

# Shown on the public page. Kept as plain text rather than derived from the
# times above, because what a clinic advertises and what it schedules are not
# always the same thing (lunch breaks, alternating doctors, and so on).
CONSULTING_HOURS_DISPLAY = os.environ.get("CONSULTING_HOURS_DISPLAY", "10:00 am – 6:00 pm")
WORKING_DAYS_DISPLAY = os.environ.get("WORKING_DAYS_DISPLAY", "Open all week")

# ── Billing ───────────────────────────────────────────────────────────────────

CURRENCY_SYMBOL = os.environ.get("CURRENCY_SYMBOL", "₹")
DEFAULT_CONSULTATION_FEE = os.environ.get("DEFAULT_CONSULTATION_FEE", "800")

# Whether the clinic day has to be signed off (KAN-48, KAN-49).
#
# **Off, at the clinic's request (again).** It was on for a while once the
# alert/worklist design was fixed, but the clinic asked for the whole
# "Unclosed appointments" tab, its board alert and buttons, and the sign-off
# action itself to stop appearing for now. Nothing was removed to do this —
# see appointments.signoff.is_enabled().
#
# Off, nothing about sign-offs appears: no alert, no held-up arrivals, no
# button. The end-of-day sweep that clears yesterday's leftovers still works,
# because that predates all of this and the board needs it.
#
# Set to "1" to switch it back on.
DAY_SIGN_OFF_ENABLED = os.environ.get("DAY_SIGN_OFF_ENABLED", "0") == "1"

# Whether the sign-off tries to email the day sheet at all.
#
# **Off, temporarily.** There is no mail server configured yet, so every
# sign-off ended with a warning about a report that could not be sent — noise
# on the one action the receptionist is being asked to perform daily, and noise
# she cannot do anything about.
#
# Off, the day still sweeps, still records, still locks, and simply says so.
# The report is built and thrown away rather than skipped, so the code that
# assembles it keeps running and keeps being tested: a reporting path that has
# not executed for a month is a reporting path that no longer works.
#
# Turn this on with EMAIL_HOST, at which point SIGN_OFF_EMAILS below applies.
SIGN_OFF_EMAIL_ENABLED = os.environ.get("SIGN_OFF_EMAIL_ENABLED", "0") == "1"

# Who receives the end-of-day sheet (KAN-48). Comma-separated.
#
# This report carries patient names against amounts, so the address matters:
# it must be a mailbox the clinic itself controls, never a personal or shared
# one. contact@cemhcare.com is the clinic's own, which is why it is set here
# rather than left to be guessed.
#
# Another clinic taking this codebase should change it or clear it. Cleared,
# the day still signs off — the clinic is never blocked by this — and the
# record says the report was not sent, so it is visible rather than silently
# lost.
SIGN_OFF_EMAILS = os.environ.get("SIGN_OFF_EMAILS", "contact@cemhcare.com")

# ── Clinical defaults ─────────────────────────────────────────────────────────

# A patient is offered growth charts below this age, in years.
PAEDIATRIC_AGE_LIMIT = int(os.environ.get("PAEDIATRIC_AGE_LIMIT", "18"))

# Growth reference standard: "IAP", "WHO" or "CDC".
#
# "IAP" is what this clinic charts against — WHO standards below five years,
# then the Indian Academy of Paediatrics 2015 revised charts for 5-18, which is
# IAP's own published recommendation for Indian children.
#
# The IAP tables are NOT yet installed. Until they are, ages above five fall
# through to CDC and every chart states the source that actually produced it,
# so the fallback is visible rather than silent. See
# growth/reference/SOURCES.md for how to install them.
GROWTH_REFERENCE = os.environ.get("GROWTH_REFERENCE", "IAP")
