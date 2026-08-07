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
from clinical.models import ClinicalNote, Diagnosis, Investigation, ReferenceLetter
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

    # Visit history means consultations that actually happened, plus the one
    # happening right now. A booking made for next week is not history, and a
    # cancellation or a no-show is a diary event rather than a clinical one —
    # listing them padded the record with rows the doctor had to read past.
    visits = (
        patient.visits.filter(status__in=(
            VisitStatus.IN_CABIN, VisitStatus.CONSULTED,
            VisitStatus.BILLED, VisitStatus.COMPLETED,
        ))
        .select_related("doctor")
        .order_by("-scheduled_start")
    )
    last_visit = visits.first()
    last_note = patient.notes.order_by("-created_at").first()

    diagnoses = patient.diagnoses.all()
    resolved = diagnoses.exclude(status=Diagnosis.Status.ACTIVE)

    context = {
        "patient": patient,
        "history": history,
        "active_diagnoses": diagnoses.filter(status=Diagnosis.Status.ACTIVE),
        # Kept apart from the active list rather than mixed in. A resolved
        # problem is still worth having — "treated for this in 2023" changes
        # what today's symptom might be — but it must never read as current.
        "past_diagnoses": resolved,
        "past_diagnosis_count": resolved.count(),
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
        .select_related("doctor", "visit__note")
        .prefetch_related(Prefetch("items", queryset=PrescriptionItem.objects.order_by("order", "id")))
        .order_by("-created_at")
    )
    # What the tab shows and what gets printed must agree, so it is computed
    # once here rather than being worked out again by the template.
    for prescription in prescriptions:
        note = getattr(prescription.visit, "note", None) if prescription.visit_id else None
        prescription.printed_note = (
            note.prescription_note if note and note.prescription_note else prescription.advice
        )
    return {"patient": patient, "prescriptions": prescriptions}


def reference_letters_context(patient):
    letters = (
        ReferenceLetter.objects.filter(patient=patient)
        .select_related("doctor")
        .order_by("-created_at")
    )
    return {"patient": patient, "reference_letters": letters}


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
            point = {
                "month": round(measurement.age_months, 2),
                "value": float(value),
                "date": measurement.measured_on.isoformat(),
                # An LMS reference computes these; a published one leaves them
                # empty and fills in the band instead. Both are carried so the
                # template can show whichever the reference actually supports.
                "z": None, "percentile": None,
                "sds": None, "centile": None,
                "band_label": None, "off_scale": None, "companion": None,
                "kind": None, "source": None,
            }
            if scored:
                point.update({key: scored[key] for key in point if key in scored})
            points.append(point)

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

        # The IAP BMI chart carries two lines that are not centiles at all —
        # the adult-equivalent overweight and obesity cut-offs. They are kept
        # apart from the curves so the chart can label them for what they are.
        cutoffs = ref.cutoff_curves(indicator, patient.sex, min_month, max_month, step=step)

        # For BMI the IAP tables print centiles only up to the 50th, then the
        # two cut-off lines. So above the median a centile band is not what the
        # chart can say — the overweight/obesity status is, and it is what the
        # paper intends the doctor to read. Attached per chart so the BMI panel
        # can lead with it instead of reporting "off the printed scale" for a
        # child who is merely above average.
        chart_status = None
        if indicator == ref.BMI_FOR_AGE:
            from growth import bmi as bmi_module

            latest_point = points[-1]
            chart_status = bmi_module.assess(
                patient.sex, latest_point["month"], latest_point["value"]
            )

        charts.append(
            {
                "indicator": indicator,
                "label": ref.INDICATOR_LABELS[indicator],
                "unit": ref.INDICATOR_UNITS[indicator],
                "status": chart_status,
                "points": points,
                "curves": [
                    {"percentile": p, "points": curves[p]} for p in sorted(curves)
                ],
                "cutoffs": [
                    {"key": key, "label": line["label"], "points": line["points"]}
                    for key, line in sorted(cutoffs.items())
                ],
                "latest": points[-1],
                "sources": sources,
            }
        )

    latest = measurements.last()

    # Overweight and obesity in a 5–18 year old are judged against the IAP
    # adult-equivalent cut-offs, not against a centile and not against the adult
    # BMI thresholds. Absent for any reference that does not publish them.
    bmi_status = None
    if latest is not None and latest.bmi is not None:
        from growth import bmi as bmi_module

        bmi_status = bmi_module.assess(
            patient.sex, latest.age_months, float(latest.bmi)
        )

    return {
        "patient": patient,
        "measurements": measurements.order_by("-measured_on"),
        "charts": charts,
        "latest_measurement": latest,
        "bmi_status": bmi_status,
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
    ordered = sorted(visits, key=lambda v: (rank.get(v.status, 9), v.scheduled_start))

    # KAN-49: grey the Send button out while somebody is still in the cabin.
    #
    # The rule itself lives in Visit.transition_to and is enforced there; this
    # only stops the doctor being offered a button that will refuse them. A
    # doctor who has forgotten to finish the last consultation reads a live
    # "Send in" that errors as the system being broken — a greyed one naming
    # who is still in there reads as the thing they have left to do, which is
    # what KAN-49 means by the missed completion persisting.
    occupied = next((v for v in ordered if v.status == VisitStatus.IN_CABIN), None)
    for visit in ordered:
        visit.send_blocked_by = occupied if occupied and occupied.pk != visit.pk else None

    return ordered
