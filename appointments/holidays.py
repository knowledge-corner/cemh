"""
Bringing the year's clinic holidays in from a spreadsheet (KAN-24).

Twenty-odd dates typed one at a time is twenty-odd chances to key the wrong one,
and the clinic already has the list — it comes out every December.

**One date format, and only one.** ``03/04/2026`` is the third of April in
Mumbai and the fourth of March in a US-locale export, and nothing in the file
says which. Guessing would shut the clinic on the wrong day and nobody would
notice until patients arrived, so anything that is not ``YYYY-MM-DD`` is
refused with the format named. The ticket raises this as an open question; the
answer taken here is the only one that cannot silently corrupt the year.

**Valid rows import even when others fail** — KAN-24 FR-4, deliberately the
opposite of the patient importer next door, which is all-or-nothing. The
difference is what a half-finished import costs. A missing patient is invisible
and re-running duplicates the ones that landed; a missing holiday is a date the
receptionist can see is absent from the calendar and add, and the duplicate
check makes re-running the corrected file safe. Structural problems — wrong
headers, unreadable file — still reject the whole thing, because then nothing
about it can be trusted (AC-7).
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction

from .models import ClinicHoliday

#: The template's columns, in order.
COLUMNS = ["holiday_date", "holiday_name", "notes"]

REQUIRED_COLUMNS = ["holiday_date", "holiday_name"]

DATE_FORMAT = "%Y-%m-%d"
DATE_HELP = "YYYY-MM-DD"

COLUMN_HELP = {
    "holiday_date": f"Required. {DATE_HELP}, e.g. 2026-11-08",
    "holiday_name": "Required. Shown on the calendar",
    "notes": "Optional",
}

#: A clinic does not have a thousand holidays. Past this, the file is not a
#: holiday list and reading it further wastes the receptionist's time.
MAX_ROWS = 500


def template_csv():
    """The blank template, carrying its own instructions and two examples."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    writer.writerow([COLUMN_HELP[name] for name in COLUMNS])
    writer.writerow(["2026-01-26", "Republic Day", ""])
    writer.writerow(["2026-11-08", "Diwali", "Clinic closed all day"])
    return out.getvalue()


@dataclass
class RowProblem:
    line: int
    message: str


@dataclass
class ImportResult:
    created: list = field(default_factory=list)     # ClinicHoliday, unsaved
    duplicates: list = field(default_factory=list)  # (line, date, why)
    problems: list = field(default_factory=list)    # RowProblem
    fatal: str = ""                                 # set = nothing may import

    @property
    def ok(self):
        return not self.fatal and not self.problems

    @property
    def can_import(self):
        """FR-4: some rows failing does not stop the good ones."""
        return not self.fatal and bool(self.created)


def _is_help_row(row):
    return (row.get("holiday_date") or "").strip() == COLUMN_HELP["holiday_date"]


def parse(file_obj):
    """Read and check the file without writing anything."""
    result = ImportResult()

    try:
        text = file_obj.read()
    except Exception:
        result.fatal = "The file could not be read."
        return result

    if isinstance(text, bytes):
        try:
            # utf-8-sig strips the byte-order mark Excel writes, which would
            # otherwise turn the first heading into "﻿holiday_date" and
            # make a correct file look as though it had no columns at all.
            text = text.decode("utf-8-sig")
        except UnicodeDecodeError:
            result.fatal = (
                "The file is not saved as UTF-8. Re-export it as CSV UTF-8 — a "
                "holiday name with an accent or a rupee sign will not survive "
                "otherwise."
            )
            return result

    if "\x00" in text:
        # A renamed .xlsx or .doc. Parsing it produces gibberish rows and an
        # error message about column names that is no help to anybody (T-7).
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
    missing = [c for c in REQUIRED_COLUMNS if c not in headings]
    if missing:
        result.fatal = (
            "These columns are missing from the file: " + ", ".join(missing)
            + ". The columns must be " + ", ".join(COLUMNS)
            + ". Download the template and fill that in instead."
        )
        return result

    existing = set(ClinicHoliday.objects.values_list("date", flat=True))
    seen_in_file = {}
    rows_read = 0

    for number, row in enumerate(reader, start=2):   # line 1 is the header
        row = {(k or "").strip().lower(): v for k, v in row.items()}

        if _is_help_row(row) or not any((v or "").strip() for v in row.values()):
            continue

        rows_read += 1
        if rows_read > MAX_ROWS:
            result.fatal = (
                f"More than {MAX_ROWS} rows. That is not a holiday list — check "
                f"the file."
            )
            return result

        def value(name):
            return (row.get(name) or "").strip()

        raw_date, name = value("holiday_date"), value("holiday_name")

        if not raw_date:
            result.problems.append(RowProblem(number, "The date is missing."))
            continue
        try:
            day = datetime.strptime(raw_date, DATE_FORMAT).date()
        except ValueError:
            result.problems.append(RowProblem(
                number,
                f"'{raw_date}' is not a date in {DATE_HELP} format. Dates like "
                f"03/04/2026 mean different days in different countries, so "
                f"they are refused rather than guessed at.",
            ))
            continue

        if not name:
            result.problems.append(RowProblem(
                number,
                f"{day:%d %b %Y} has no name. The calendar shows the name, so "
                f"an unnamed closure tells nobody why.",
            ))
            continue

        if day in seen_in_file:
            # AC/edge case: first wins, later ones are reported rather than
            # silently overwriting a name somebody typed.
            result.duplicates.append(
                (number, day, f"already on line {seen_in_file[day]} of this file")
            )
            continue
        if day in existing:
            result.duplicates.append(
                (number, day, "already recorded as a holiday")
            )
            continue

        seen_in_file[day] = number
        result.created.append(ClinicHoliday(
            date=day, name=name, note=value("notes"),
        ))

    if not result.created and not result.problems and not result.duplicates:
        result.fatal = "The file has headings but no holidays in it."

    return result


@transaction.atomic
def commit(result):
    """Write the rows that parsed cleanly, and nothing else."""
    if result.fatal:
        return []
    for holiday in result.created:
        holiday.save()
    return result.created
