"""
Turning a measurement into a percentile.

There are two kinds of growth reference here, and the difference is not
cosmetic — it changes what the application is able to say about a child.

**Computed references (WHO, CDC).** These publish **LMS** parameters — a Box-Cox
power (L), the median (M) and a coefficient of variation (S) — for each age and
sex. Those three numbers describe the whole distribution at that age, so any
percentile and any z-score can be computed exactly::

    z = ((X / M)^L - 1) / (L * S)          for L ≠ 0
    z = ln(X / M) / S                       for L = 0

and back the other way, to draw the reference curves themselves::

    X = M * (1 + L * S * z)^(1/L)          for L ≠ 0
    X = M * exp(S * z)                      for L = 0

**Published references (IAP 2015).** These publish the centile curves
themselves and nothing more. A child is placed between two printed curves, the
way a doctor places one on paper, and no more precision is claimed than the
table supports. :mod:`growth.centiles` does that work, and explains why it
refuses to extrapolate past the outermost curve.

:func:`assess` reports which kind of reference answered, and which source, on
every measurement. Nothing here is ever silent about where a number came from.
Reference tables live in ``growth/reference/`` — see the SOURCES.md there.
"""

import functools
import json
import math
from pathlib import Path

REFERENCE_DIR = Path(__file__).resolve().parent / "reference"

#: Indicators this module can chart.
HEIGHT_FOR_AGE = "lhfa"
WEIGHT_FOR_AGE = "wfa"
BMI_FOR_AGE = "bmifa"
HEAD_CIRCUMFERENCE_FOR_AGE = "hcfa"

INDICATOR_LABELS = {
    HEIGHT_FOR_AGE: "Height for age",
    WEIGHT_FOR_AGE: "Weight for age",
    BMI_FOR_AGE: "BMI for age",
    HEAD_CIRCUMFERENCE_FOR_AGE: "Head circumference for age",
}

INDICATOR_UNITS = {
    HEIGHT_FOR_AGE: "cm",
    WEIGHT_FOR_AGE: "kg",
    BMI_FOR_AGE: "kg/m²",
    HEAD_CIRCUMFERENCE_FOR_AGE: "cm",
}

#: The two kinds of reference, as reported by :func:`assess`.
LMS = "lms"
CENTILES = "centiles"

#: Age bands published by each source, per indicator.
#: Each entry is (directory, filename stem template, min month, max month).
_BANDS = {
    "who": {
        HEIGHT_FOR_AGE: [("who", "lhfa_{sex}_0_5", 0, 60)],
        WEIGHT_FOR_AGE: [("who", "wfa_{sex}_0_5", 0, 60)],
        BMI_FOR_AGE: [("who", "bmifa_{sex}_0_2", 0, 24),
                      ("who", "bmifa_{sex}_2_5", 24, 60)],
        HEAD_CIRCUMFERENCE_FOR_AGE: [("who", "hcfa_{sex}_0_5", 0, 60)],
    },
    "iap": {
        # Indian Academy of Paediatrics 2015 revised charts, 5–18 years.
        # Centile tables, not LMS — see growth/reference/SOURCES.md.
        HEIGHT_FOR_AGE: [("iap", "lhfa_{sex}_5_18", 60, 216)],
        WEIGHT_FOR_AGE: [("iap", "wfa_{sex}_5_18", 60, 216)],
        BMI_FOR_AGE: [("iap", "bmifa_{sex}_5_18", 60, 216)],
        HEAD_CIRCUMFERENCE_FOR_AGE: [],
    },
    "cdc": {
        HEIGHT_FOR_AGE: [("cdc", "lhfa_{sex}_2_20", 24, 240)],
        WEIGHT_FOR_AGE: [("cdc", "wfa_{sex}_2_20", 24, 240)],
        BMI_FOR_AGE: [("cdc", "bmifa_{sex}_2_20", 24, 240)],
        HEAD_CIRCUMFERENCE_FOR_AGE: [],
    },
}

#: Which sources to consult, in order, for each configurable standard.
#:
#: Bands are tried in order and the first one that both covers the age *and* is
#: actually installed wins, so the chosen standard leads its own list. The
#: under-fives still come from WHO under every option below — not by ordering
#: but because IAP publishes nothing under 5.0 years and CDC nothing under 2.
#: Order matters at the boundary: a child of exactly 5.0 years falls in both the
#: WHO and the IAP band, and IAP is the one that should answer.
#:
#: CDC trails every list as a last resort. That is deliberate: a chart drawn
#: against a fallback and labelled as such is useful, whereas no chart at all
#: is not. :func:`assess` reports which source actually produced each value, so
#: the fallback is never silent.
_STANDARDS = {
    # IAP's own recommendation: IAP 2015 from five, WHO below it.
    "IAP": ["iap", "who", "cdc"],
    "WHO": ["who", "cdc"],
    "CDC": ["cdc", "who"],
}

#: Sources that publish LMS, in the order to try when :func:`assess` needs a
#: continuous z-score to sit alongside an off-scale centile band.
_COMPANION_SOURCES = ["cdc", "who"]

DEFAULT_STANDARD = "WHO"


def active_standard():
    """The reference standard this clinic is configured to chart against."""
    from django.conf import settings

    chosen = getattr(settings.CLINIC, "GROWTH_REFERENCE", DEFAULT_STANDARD)
    return chosen if chosen in _STANDARDS else DEFAULT_STANDARD


def _tables_for(indicator, standard):
    """Ordered bands for one indicator under the given standard."""
    bands = []
    for source in _STANDARDS[standard]:
        bands.extend(_BANDS[source].get(indicator, []))
    return bands


#: Percentile curves drawn behind the patient's own points. These are the lines
#: a paediatrician expects to see on a growth chart. A published reference draws
#: whichever of them it prints, and no others.
CHART_PERCENTILES = [3, 10, 25, 50, 75, 90, 97]


class ReferenceUnavailable(Exception):
    """No published reference covers this indicator, sex and age."""


class NotComputable(Exception):
    """
    Asked for an exact z-score from a reference that only publishes centiles.

    Raised rather than returning an approximation, because a caller holding a
    z-score has no way to tell a fitted one from a published one.
    """


def _sex_key(sex):
    if sex == "M":
        return "boys"
    if sex == "F":
        return "girls"
    raise ReferenceUnavailable(
        "Growth references are published separately for boys and girls; "
        "this patient's sex is recorded as neither."
    )


@functools.lru_cache(maxsize=64)
def _load_table(directory, stem):
    """
    Load one reference file, as ``(rows, kind)``.

    The kind is read from the file's own columns rather than assumed from the
    directory it sits in, so a file in the wrong shape is caught here instead of
    producing nonsense further down.
    """
    path = REFERENCE_DIR / directory / f"{stem}.json"
    if not path.exists():
        raise ReferenceUnavailable(f"Reference table {directory}/{stem}.json is not installed.")

    with path.open() as fh:
        raw = json.load(fh)

    rows, kind = [], None
    for row in raw:
        # Tables key the x-axis as Month, Week, Length or Height depending on
        # the indicator; for age-based charts it is always Month.
        if "Month" not in row:
            continue
        try:
            month = float(row["Month"])
        except (TypeError, ValueError):
            continue

        if {"L", "M", "S"} <= set(row):
            try:
                parsed = {key: float(row[key]) for key in ("L", "M", "S")}
            except (TypeError, ValueError):
                continue
            kind = kind or LMS
        elif "P50" in row:
            parsed = {}
            for key, value in row.items():
                if key == "Month":
                    continue
                try:
                    parsed[key] = float(value)
                except (TypeError, ValueError):
                    continue
            kind = kind or CENTILES
        else:
            continue

        parsed["month"] = month
        rows.append(parsed)

    rows.sort(key=lambda r: r["month"])
    return rows, (kind or LMS)


def _interpolate(lower, upper, age_months):
    """Blend two reference rows. Every published column moves together."""
    span = upper["month"] - lower["month"]
    if span == 0:
        return lower
    weight = (age_months - lower["month"]) / span
    blended = {
        key: lower[key] + weight * (upper[key] - lower[key])
        for key in lower
        if key != "month" and key in upper
    }
    blended["month"] = age_months
    return blended


def _row_in(rows, age_months):
    """The row for an age within one already-loaded table."""
    if age_months <= rows[0]["month"]:
        return rows[0]
    if age_months >= rows[-1]["month"]:
        return rows[-1]
    for lower, upper in zip(rows, rows[1:]):
        if lower["month"] <= age_months <= upper["month"]:
            return _interpolate(lower, upper, age_months)
    return None  # pragma: no cover - the bounds above cover every age


def _row_for_age(indicator, sex, age_months, standard=None):
    """
    The reference row for one indicator at a given age, as
    ``(row, source, kind)``, interpolating between the two nearest published
    rows.

    Tables are published at intervals — every month for WHO, every six months
    for IAP — and a child measured between rows is interpolated rather than
    snapped to the nearest one.

    A band whose file is not installed is skipped rather than raising, so a
    standard that has been selected but not supplied falls through to the next
    source. The source returned is the one that actually answered.
    """
    sex_key = _sex_key(sex)
    standard = standard or active_standard()

    for directory, template, low, high in _tables_for(indicator, standard):
        if not (low <= age_months <= high):
            continue
        try:
            rows, kind = _load_table(directory, template.format(sex=sex_key))
        except ReferenceUnavailable:
            continue
        if not rows:
            continue
        row = _row_in(rows, age_months)
        if row is not None:
            return row, directory, kind

    raise ReferenceUnavailable(
        f"No growth reference covers {INDICATOR_LABELS.get(indicator, indicator)} "
        f"at {age_months:.1f} months."
    )


def _lms_for_age(indicator, sex, age_months, standard=None):
    """
    As :func:`_row_for_age`, but only for references that publish LMS.

    Raises :class:`NotComputable` if the reference that answers is a centile
    table, since there is no L, M or S to return.
    """
    row, source, kind = _row_for_age(indicator, sex, age_months, standard)
    if kind != LMS:
        raise NotComputable(
            f"The {source.upper()} reference publishes centile curves, not LMS "
            f"parameters, so an exact z-score cannot be computed from it."
        )
    return row, source


def _z_from_lms(row, value):
    L, M, S = row["L"], row["M"], row["S"]
    if L == 0:
        return math.log(value / M) / S
    return (((value / M) ** L) - 1) / (L * S)


def _value_from_lms(row, z):
    L, M, S = row["L"], row["M"], row["S"]
    if L == 0:
        return M * math.exp(S * z)
    base = 1 + L * S * z
    if base <= 0:
        return None
    return M * (base ** (1 / L))


def z_score(indicator, sex, age_months, value):
    """
    Z-score of ``value`` against the published reference.

    May raise :class:`ReferenceUnavailable` or :class:`NotComputable`.
    """
    if value is None or value <= 0:
        return None

    row, _source = _lms_for_age(indicator, sex, age_months)
    return _z_from_lms(row, value)


def value_for_z(indicator, sex, age_months, z):
    """Inverse of :func:`z_score` — the measurement sitting exactly on ``z``.
    This is what draws the reference curves for an LMS reference."""
    row, _source = _lms_for_age(indicator, sex, age_months)
    return _value_from_lms(row, z)


def z_to_percentile(z):
    """Convert a z-score to a percentile using the normal CDF."""
    if z is None:
        return None
    return 100 * 0.5 * (1 + math.erf(z / math.sqrt(2)))


def percentile_for_z(z):
    """Percentile rounded for display."""
    pct = z_to_percentile(z)
    return None if pct is None else round(pct, 1)


def z_for_percentile(percentile):
    """
    Inverse normal CDF — the z-score at a given percentile.

    Uses the Acklam rational approximation, accurate to about 1.15e-9 across
    the range, which is far finer than growth charting needs.
    """
    p = percentile / 100.0
    if not 0 < p < 1:
        raise ValueError("Percentile must be strictly between 0 and 100.")

    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]

    p_low, p_high = 0.02425, 1 - 0.02425

    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    if p > p_high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
                ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)

    q = p - 0.5
    r = q * q
    return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5])*q / \
           (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)


def _companion(indicator, sex, age_months, value):
    """
    A z-score from an LMS reference, for a child who is off the printed scale of
    a centile reference.

    Growth failure is diagnosed below the 3rd centile, which is exactly where a
    centile table stops being able to say anything. Rather than leave the doctor
    with "below the 3rd" and nothing else, fall back to a reference that can
    still produce a number — and label it, because it describes a different
    population.
    """
    try:
        sex_key = _sex_key(sex)
    except ReferenceUnavailable:
        return None

    for source in _COMPANION_SOURCES:
        for directory, template, low, high in _BANDS[source].get(indicator, []):
            if not (low <= age_months <= high):
                continue
            try:
                rows, kind = _load_table(directory, template.format(sex=sex_key))
            except ReferenceUnavailable:
                continue
            if kind != LMS or not rows:
                continue
            row = _row_in(rows, age_months)
            if row is None:
                continue
            z = _z_from_lms(row, value)
            return {"source": directory.upper(), "z": round(z, 2),
                    "percentile": percentile_for_z(z)}
    return None


def assess(indicator, sex, age_months, value):
    """
    Score one measurement.

    Returns ``None`` when no published reference covers this combination — a
    missing chart is far better than a wrong one. Otherwise a dict whose keys
    always say what kind of number they hold:

    ``source``, ``kind``
        Which reference answered, and whether it computes (``"lms"``) or
        publishes (``"centiles"``) its curves.
    ``z``, ``percentile``
        Exact, from LMS. ``None`` for a published reference.
    ``sds``, ``centile``, ``band``, ``band_label``, ``off_scale``
        From a published reference. The band is read straight off the table;
        the interpolated ``centile`` and ``sds`` are ``None`` when the child is
        outside the outermost printed curve.
    ``companion``
        A z-score from an LMS reference, present only when a published
        reference could not place the child.
    """
    if value is None:
        return None
    try:
        row, source, kind = _row_for_age(indicator, sex, age_months)
    except ReferenceUnavailable:
        return None

    value = float(value)
    result = {
        "source": source.upper(), "kind": kind,
        "z": None, "percentile": None,
        "sds": None, "centile": None,
        "band": None, "band_label": None, "off_scale": None,
        "companion": None,
    }

    if kind == LMS:
        z = _z_from_lms(row, value)
        result["z"] = round(z, 2)
        result["percentile"] = percentile_for_z(z)
        return result

    from growth import centiles

    placed = centiles.place(row, value)
    if placed is None:
        return None
    result.update(placed)
    if placed["off_scale"]:
        result["companion"] = _companion(indicator, sex, age_months, value)
    return result


def reference_curves(indicator, sex, min_month, max_month, step=1.0):
    """
    Percentile curves to draw behind the patient's points.

    Returns ``{percentile: [{"month": m, "value": v}, ...]}``, restricted to the
    ages the published tables actually cover — the curve stops where the data
    stops rather than being extrapolated. A chart spanning a change of source
    (WHO below five, IAP above it) is drawn from whichever reference covers each
    age, which is how the two are meant to be used together.

    For a published reference the values are the printed ones: nothing is
    computed, because the paper's curves *are* the reference.
    """
    from growth import centiles

    z_by_percentile = {p: z_for_percentile(p) for p in CHART_PERCENTILES}
    column_for = {
        percentile: name
        for name, percentile in centiles.PERCENTILE_COLUMNS.items()
    }
    points = {p: [] for p in CHART_PERCENTILES}

    month = max(0.0, float(min_month))
    end = float(max_month)

    while month <= end:
        try:
            row, _source, kind = _row_for_age(indicator, sex, month)
        except ReferenceUnavailable:
            month += step
            continue

        for percentile in CHART_PERCENTILES:
            if kind == LMS:
                value = _value_from_lms(row, z_by_percentile[percentile])
            else:
                value = row.get(column_for.get(percentile))
            if value is not None:
                points[percentile].append(
                    {"month": round(month, 2), "value": round(value, 2)}
                )
        month += step

    return {percentile: series for percentile, series in points.items() if series}


def cutoff_curves(indicator, sex, min_month, max_month, step=1.0):
    """
    Non-centile reference lines to draw alongside the curves.

    At present these are the IAP BMI chart's adult-equivalent cut-offs, which
    mark overweight and obesity and are not percentiles of anything. They are
    returned separately so the chart can label them for what they are rather
    than drawing them as two more anonymous centiles. ``{}`` for every other
    chart.
    """
    lines = {
        "Eq23": {"label": "Overweight (adult BMI 23)", "points": []},
        "Eq27": {"label": "Obesity (adult BMI 27)", "points": []},
    }

    month = max(0.0, float(min_month))
    end = float(max_month)
    while month <= end:
        try:
            row, _source, kind = _row_for_age(indicator, sex, month)
        except ReferenceUnavailable:
            month += step
            continue
        if kind == CENTILES:
            for key, line in lines.items():
                if key in row:
                    line["points"].append(
                        {"month": round(month, 2), "value": round(row[key], 2)}
                    )
        month += step

    return {key: line for key, line in lines.items() if line["points"]}
