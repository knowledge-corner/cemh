"""
Overweight and obesity in a 5- to 18-year-old.

This is a different question from "what centile is this child on", and the IAP
2015 paper answers it differently. Its BMI tables print the 3rd, 5th, 10th, 25th
and 50th centiles, and then two columns that are **not centiles at all**: the
BMI which, extrapolated along the child's own curve, reaches an adult BMI of 23
or of 27 at eighteen years. Those are the overweight and obesity cut-offs, and
the paper is explicit that they are the ones to use::

    To define overweight and obesity in children from 5-18 years of age, adult
    equivalent of 23 and 27 cut-offs presented in BMI charts may be used.

23 and 27, not 25 and 30, because Asian populations carry more adiposity and
more cardio-metabolic risk at a lower BMI. **The adult cut-offs must never be
applied to a child**, and the childhood cut-offs are age- and sex-specific: the
23-equivalent line sits at 15.7 kg/m² for a five-year-old boy and 23.2 for an
eighteen-year-old.

Thinness is the 3rd centile, per the paper's reference to the WHO definition.
"""

THINNESS = "Thinness"
NORMAL = "Normal"
OVERWEIGHT = "Overweight"
OBESITY = "Obesity"

#: What each verdict means, for display next to it.
STATUS_NOTES = {
    THINNESS: "below the 3rd centile",
    NORMAL: "3rd centile to the 23-equivalent line",
    OVERWEIGHT: "at or above the adult-equivalent BMI 23 line",
    OBESITY: "at or above the adult-equivalent BMI 27 line",
}


def status(row, bmi):
    """
    Classify a BMI against one row of the IAP table.

    Returns one of the four constants above, or ``None`` when this reference
    does not publish the adult-equivalent cut-offs — a clinic charting against
    WHO or CDC gets no verdict rather than one derived from the wrong lines.
    """
    if bmi is None or row is None:
        return None
    if not {"Eq23", "Eq27", "P3"} <= set(row):
        return None

    bmi = float(bmi)
    if bmi >= row["Eq27"]:
        return OBESITY
    if bmi >= row["Eq23"]:
        return OVERWEIGHT
    if bmi < row["P3"]:
        return THINNESS
    return NORMAL


def assess(sex, age_months, bmi, standard=None):
    """
    Convenience wrapper: look the row up, then classify.

    Returns ``{"status", "note", "cutoffs"}`` or ``None``.
    """
    from growth.reference import BMI_FOR_AGE, ReferenceUnavailable, _row_for_age

    try:
        row, _source, kind = _row_for_age(BMI_FOR_AGE, sex, age_months, standard)
    except ReferenceUnavailable:
        return None
    if kind != "centiles":
        return None

    verdict = status(row, bmi)
    if verdict is None:
        return None
    return {
        "status": verdict,
        "note": STATUS_NOTES[verdict],
        "cutoffs": {
            "thinness": row["P3"],
            "overweight": row["Eq23"],
            "obesity": row["Eq27"],
        },
    }
