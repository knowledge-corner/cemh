"""
Turning a measurement into a percentile.

Growth references are published as **LMS** parameters — a Box-Cox power (L),
the median (M) and a coefficient of variation (S) — for each age and sex. Those
three numbers describe the whole distribution at that age, which is what lets a
single height become "this child is on the 12th percentile".

The conversion is::

    z = ((X / M)^L - 1) / (L * S)          for L ≠ 0
    z = ln(X / M) / S                       for L = 0

and back the other way, to draw the reference curves themselves::

    X = M * (1 + L * S * z)^(1/L)          for L ≠ 0
    X = M * exp(S * z)                      for L = 0

Reference tables live in ``growth/reference/`` — see the SOURCES.md there for
what is included, and for the two gaps that must be closed before clinical use.
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

#: Which file covers which age band, per source and indicator.
#: Each entry is (directory, filename stem template, min month, max month).
#: Bands are tried in order and the first one covering the age wins, so WHO
#: data takes precedence for the under-fives where both exist.
_TABLES = {
    HEIGHT_FOR_AGE: [
        ("who", "lhfa_{sex}_0_5", 0, 60),
        ("cdc", "lhfa_{sex}_2_20", 24, 240),
    ],
    WEIGHT_FOR_AGE: [
        ("who", "wfa_{sex}_0_5", 0, 60),
        ("cdc", "wfa_{sex}_2_20", 24, 240),
    ],
    BMI_FOR_AGE: [
        ("who", "bmifa_{sex}_0_2", 0, 24),
        ("who", "bmifa_{sex}_2_5", 24, 60),
        ("cdc", "bmifa_{sex}_2_20", 24, 240),
    ],
    HEAD_CIRCUMFERENCE_FOR_AGE: [
        ("who", "hcfa_{sex}_0_5", 0, 60),
    ],
}

#: Percentile curves drawn behind the patient's own points. These are the lines
#: a paediatrician expects to see on a growth chart.
CHART_PERCENTILES = [3, 10, 25, 50, 75, 90, 97]


class ReferenceUnavailable(Exception):
    """No published reference covers this indicator, sex and age."""


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
    """Load one reference file, normalised to a sorted list of LMS rows."""
    path = REFERENCE_DIR / directory / f"{stem}.json"
    if not path.exists():
        raise ReferenceUnavailable(f"Reference table {directory}/{stem}.json is not installed.")

    with path.open() as fh:
        raw = json.load(fh)

    rows = []
    for row in raw:
        # Tables key the x-axis as Month, Week, Length or Height depending on
        # the indicator; for age-based charts it is always Month.
        if "Month" not in row:
            continue
        try:
            rows.append(
                {
                    "month": float(row["Month"]),
                    "L": float(row["L"]),
                    "M": float(row["M"]),
                    "S": float(row["S"]),
                }
            )
        except (KeyError, ValueError, TypeError):
            continue

    rows.sort(key=lambda r: r["month"])
    return rows


def _lms_for_age(indicator, sex, age_months):
    """
    LMS parameters for one indicator at a given age, interpolating between the
    two nearest published rows.

    Tables are published at whole months; a child measured at 42.7 months sits
    between rows, and interpolating is more faithful than snapping to the
    nearest month.
    """
    sex_key = _sex_key(sex)

    for directory, template, low, high in _TABLES.get(indicator, []):
        if not (low <= age_months <= high):
            continue
        try:
            rows = _load_table(directory, template.format(sex=sex_key))
        except ReferenceUnavailable:
            continue
        if not rows:
            continue

        if age_months <= rows[0]["month"]:
            return rows[0], directory
        if age_months >= rows[-1]["month"]:
            return rows[-1], directory

        for lower, upper in zip(rows, rows[1:]):
            if lower["month"] <= age_months <= upper["month"]:
                span = upper["month"] - lower["month"]
                if span == 0:
                    return lower, directory
                weight = (age_months - lower["month"]) / span
                blended = {
                    key: lower[key] + weight * (upper[key] - lower[key])
                    for key in ("L", "M", "S")
                }
                blended["month"] = age_months
                return blended, directory

    raise ReferenceUnavailable(
        f"No growth reference covers {INDICATOR_LABELS.get(indicator, indicator)} "
        f"at {age_months:.1f} months."
    )


def z_score(indicator, sex, age_months, value):
    """Z-score of ``value`` against the published reference. May raise
    :class:`ReferenceUnavailable`."""
    if value is None or value <= 0:
        return None

    lms, _source = _lms_for_age(indicator, sex, age_months)
    L, M, S = lms["L"], lms["M"], lms["S"]

    if L == 0:
        return math.log(value / M) / S
    return (((value / M) ** L) - 1) / (L * S)


def value_for_z(indicator, sex, age_months, z):
    """Inverse of :func:`z_score` — the measurement sitting exactly on ``z``.
    This is what draws the reference curves."""
    lms, _source = _lms_for_age(indicator, sex, age_months)
    L, M, S = lms["L"], lms["M"], lms["S"]

    if L == 0:
        return M * math.exp(S * z)
    base = 1 + L * S * z
    if base <= 0:
        return None
    return M * (base ** (1 / L))


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


def assess(indicator, sex, age_months, value):
    """
    Score one measurement.

    Returns ``{"z", "percentile", "source"}``, or ``None`` when no published
    reference covers this combination — a missing chart is far better than a
    wrong one.
    """
    if value is None:
        return None
    try:
        lms, source = _lms_for_age(indicator, sex, age_months)
    except ReferenceUnavailable:
        return None

    value = float(value)
    L, M, S = lms["L"], lms["M"], lms["S"]
    z = math.log(value / M) / S if L == 0 else (((value / M) ** L) - 1) / (L * S)

    return {
        "z": round(z, 2),
        "percentile": percentile_for_z(z),
        "source": source.upper(),
    }


def reference_curves(indicator, sex, min_month, max_month, step=1.0):
    """
    Percentile curves to draw behind the patient's points.

    Returns ``{percentile: [{"month": m, "value": v}, ...]}``, restricted to the
    ages the published tables actually cover — the curve stops where the data
    stops rather than being extrapolated.
    """
    curves = {}
    z_by_percentile = {p: z_for_percentile(p) for p in CHART_PERCENTILES}

    month = max(0.0, float(min_month))
    end = float(max_month)

    points = {p: [] for p in CHART_PERCENTILES}
    while month <= end:
        for percentile, z in z_by_percentile.items():
            try:
                value = value_for_z(indicator, sex, month, z)
            except ReferenceUnavailable:
                continue
            if value is not None:
                points[percentile].append({"month": round(month, 2), "value": round(value, 2)})
        month += step

    for percentile, series in points.items():
        if series:
            curves[percentile] = series
    return curves
