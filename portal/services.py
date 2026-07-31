"""
Assembling what the doctor sees.

Each function here gathers the data for one tab of the patient dashboard. They
are kept out of the view layer so the queries can be tested directly, and so
the growth tab can be skipped entirely when that app is not installed.
"""

from collections import OrderedDict
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone

from appointments.models import Visit, VisitStatus
from clinical.models import ClinicalNote, Diagnosis, Investigation
from pharmacy.models import Prescription, PrescriptionItem


def growth_installed():
    return apps.is_installed("growth")


def group_visits_by_period(visits):
    """
    Bucket visits into the time bands clinicians read charts in — "Today",
    "6 months ago", "2 years ago" — rather than a flat dated list.

    Returns a list of ``(label, [visit, ...])`` in reverse chronological order,
    preserving the order of the queryset it was given.
    """
    today = timezone.localdate()
    bands = [
        ("Today", timedelta(days=0)),
        ("This week", timedelta(days=7)),
        ("This month", timedelta(days=31)),
        ("Last 6 months", timedelta(days=183)),
        ("Last year", timedelta(days=365)),
        ("1–2 years ago", timedelta(days=730)),
        ("2–5 years ago", timedelta(days=1826)),
    ]

    grouped = OrderedDict()
    for visit in visits:
        age = today - timezone.localtime(visit.scheduled_start).date()
        label = "Earlier"
        for band_label, window in bands:
            if age <= window:
                label = band_label
                break
        grouped.setdefault(label, []).append(visit)

    return list(grouped.items())


def summary_context(patient):
    """Everything the doctor should see the moment the file opens."""
    history = getattr(patient, "history", None)

    visits = (
        patient.visits.exclude(status=VisitStatus.CANCELLED)
        .select_related("doctor")
        .order_by("-scheduled_start")
    )
    last_visit = visits.first()
    last_note = patient.notes.order_by("-created_at").first()

    context = {
        "patient": patient,
        "history": history,
        "active_diagnoses": patient.diagnoses.filter(status=Diagnosis.Status.ACTIVE),
        "last_visit": last_visit,
        "last_note": last_note,
        "visit_count": visits.count(),
        "visit_groups": group_visits_by_period(visits[:40]),
        "recent_investigations": patient.investigations.all()[:5],
    }

    if growth_installed():
        Measurement = apps.get_model("growth", "Measurement")
        context["latest_measurement"] = (
            Measurement.objects.filter(patient=patient).order_by("-measured_on").first()
        )

    return context


def notes_context(patient):
    notes = (
        ClinicalNote.objects.filter(patient=patient)
        .select_related("author", "visit")
        .order_by("-created_at")
    )
    return {"patient": patient, "notes": notes}


def investigations_context(patient):
    """
    Investigations grouped by test, newest first within each group.

    Grouping matters clinically: the doctor is not asking "what tests were done
    in March", they are asking "what has this patient's TSH done over time".
    """
    results = Investigation.objects.filter(patient=patient).order_by(
        "test_name", "-performed_on"
    )

    grouped = OrderedDict()
    for result in results:
        grouped.setdefault(result.test_name, []).append(result)

    # Present the most recently updated test first.
    ordered = OrderedDict(
        sorted(grouped.items(), key=lambda kv: kv[1][0].performed_on, reverse=True)
    )

    trends = {}
    for test_name, rows in ordered.items():
        numeric = [r for r in reversed(rows) if r.value_numeric is not None]
        if len(numeric) >= 2:
            trends[test_name] = [
                {"date": r.performed_on.isoformat(), "value": float(r.value_numeric)}
                for r in numeric
            ]

    return {
        "patient": patient,
        "grouped_investigations": ordered,
        "trends": trends,
        "has_results": bool(ordered),
    }


def prescriptions_context(patient):
    prescriptions = (
        Prescription.objects.filter(patient=patient)
        .select_related("doctor", "visit")
        .prefetch_related(Prefetch("items", queryset=PrescriptionItem.objects.order_by("order", "id")))
        .order_by("-created_at")
    )
    return {"patient": patient, "prescriptions": prescriptions}


def growth_context(patient):
    """
    Growth chart data.

    Returns ``None`` when the growth app is not installed, so the caller can
    omit the tab entirely rather than rendering an empty one.
    """
    if not growth_installed():
        return None

    from growth import reference as ref

    Measurement = apps.get_model("growth", "Measurement")
    measurements = Measurement.objects.filter(patient=patient).order_by("measured_on")

    indicators = [
        (ref.HEIGHT_FOR_AGE, "height_cm"),
        (ref.WEIGHT_FOR_AGE, "weight_kg"),
        (ref.BMI_FOR_AGE, "bmi"),
    ]
    if patient.age_years < 3:
        indicators.append((ref.HEAD_CIRCUMFERENCE_FOR_AGE, "head_circumference_cm"))

    charts = []
    for indicator, attribute in indicators:
        points = []
        for measurement in measurements:
            value = getattr(measurement, attribute, None)
            if value is None:
                continue
            scored = ref.assess(indicator, patient.sex, measurement.age_months, float(value))
            points.append(
                {
                    "month": round(measurement.age_months, 2),
                    "value": float(value),
                    "date": measurement.measured_on.isoformat(),
                    "z": scored["z"] if scored else None,
                    "percentile": scored["percentile"] if scored else None,
                    "source": scored["source"] if scored else None,
                }
            )

        if not points:
            continue

        # Draw the reference curves well either side of the patient's own data.
        # A growth chart is read by seeing where a child sits relative to the
        # whole family of centiles, so a narrow window either side of their
        # points would defeat the purpose.
        min_month = max(0, min(p["month"] for p in points) - 24)
        max_month = max(p["month"] for p in points) + 24
        step = 1.0 if (max_month - min_month) <= 60 else 3.0

        curves = ref.reference_curves(indicator, patient.sex, min_month, max_month, step=step)
        if not curves:
            continue

        # Report the source that actually produced these values, not the one
        # configured. A selected standard whose tables are missing falls back,
        # and the doctor must be able to see that it did.
        sources = sorted({p["source"] for p in points if p.get("source")})

        charts.append(
            {
                "indicator": indicator,
                "label": ref.INDICATOR_LABELS[indicator],
                "unit": ref.INDICATOR_UNITS[indicator],
                "points": points,
                "curves": [
                    {"percentile": p, "points": curves[p]} for p in sorted(curves)
                ],
                "latest": points[-1],
                "sources": sources,
            }
        )

    latest = measurements.last()
    return {
        "patient": patient,
        "measurements": measurements.order_by("-measured_on"),
        "charts": charts,
        "latest_measurement": latest,
        "mid_parental_height": latest.mid_parental_height_cm if latest else None,
        "configured_standard": ref.active_standard(),
        # True when the configured standard could not supply every chart and
        # something else was used instead.
        "using_fallback": any(
            chart["sources"] and ref.active_standard() not in chart["sources"]
            for chart in charts
        ),
    }


def todays_queue(doctor):
    """
    The doctor's list for today.

    Ordered so the patient already in the cabin is first, then those waiting in
    arrival order, then the rest of the day's bookings.
    """
    visits = (
        Visit.objects.for_date()
        .filter(doctor=doctor)
        .active()
        .with_related()
        .order_by("scheduled_start")
    )

    rank = {
        VisitStatus.IN_CABIN: 0,
        VisitStatus.ARRIVED: 1,
        VisitStatus.CONFIRMED: 2,
        VisitStatus.BOOKED: 3,
        VisitStatus.CONSULTED: 4,
        VisitStatus.BILLED: 5,
    }
    return sorted(visits, key=lambda v: (rank.get(v.status, 9), v.scheduled_start))
