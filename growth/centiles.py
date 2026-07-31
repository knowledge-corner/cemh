"""
Reading a measurement off a published centile table.

WHO and CDC publish **LMS parameters**, from which any percentile can be
computed exactly — that is what :mod:`growth.reference` was originally built
around. The IAP 2015 charts do not: the committee fitted LMS curves but printed
only the resulting centiles, seven columns at half-year steps.

So a child is placed the way a doctor places one on a printed chart: find the
two curves the measurement falls between, and interpolate. The interpolation is
done on the **z-scale** rather than the value scale, because that is the axis on
which the reference curves are (very nearly) straight — it is the same
assumption that draws them.

Two things this module deliberately refuses to do:

**It will not extrapolate past the printed curves.** Below the 3rd centile or
above the 97th there is no second curve to interpolate against, and the error in
guessing grows without bound exactly where it matters most — short stature is
diagnosed below the 3rd centile. Such a child gets ``off_scale`` and no invented
number; :func:`growth.reference.assess` then supplies a z-score from an LMS
reference alongside, labelled as coming from somewhere else.

**It will not treat the BMI table's 23-Eq and 27-Eq columns as centiles.** They
are adult-equivalent cut-offs marking overweight and obesity, not percentiles of
the childhood distribution. See :mod:`growth.bmi`.
"""

#: Columns that really are percentiles, and which percentile each one is.
#: ``Eq23``/``Eq27`` are absent on purpose — see the module docstring.
PERCENTILE_COLUMNS = {
    "P3": 3, "P5": 5, "P10": 10, "P25": 25,
    "P50": 50, "P75": 75, "P90": 90, "P97": 97,
}


def _ordinal(percentile):
    if percentile in (11, 12, 13):
        return f"{percentile}th"
    return f"{percentile}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(percentile % 10, 'th') }"


def published_points(row):
    """
    The ``(value, percentile)`` pairs one row of a centile table publishes,
    in ascending order.
    """
    return sorted(
        (row[name], percentile)
        for name, percentile in PERCENTILE_COLUMNS.items()
        if name in row
    )


def place(row, value):
    """
    Locate ``value`` among the centiles published for one age.

    Returns a dict describing where the measurement sits, or ``None`` if the row
    publishes too few centiles to say anything. The keys are:

    ``band``, ``band_label``
        The two printed centiles the value falls between, always populated —
        this part is read straight off the table and is not approximated.
    ``centile``, ``sds``
        The interpolated position, and the equivalent standard deviation score.
        ``None`` when the value is off the printed scale.
    ``off_scale``
        ``"below"``, ``"above"``, or ``None``.
    """
    from growth.reference import z_for_percentile, z_to_percentile

    points = published_points(row)
    if len(points) < 2 or value is None:
        return None

    value = float(value)
    lowest, highest = points[0], points[-1]

    if value < lowest[0]:
        return {
            "band": (None, lowest[1]),
            "band_label": f"below the {_ordinal(lowest[1])} centile",
            "centile": None, "sds": None, "off_scale": "below",
        }
    if value > highest[0]:
        return {
            "band": (highest[1], None),
            "band_label": f"above the {_ordinal(highest[1])} centile",
            "centile": None, "sds": None, "off_scale": "above",
        }

    # Sitting exactly on a printed curve is worth saying plainly rather than
    # reporting as a band of zero width.
    for point_value, percentile in points:
        if value == point_value:
            z = z_for_percentile(percentile)
            return {
                "band": (percentile, percentile),
                "band_label": f"{_ordinal(percentile)} centile",
                "centile": float(percentile), "sds": round(z, 2), "off_scale": None,
            }

    for (low_value, low_pct), (high_value, high_pct) in zip(points, points[1:]):
        if not low_value < value < high_value:
            continue
        low_z, high_z = z_for_percentile(low_pct), z_for_percentile(high_pct)
        fraction = (value - low_value) / (high_value - low_value)
        z = low_z + fraction * (high_z - low_z)
        return {
            "band": (low_pct, high_pct),
            "band_label": f"{_ordinal(low_pct)}–{_ordinal(high_pct)} centile",
            "centile": round(z_to_percentile(z), 1),
            "sds": round(z, 2),
            "off_scale": None,
        }

    return None  # pragma: no cover - the bounds above make this unreachable


def curves(rows):
    """
    The published centile curves, verbatim.

    Nothing is computed: for a reference of this kind the printed columns *are*
    the curves. Returns ``{percentile: [{"month": m, "value": v}, …]}``.
    """
    series = {}
    for row in rows:
        for name, percentile in PERCENTILE_COLUMNS.items():
            if name in row:
                series.setdefault(percentile, []).append(
                    {"month": round(row["Month"], 2), "value": row[name]}
                )
    return series
