"""
Getting real, sourced reference ranges from a clinician's spreadsheet into
LabReferenceRange (KAN — lab reference ranges).

The master test list ships with every clinical column blank on purpose — see
LabTest's own docstring. This is the other half: a downloadable template
naming every test on the master list, for a doctor to fill in with ranges
they can actually stand behind, and an importer to bring that filled sheet
back in.

**Valid rows import even when others fail**, for the same reason the rota
and holiday importers work this way: a row that failed to import is visibly
absent from the table, and re-uploading the corrected file is safe because
updating an existing band (same test, same sex, same age band) in place is
exactly what a second upload is for.

A row with no ``status`` column is not rejected — it is entered as
REVIEW_REQUIRED, never VALIDATED. Nothing this importer writes can start
driving an automatic normal/abnormal flag on its own; only a status of
VALIDATED does that (see clinical.lab_reference), and that is something a
person sets deliberately, never a side effect of a bulk upload.
"""

import csv
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import LabReferenceRange, LabTest, ReferenceSex, ReferenceStatus

COLUMNS = [
    "test_code", "test_name", "sex", "age_min", "age_max", "age_unit",
    "pregnancy_status", "fasting_status", "low", "high", "unit",
    "source", "source_year", "notes", "status",
]

COLUMN_HELP = {
    "test_code": "Required. Must match a code on the master list — do not change this column.",
    "test_name": "For reference only — not read on import, test_code is what's matched.",
    "sex": "Required. One of: ANY, MALE, FEMALE.",
    "age_min": "Optional. Lowest age this band applies from.",
    "age_max": "Optional. Highest age this band applies to.",
    "age_unit": "years or months. Defaults to years if left blank.",
    "pregnancy_status": "Optional free text, e.g. 'Not pregnant', 'First trimester'.",
    "fasting_status": "Optional free text, e.g. 'Fasting', 'Random'.",
    "low": "Lower bound. Leave blank if this range only has an upper limit.",
    "high": "Upper bound. Leave blank if this range only has a lower limit. At least one of low/high is required.",
    "unit": "Required. The unit low/high are expressed in, e.g. mIU/L.",
    "source": "Required. Where this range comes from, e.g. 'IFCC 2023', 'Lab's own validated range'.",
    "source_year": "Optional. The year of the source, e.g. 2023.",
    "notes": "Optional.",
    "status": (
        "VALIDATED to make this drive an automatic normal/abnormal flag. "
        "Leave blank or write REVIEW_REQUIRED to record it without that yet."
    ),
}

#: A spreadsheet of ranges is a slow, careful clinical task, not a bulk
#: data-entry job — this is a generous ceiling, not a working assumption.
MAX_ROWS = 2000

_STATUS_ALIASES = {c.replace("_", "").lower(): c for c in ReferenceStatus.values}
_SEX_ALIASES = {c.lower(): c for c in ReferenceSex.values}


def template_csv(category=None):
    """
    One row per test on the master list (optionally narrowed to one
    category), test_code and test_name filled in, every clinical column
    blank for the doctor to complete.
    """
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    writer.writerow([COLUMN_HELP[name] for name in COLUMNS])

    tests = LabTest.objects.filter(is_active=True)
    if category:
        tests = tests.filter(category=category)
    for test in tests.order_by("category", "name"):
        writer.writerow([
            test.code, test.name, "", "", "", "years", "", "", "", "", "",
            "", "", "", "",
        ])
    return out.getvalue()


@dataclass
class RowProblem:
    line: int
    message: str


@dataclass
class PlannedRange:
    line: int
    lab_test: object
    fields: dict


@dataclass
class ImportResult:
    planned: list = field(default_factory=list)    # PlannedRange
    problems: list = field(default_factory=list)    # RowProblem
    fatal: str = ""

    @property
    def can_import(self):
        return not self.fatal and bool(self.planned)


def _is_help_row(row):
    return (row.get("test_code") or "").strip() == COLUMN_HELP["test_code"]


def _decimal(value):
    value = (value or "").strip()
    if not value:
        return None, True
    try:
        return Decimal(value), True
    except InvalidOperation:
        return None, False


def parse(file_obj):
    result = ImportResult()

    try:
        text = file_obj.read()
    except Exception:
        result.fatal = "The file could not be read."
        return result

    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8-sig")
        except UnicodeDecodeError:
            result.fatal = "The file is not saved as UTF-8. Re-export it as CSV UTF-8."
            return result

    if "\x00" in text:
        result.fatal = (
            "That is not a CSV file. If it came from Excel, use "
            "File → Save As → CSV UTF-8."
        )
        return result

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        result.fatal = "The file is empty."
        return result

    headings = [(name or "").strip().lower() for name in reader.fieldnames]
    missing = [c for c in COLUMNS if c not in headings]
    if missing:
        result.fatal = (
            "These columns are missing from the file: " + ", ".join(missing)
            + ". Download the template and fill that in instead."
        )
        return result

    tests_by_code = {t.code: t for t in LabTest.objects.all()}
    rows_read = 0

    for number, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): v for k, v in raw.items()}

        if _is_help_row(row) or not any((v or "").strip() for v in row.values()):
            continue

        rows_read += 1
        if rows_read > MAX_ROWS:
            result.fatal = f"More than {MAX_ROWS} rows. Split the file and import in parts."
            return result

        def value(name):
            return (row.get(name) or "").strip()

        code = value("test_code")
        lab_test = tests_by_code.get(code)
        if not code:
            result.problems.append(RowProblem(number, "No test_code."))
            continue
        if lab_test is None:
            result.problems.append(RowProblem(
                number, f"'{code}' is not a code on the master list.",
            ))
            continue

        sex_raw = value("sex")
        sex = _SEX_ALIASES.get(sex_raw.lower())
        if sex is None:
            result.problems.append(RowProblem(
                number, f"sex must be one of ANY, MALE, FEMALE — got '{sex_raw}'.",
            ))
            continue

        age_min, age_min_ok = _decimal(value("age_min"))
        if not age_min_ok:
            result.problems.append(RowProblem(number, f"age_min '{value('age_min')}' is not a number."))
            continue
        age_max, age_max_ok = _decimal(value("age_max"))
        if not age_max_ok:
            result.problems.append(RowProblem(number, f"age_max '{value('age_max')}' is not a number."))
            continue
        if age_min is not None and age_max is not None and age_max < age_min:
            result.problems.append(RowProblem(number, "age_max is less than age_min."))
            continue

        low, low_ok = _decimal(value("low"))
        if not low_ok:
            result.problems.append(RowProblem(number, f"low '{value('low')}' is not a number."))
            continue
        high, high_ok = _decimal(value("high"))
        if not high_ok:
            result.problems.append(RowProblem(number, f"high '{value('high')}' is not a number."))
            continue
        if low is None and high is None:
            result.problems.append(RowProblem(number, "At least one of low/high is required."))
            continue
        if low is not None and high is not None and high < low:
            result.problems.append(RowProblem(number, "high is less than low."))
            continue

        unit = value("unit")
        if not unit:
            result.problems.append(RowProblem(number, "unit is required."))
            continue

        source = value("source")
        if not source:
            result.problems.append(RowProblem(number, "source is required."))
            continue

        source_year_raw = value("source_year")
        source_year = None
        if source_year_raw:
            try:
                source_year = int(source_year_raw)
            except ValueError:
                result.problems.append(RowProblem(
                    number, f"source_year '{source_year_raw}' is not a whole number.",
                ))
                continue

        status_raw = value("status")
        if not status_raw:
            status = ReferenceStatus.REVIEW_REQUIRED
        else:
            status = _STATUS_ALIASES.get(status_raw.replace(" ", "").replace("-", "").lower())
            if status is None:
                result.problems.append(RowProblem(
                    number,
                    f"status '{status_raw}' is not recognised. Use VALIDATED, "
                    f"REVIEW_REQUIRED, DEPRECATED, or leave it blank.",
                ))
                continue

        result.planned.append(PlannedRange(
            line=number, lab_test=lab_test, fields=dict(
                sex=sex, age_min=age_min, age_max=age_max,
                age_unit=value("age_unit") or "years",
                pregnancy_status=value("pregnancy_status"),
                fasting_status=value("fasting_status"),
                low=low, high=high, unit=unit,
                source=source, source_year=source_year,
                notes=value("notes"), status=status,
            ),
        ))

    if not (result.planned or result.problems):
        result.fatal = "The file has headings but no reference ranges in it."

    return result


@transaction.atomic
def commit(result):
    """
    Write the rows that parsed cleanly.

    A row matching an existing band (same test, sex, age range and pregnancy
    status) updates it in place rather than duplicating it — a corrected
    re-upload is meant to replace what was there, not pile a second row on
    top of the first. Returns (created_count, updated_count).
    """
    if result.fatal:
        return 0, 0

    created = updated = 0
    for row in result.planned:
        key = dict(
            lab_test=row.lab_test,
            sex=row.fields["sex"],
            age_min=row.fields["age_min"],
            age_max=row.fields["age_max"],
            pregnancy_status=row.fields["pregnancy_status"],
        )
        defaults = {
            k: v for k, v in row.fields.items()
            if k not in ("sex", "age_min", "age_max", "pregnancy_status")
        }
        _obj, was_created = LabReferenceRange.objects.update_or_create(
            defaults=defaults, **key,
        )
        if was_created:
            created += 1
        else:
            updated += 1
    return created, updated
