"""
Turning an entered lab value into "here's the range, here's whether it's
abnormal" — safely, or not at all.

The one rule everything here follows: nothing gets flagged unless a human
entered a matching reference range and marked it VALIDATED (see
LabReferenceRange.status). No range, or only a REVIEW_REQUIRED one, means
this stays silent and the doctor keeps entering the reference range and the
abnormal flag by hand, exactly as they could before this existed.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db.models import Q

from .models import LabReferenceRange, LabUnitConversion, ReferenceSex, ReferenceStatus

#: patients.models.Sex's single-letter codes -> ReferenceSex's codes.
#: "O" and "N" (Other / Prefer not to say) intentionally map to nothing —
#: they fall back to sex-agnostic (ANY) ranges only, rather than guessing.
_SEX_CODES = {"M": ReferenceSex.MALE, "F": ReferenceSex.FEMALE}


def best_matching_range(lab_test, *, sex=None, age_years=None):
    """
    The most specific VALIDATED range for this test and patient, or ``None``.

    "Most specific" prefers a range that names this patient's actual sex over
    one marked ANY, and prefers a range that names an age band over one that
    doesn't — so a validated adult-only range never gets picked for a child
    just because it happened to be the only VALIDATED row on the test.
    """
    reference_sex = _SEX_CODES.get(sex)
    candidates = lab_test.reference_ranges.filter(status=ReferenceStatus.VALIDATED)
    candidates = candidates.filter(
        Q(sex=ReferenceSex.ANY) | Q(sex=reference_sex) if reference_sex else Q(sex=ReferenceSex.ANY)
    )

    best, best_score = None, -1
    for candidate in candidates:
        if not candidate.covers_age(age_years):
            continue
        score = 0
        if reference_sex and candidate.sex == reference_sex:
            score += 2
        if candidate.age_min is not None or candidate.age_max is not None:
            score += 1
        if score > best_score:
            best, best_score = candidate, score
    return best


def convert_value(value, from_unit, to_unit, lab_test=None):
    """
    ``value`` expressed in ``to_unit``, or ``None`` if no conversion is on
    file — never a guessed multiplier. A test-specific conversion is tried
    before a generic (``lab_test=None``) one.
    """
    if not from_unit or not to_unit or from_unit.strip().lower() == to_unit.strip().lower():
        return value

    conversion = None
    if lab_test is not None:
        conversion = LabUnitConversion.objects.filter(
            lab_test=lab_test, from_unit__iexact=from_unit, to_unit__iexact=to_unit,
        ).first()
    if conversion is None:
        conversion = LabUnitConversion.objects.filter(
            lab_test__isnull=True, from_unit__iexact=from_unit, to_unit__iexact=to_unit,
        ).first()
    if conversion is None:
        return None
    return conversion.convert(value)


def format_range(reference_range):
    if reference_range.low is not None and reference_range.high is not None:
        bounds = f"{reference_range.low}–{reference_range.high}"
    elif reference_range.low is not None:
        bounds = f"≥{reference_range.low}"
    else:
        bounds = f"≤{reference_range.high}"
    return f"{bounds} {reference_range.unit}".strip()


@dataclass
class Evaluation:
    reference_range: object = None
    formatted_range: str = ""
    #: True/False once a validated range was actually compared against;
    #: None means "say nothing" — no range, or a unit that couldn't be
    #: converted. Never guess in either direction.
    is_abnormal: object = None
    note: str = ""


def evaluate_value(lab_test, value_numeric, unit, *, sex=None, age_years=None):
    """The one entry point the investigation form's autocomplete calls."""
    if lab_test is None:
        return Evaluation()

    reference_range = best_matching_range(lab_test, sex=sex, age_years=age_years)
    if reference_range is None:
        return Evaluation(note="No validated reference range on file yet.")

    formatted = format_range(reference_range)
    if value_numeric is None:
        return Evaluation(reference_range=reference_range, formatted_range=formatted)

    try:
        compare_value = Decimal(value_numeric)
    except (InvalidOperation, TypeError):
        return Evaluation(reference_range=reference_range, formatted_range=formatted)

    if unit and reference_range.unit and unit.strip().lower() != reference_range.unit.strip().lower():
        converted = convert_value(compare_value, unit, reference_range.unit, lab_test=lab_test)
        if converted is None:
            return Evaluation(
                reference_range=reference_range,
                formatted_range=formatted,
                note=(
                    f"Entered in {unit}, the range on file is in "
                    f"{reference_range.unit} — no conversion between them is on "
                    f"file, so enter the value in {reference_range.unit} instead."
                ),
            )
        compare_value = converted

    abnormal = (
        (reference_range.low is not None and compare_value < reference_range.low)
        or (reference_range.high is not None and compare_value > reference_range.high)
    )
    return Evaluation(reference_range=reference_range, formatted_range=formatted, is_abnormal=abnormal)
