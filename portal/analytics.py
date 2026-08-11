"""
Doctor-facing research filters and aggregates, backing the Analytics tab.

One filtered patient set, two destinations: the doctor either writes it out
as a CSV for her own analysis, or reads it as the dashboard's stat tiles and
charts. Kept in one module so the two screens cannot end up disagreeing about
what a given combination of filters actually means. Every filter is optional —
leaving all of them blank is "every patient the clinic has", which is the
unfiltered view the dashboard and the CSV both start from.
"""

from datetime import date
from urllib.parse import urlencode

from django.db.models import Count, Q
from django.db.models.functions import TruncMonth

from accounts.models import Specialisation, Role, User
from appointments.models import Visit, VisitStatus
from clinical.models import Diagnosis
from patients.models import Patient, Sex

#: (low, high, label) — high is inclusive. The last bucket has no real
#: ceiling; 120 just has to be past any human age.
AGE_BUCKETS = [
    (0, 12, "0–12"),
    (13, 17, "13–17"),
    (18, 30, "18–30"),
    (31, 45, "31–45"),
    (46, 60, "46–60"),
    (61, 120, "61+"),
]


def _shift_years(on, years):
    """``on`` minus whole ``years``, landing on the 28th in a leap-day corner case."""
    try:
        return on.replace(year=on.year - years)
    except ValueError:
        return on.replace(year=on.year - years, day=28)


def parse_filters(get):
    """Read the shared querystring into a plain dict — used by both screens."""

    def _int(name):
        raw = (get.get(name) or "").strip()
        return int(raw) if raw.isdigit() else None

    def _date(name):
        raw = (get.get(name) or "").strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    return {
        "condition": (get.get("condition") or "").strip(),
        "diagnosis_status": (get.get("diagnosis_status") or "").strip(),
        "sex": (get.get("sex") or "").strip(),
        "age_min": _int("age_min"),
        "age_max": _int("age_max"),
        "date_from": _date("date_from"),
        "date_to": _date("date_to"),
        "doctor": _int("doctor"),
        "specialisation": _int("specialisation"),
    }


#: Every filter key the querystring carries, in one place so the "switch tab
#: without losing my filters" link and the parser can't drift apart.
FILTER_KEYS = (
    "condition", "diagnosis_status", "sex", "age_min", "age_max",
    "date_from", "date_to", "doctor", "specialisation",
)


def has_any_filter(chosen):
    return any(chosen.values())


def filter_querystring(get):
    """The chosen filters as a query string, for links that must carry them
    across screens — switching the Download/Dashboard sub-tab, or jumping to
    the CSV download — without the doctor having to set them twice."""
    pairs = [(key, get.get(key)) for key in FILTER_KEYS if (get.get(key) or "").strip()]
    return urlencode(pairs)


def filtered_patients(chosen):
    """The patient set every other number on this page is drawn from."""
    patients = Patient.objects.all()

    if chosen["sex"]:
        patients = patients.filter(sex=chosen["sex"])

    today = date.today()
    if chosen["age_min"] is not None:
        # At least age_min years old today means born on or before this date.
        patients = patients.filter(date_of_birth__lte=_shift_years(today, chosen["age_min"]))
    if chosen["age_max"] is not None:
        # At most age_max years old means born after the day they'd have
        # turned age_max + 1.
        patients = patients.filter(date_of_birth__gt=_shift_years(today, chosen["age_max"] + 1))

    if chosen["condition"] or chosen["diagnosis_status"]:
        # Both conditions land in one .filter() call on purpose, so they are
        # matched against the *same* joined diagnosis row rather than "has a
        # diagnosis matching the text and (maybe a different one) matching
        # the status".
        diagnosis_filter = Q()
        if chosen["condition"]:
            diagnosis_filter &= Q(diagnoses__description__icontains=chosen["condition"])
        if chosen["diagnosis_status"]:
            diagnosis_filter &= Q(diagnoses__status=chosen["diagnosis_status"])
        patients = patients.filter(diagnosis_filter)

    if chosen["doctor"] or chosen["specialisation"] or chosen["date_from"] or chosen["date_to"]:
        visit_filter = Q()
        if chosen["doctor"]:
            visit_filter &= Q(visits__doctor_id=chosen["doctor"])
        if chosen["specialisation"]:
            visit_filter &= Q(
                visits__doctor__doctor_profile__specialisation_id=chosen["specialisation"]
            )
        if chosen["date_from"]:
            visit_filter &= Q(visits__scheduled_start__date__gte=chosen["date_from"])
        if chosen["date_to"]:
            visit_filter &= Q(visits__scheduled_start__date__lte=chosen["date_to"])
        patients = patients.filter(visit_filter)

    return patients.distinct()


def visits_for(patients, chosen):
    """
    Visits belonging to the filtered patients.

    Narrowed again by doctor/specialisation/date: a patient can match the
    filters through one visit with Dr Rao and still have unrelated visits with
    somebody else, and those must not count towards "Dr Rao's visits over
    time".
    """
    visits = Visit.objects.filter(patient__in=patients).select_related(
        "patient", "doctor", "doctor__doctor_profile__specialisation"
    )
    if chosen["doctor"]:
        visits = visits.filter(doctor_id=chosen["doctor"])
    if chosen["specialisation"]:
        visits = visits.filter(doctor__doctor_profile__specialisation_id=chosen["specialisation"])
    if chosen["date_from"]:
        visits = visits.filter(scheduled_start__date__gte=chosen["date_from"])
    if chosen["date_to"]:
        visits = visits.filter(scheduled_start__date__lte=chosen["date_to"])
    return visits


#: The records table caps out here — the same reason bookings.html caps its
#: completed list at 300: a doctor with years of patients should not turn the
#: page into an unbounded query. The CSV export has no such limit.
RECORD_ROWS_LIMIT = 200


def patient_rows(patients, limit=RECORD_ROWS_LIMIT):
    """
    One row per patient for the View tab's table — the same fields the CSV
    export writes, computed once here so the two cannot drift apart.
    """
    rows = []
    for patient in patients.order_by("last_name", "first_name")[:limit]:
        diagnoses = ", ".join(
            d.description for d in patient.diagnoses.filter(status="ACTIVE")[:5]
        )
        patient_visits = patient.visits.order_by("-scheduled_start")
        last_visit = patient_visits.first()
        rows.append({
            "patient": patient,
            "diagnoses": diagnoses,
            "visit_count": patient_visits.count(),
            "last_visit": last_visit,
        })
    return rows


def filter_options():
    """Choices for the filter form's dropdowns."""
    return {
        "doctors": User.objects.filter(role=Role.DOCTOR, is_active=True),
        "specialisations": Specialisation.objects.filter(is_active=True),
        "sex_choices": Sex.choices,
        "diagnosis_status_choices": Diagnosis.Status.choices,
    }


def _age_distribution(patients):
    dobs = patients.values_list("date_of_birth", flat=True)
    counts = [0] * len(AGE_BUCKETS)
    today = date.today()
    for born in dobs:
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        for i, (low, high, _label) in enumerate(AGE_BUCKETS):
            if low <= age <= high:
                counts[i] += 1
                break
    total = sum(counts) or 1
    return [
        {"label": label, "count": count, "pct": round(count * 100 / total)}
        for (_low, _high, label), count in zip(AGE_BUCKETS, counts)
    ]


def _sex_distribution(patients):
    rows = patients.values("sex").annotate(n=Count("id")).order_by("-n")
    by_sex = {row["sex"]: row["n"] for row in rows}
    total = sum(by_sex.values()) or 1
    return [
        {"label": label, "count": by_sex.get(value, 0), "pct": round(by_sex.get(value, 0) * 100 / total)}
        for value, label in Sex.choices
    ]


def _top_conditions(patients, chosen, limit=10):
    diagnoses = Diagnosis.objects.filter(patient__in=patients)
    if chosen["diagnosis_status"]:
        diagnoses = diagnoses.filter(status=chosen["diagnosis_status"])
    rows = (
        diagnoses.values("description")
        .annotate(n=Count("id"))
        .order_by("-n", "description")[:limit]
    )
    top = max((row["n"] for row in rows), default=0) or 1
    return [
        {"label": row["description"], "count": row["n"], "pct": round(row["n"] * 100 / top)}
        for row in rows
    ]


def _visits_over_time(visits, limit=12):
    rows = (
        visits.annotate(month=TruncMonth("scheduled_start"))
        .values("month")
        .annotate(n=Count("id"))
        .order_by("month")
    )
    rows = list(rows)[-limit:]
    top = max((row["n"] for row in rows), default=0) or 1
    return [
        {"label": row["month"].strftime("%b %Y"), "count": row["n"], "pct": round(row["n"] * 100 / top)}
        for row in rows
    ]


def _visit_outcomes(visits):
    rows = visits.values("status").annotate(n=Count("id")).order_by("-n")
    by_status = {row["status"]: row["n"] for row in rows}
    total = sum(by_status.values()) or 1
    return [
        {"label": label, "count": by_status.get(value, 0), "pct": round(by_status.get(value, 0) * 100 / total)}
        for value, label in VisitStatus.choices
        if by_status.get(value, 0)
    ]


def dashboard_stats(patients, visits, chosen):
    """Everything the dashboard tab shows, computed from one filtered set."""
    return {
        "patient_count": patients.count(),
        "visit_count": visits.count(),
        "total_patients": Patient.objects.count(),
        "age_distribution": _age_distribution(patients),
        "sex_distribution": _sex_distribution(patients),
        "top_conditions": _top_conditions(patients, chosen),
        "visits_over_time": _visits_over_time(visits),
        "visit_outcomes": _visit_outcomes(visits),
    }
