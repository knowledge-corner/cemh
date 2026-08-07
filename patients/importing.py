"""
Bringing existing patients in from a spreadsheet.

A clinic changing systems has its patients in a file somewhere, and typing two
thousand of them in by hand is not a plan. This reads the template the same
screen offers for download, checks every row, and then either writes all of
them or none.

All-or-nothing on purpose. A half-finished import is the worst outcome
available: nobody can tell which rows landed, running it again duplicates the
ones that did, and the clinic is left reconciling a spreadsheet against a
database by eye. Refusing the file and naming the bad rows costs one more
attempt and leaves the records trustworthy.
"""

import csv
import io
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from . import matching
from .models import Patient, Sex, age_in_years

#: The template's columns, in order. Everything the registration form asks for.
COLUMNS = [
    "first_name",
    "last_name",
    "date_of_birth",
    "gender",
    "phone",
    "guardian_name",
    "guardian_relation",
]

#: What each column means, written for whoever fills the file in.
COLUMN_HELP = {
    "first_name": "Required",
    "last_name": "Required",
    "date_of_birth": "Required. YYYY-MM-DD, e.g. 1998-04-23",
    "gender": "Required. Male, Female, Other, or Prefer not to say",
    "phone": "Required. 10 digits. +91 and spaces are fine",
    "guardian_name": "Required only if the patient is under 18",
    "guardian_relation": "Required only if the patient is under 18",
}

#: Accepted spellings for the gender column, mapped to what is stored. Written
#: out rather than derived so that a file typed by hand — "M", "male", "F" —
#: is not rejected for a difference nobody would consider meaningful.
GENDER_WORDS = {
    "m": Sex.MALE, "male": Sex.MALE,
    "f": Sex.FEMALE, "female": Sex.FEMALE,
    "o": Sex.OTHER, "other": Sex.OTHER,
    "n": Sex.NOT_STATED,
    "prefer not to say": Sex.NOT_STATED,
    "prefer not to mention": Sex.NOT_STATED,
    "not stated": Sex.NOT_STATED,
    "": Sex.NOT_STATED,
}

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]

GUARDIAN_AGE = 18
MAX_PLAUSIBLE_AGE_YEARS = 120

#: A file larger than this is not a patient list; refuse it before parsing.
MAX_ROWS = 5000


def template_csv():
    """The blank template, with a help row and one example."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    writer.writerow([COLUMN_HELP[name] for name in COLUMNS])
    writer.writerow([
        "Meera", "Kulkarni", "1998-04-23", "Female", "9820012345", "", "",
    ])
    writer.writerow([
        "Rohan", "Kulkarni", "2015-07-02", "Male", "9820012345",
        "Meera Kulkarni", "Mother",
    ])
    return out.getvalue()


@dataclass
class RowProblem:
    line: int
    message: str


@dataclass
class ImportResult:
    created: list = field(default_factory=list)
    skipped: list = field(default_factory=list)   # (line, existing patient)
    problems: list = field(default_factory=list)  # RowProblem

    @property
    def ok(self):
        return not self.problems


def _parse_date(value):
    for fmt in DATE_FORMATS:
        try:
            return timezone.datetime.strptime(value.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _is_help_row(row):
    """The template ships with its own instructions; do not import them."""
    return (row.get("first_name") or "").strip() == COLUMN_HELP["first_name"]


def parse(file_obj):
    """
    Read and check the file without writing anything.

    Returns an :class:`ImportResult`. Rows that match an existing patient are
    reported as skipped rather than as errors — re-importing a file after
    adding a few rows to it is the normal way this gets used, and refusing the
    whole thing because the first hundred are already in would make that
    impossible.
    """
    result = ImportResult()

    try:
        text = file_obj.read()
    except Exception:
        result.problems.append(RowProblem(0, "The file could not be read."))
        return result

    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8-sig")
        except UnicodeDecodeError:
            result.problems.append(RowProblem(
                0, "The file is not saved as UTF-8. Re-export it as CSV UTF-8.",
            ))
            return result

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        result.problems.append(RowProblem(0, "The file is empty."))
        return result

    missing = [c for c in COLUMNS if c not in reader.fieldnames]
    if missing:
        result.problems.append(RowProblem(
            0,
            "These columns are missing from the file: " + ", ".join(missing)
            + ". Download the template and fill that in instead.",
        ))
        return result

    today = timezone.localdate()
    seen_in_file = {}

    for number, row in enumerate(reader, start=2):   # line 1 is the header
        if _is_help_row(row) or not any((v or "").strip() for v in row.values()):
            continue

        if len(result.created) + len(result.skipped) >= MAX_ROWS:
            result.problems.append(RowProblem(
                number, f"More than {MAX_ROWS} rows. Split the file and import it "
                        f"in parts.",
            ))
            return result

        def value(name):
            return (row.get(name) or "").strip()

        first, last, phone = value("first_name"), value("last_name"), value("phone")

        if not first:
            result.problems.append(RowProblem(number, "First name is missing."))
            continue
        if not last:
            result.problems.append(RowProblem(number, "Last name is missing."))
            continue

        digits = matching.normalise_phone(phone)
        if len(digits) < matching.SIGNIFICANT_DIGITS:
            result.problems.append(RowProblem(
                number, f"Phone '{phone}' is not a full number.",
            ))
            continue

        dob = _parse_date(value("date_of_birth"))
        if dob is None:
            result.problems.append(RowProblem(
                number,
                f"Date of birth '{value('date_of_birth')}' is not a date. "
                f"Use YYYY-MM-DD.",
            ))
            continue
        if dob > today:
            result.problems.append(RowProblem(
                number, "Date of birth is in the future.",
            ))
            continue
        if dob < today.replace(year=today.year - MAX_PLAUSIBLE_AGE_YEARS):
            result.problems.append(RowProblem(
                number, f"Date of birth is over {MAX_PLAUSIBLE_AGE_YEARS} years ago.",
            ))
            continue

        gender = GENDER_WORDS.get(value("gender").casefold())
        if gender is None:
            result.problems.append(RowProblem(
                number,
                f"Gender '{value('gender')}' is not one of Male, Female, Other "
                f"or Prefer not to say.",
            ))
            continue

        guardian_name = value("guardian_name")
        guardian_relation = value("guardian_relation")

        if age_in_years(dob, today) < GUARDIAN_AGE:
            if not guardian_name or not guardian_relation:
                result.problems.append(RowProblem(
                    number,
                    f"{first} is under {GUARDIAN_AGE}, so a guardian name and "
                    f"relation are needed.",
                ))
                continue
        else:
            guardian_name = guardian_relation = ""

        # The same person twice inside one file — which a spreadsheet built by
        # copy and paste produces easily, and which nothing in the database can
        # catch because neither row exists yet.
        key = (matching.normalise_name(first), matching.normalise_name(last), digits)
        if key in seen_in_file:
            result.problems.append(RowProblem(
                number, f"Same name and number as line {seen_in_file[key]}.",
            ))
            continue
        seen_in_file[key] = number

        existing = matching.find_duplicates(first, last, phone, limit=1)
        if existing:
            result.skipped.append((number, existing[0]))
            continue

        result.created.append(Patient(
            first_name=first,
            last_name=last,
            date_of_birth=dob,
            sex=gender,
            phone=phone,
            guardian_name=guardian_name,
            guardian_relation=guardian_relation,
        ))

    return result


@transaction.atomic
def commit(result):
    """
    Write the rows that parsed cleanly.

    Saved one at a time rather than in bulk: every patient needs an ID from the
    sequence, and ``bulk_create`` would skip the save that allocates it.
    """
    for patient in result.created:
        patient.save()
    return result.created
