"""
Bringing a month of doctor rotas in from a spreadsheet (KAN-22).

One row says "Dr Vrushali, Cabin 1, every Monday, Wednesday and Friday through
September, 09:00 to 13:00" and becomes thirteen dated working-hours entries.
Typing those by hand is thirteen chances to miss one, and a missed one is a
patient booked into a slot no doctor is sitting in.

**Valid rows import even when others fail**, like the holiday importer beside
it and for the same reason: a rota that failed to import is visibly absent from
the calendar, and re-running the corrected file is safe because a duplicate is
refused. Structural problems — wrong headings, an unreadable file — still reject
the whole thing, because then nothing about it can be trusted.

Conflicts are checked exactly as they are for a rota typed into the pop-up. An
importer that skipped the conflict check would be a way to put two doctors in
one cabin by using a spreadsheet, which is not a difference the clinic would
expect or want.
"""

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime

from django.db import transaction

from accounts.models import Role, User

from . import calendar as clinic_calendar
from . import weekdays as weekday_codes
from .models import Cabin, ScheduleOverride

COLUMNS = [
    "doctor_email", "cabin", "start_date", "end_date",
    "days", "start_time", "end_time",
]

#: Both are unambiguous *by shape* — a four-digit first field can only be a
#: year, and a two-digit one can only be a day — so accepting the pair cannot
#: silently read one as the other.
#:
#: The ticket's own template is written DD-MM-YYYY; the holiday importer next
#: door fixed on YYYY-MM-DD. Both are taken here so neither spreadsheet is
#: rejected, but the two importers disagreeing is worth settling — see the note
#: on the ticket.
DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y"]
DATE_HELP = "DD-MM-YYYY or YYYY-MM-DD"

TIME_FORMATS = ["%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"]
TIME_HELP = "24-hour, e.g. 09:00"

COLUMN_HELP = {
    "doctor_email": "Required. Must match a doctor already on the system",
    "cabin": "Required. Must match a cabin already added",
    "start_date": f"Required. {DATE_HELP}",
    "end_date": f"Required. {DATE_HELP}. Not before the start date",
    "days": "Required. M T W Th F Sa Su joined by hyphens, e.g. M-W-F",
    "start_time": f"Required. {TIME_HELP}",
    "end_time": f"Required. {TIME_HELP}. Later than the start time",
}

#: A rota file is a handful of rows per doctor per month, not thousands.
MAX_ROWS = 300


def template_csv():
    """The blank template, carrying its own instructions and worked examples."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    writer.writerow([COLUMN_HELP[name] for name in COLUMNS])
    writer.writerow(["vrushali@example.com", "Cabin 1", "01-09-2026", "30-09-2026",
                     "M-W-F", "09:00", "13:00"])
    writer.writerow(["vrushali@example.com", "Cabin 2", "01-09-2026", "30-09-2026",
                     "T-Th", "14:00", "18:00"])
    writer.writerow(["adway@example.com", "Cabin 1", "01-09-2026", "30-09-2026",
                     "M-T-W-Th-F", "10:00", "16:00"])
    return out.getvalue()


@dataclass
class RowProblem:
    line: int
    message: str


@dataclass
class PlannedRow:
    """One spreadsheet row, expanded into the dates it will create."""

    line: int
    doctor: object
    cabin: object
    dates: list
    start_time: object
    end_time: object
    days: str

    @property
    def count(self):
        return len(self.dates)


@dataclass
class ImportResult:
    planned: list = field(default_factory=list)     # PlannedRow
    problems: list = field(default_factory=list)    # RowProblem
    duplicates: list = field(default_factory=list)  # (line, message)
    fatal: str = ""

    @property
    def can_import(self):
        return not self.fatal and bool(self.planned)

    @property
    def total_entries(self):
        return sum(row.count for row in self.planned)


def _parse_date(value):
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_time(value):
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(value.strip().upper(), fmt).time()
        except ValueError:
            continue
    return None


def _is_help_row(row):
    return (row.get("doctor_email") or "").strip() == COLUMN_HELP["doctor_email"]


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
            text = text.decode("utf-8-sig")
        except UnicodeDecodeError:
            result.fatal = (
                "The file is not saved as UTF-8. Re-export it as CSV UTF-8."
            )
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
            + ". The columns must be " + ", ".join(COLUMNS)
            + ". Download the template and fill that in instead."
        )
        return result

    doctors = {
        user.email.casefold(): user
        for user in User.objects.filter(role=Role.DOCTOR).select_related(
            "doctor_profile"
        )
        if user.email
    }
    cabins = {cabin.name.casefold(): cabin for cabin in Cabin.objects.all()}

    # Everything already on the calendar, plus everything earlier rows in this
    # same file have claimed. Without the second half, two rows in one
    # spreadsheet could put two doctors in one cabin — neither row conflicts
    # with the database, because neither has been written yet.
    claimed = []
    rows_read = 0

    for number, raw in enumerate(reader, start=2):   # line 1 is the header
        row = {(k or "").strip().lower(): v for k, v in raw.items()}

        if _is_help_row(row) or not any((v or "").strip() for v in row.values()):
            continue

        rows_read += 1
        if rows_read > MAX_ROWS:
            result.fatal = f"More than {MAX_ROWS} rows. Split the file and import in parts."
            return result

        def value(name):
            return (row.get(name) or "").strip()

        # ── Doctor ───────────────────────────────────────────────────────────
        email = value("doctor_email")
        doctor = doctors.get(email.casefold())
        if not email:
            result.problems.append(RowProblem(number, "No doctor email."))
            continue
        if doctor is None:
            result.problems.append(RowProblem(
                number, f"No doctor on the system has the email '{email}'.",
            ))
            continue
        if not doctor.is_active:
            result.problems.append(RowProblem(
                number, f"{doctor.display_name} is no longer an active doctor.",
            ))
            continue
        profile = getattr(doctor, "doctor_profile", None)
        if profile is not None and profile.is_pending:
            # Same rule as the pop-up: nobody can sign in as them, so a patient
            # booked into those hours would arrive to an empty cabin.
            result.problems.append(RowProblem(
                number,
                f"{doctor.display_name} has not set their password yet, so they "
                f"cannot be given hours.",
            ))
            continue

        # ── Cabin ────────────────────────────────────────────────────────────
        cabin_name = value("cabin")
        cabin = cabins.get(cabin_name.casefold())
        if cabin is None:
            result.problems.append(RowProblem(
                number,
                f"There is no cabin called '{cabin_name}'. Add it on the "
                f"calendar first, or correct the spelling.",
            ))
            continue

        # ── Dates ────────────────────────────────────────────────────────────
        start_date = _parse_date(value("start_date"))
        end_date = _parse_date(value("end_date"))
        if start_date is None:
            result.problems.append(RowProblem(
                number, f"Start date '{value('start_date')}' is not a date. Use {DATE_HELP}.",
            ))
            continue
        if end_date is None:
            result.problems.append(RowProblem(
                number, f"End date '{value('end_date')}' is not a date. Use {DATE_HELP}.",
            ))
            continue
        if end_date < start_date:
            result.problems.append(RowProblem(
                number, "The end date is before the start date.",
            ))
            continue
        span = (end_date - start_date).days + 1
        if span > weekday_codes.MAX_RANGE_DAYS:
            result.problems.append(RowProblem(
                number,
                f"That range is {span} days. Check the years — the most one row "
                f"may cover is {weekday_codes.MAX_RANGE_DAYS}.",
            ))
            continue

        # ── Times ────────────────────────────────────────────────────────────
        start_time = _parse_time(value("start_time"))
        end_time = _parse_time(value("end_time"))
        if start_time is None:
            result.problems.append(RowProblem(
                number, f"Start time '{value('start_time')}' is not a time. Use {TIME_HELP}.",
            ))
            continue
        if end_time is None:
            result.problems.append(RowProblem(
                number, f"End time '{value('end_time')}' is not a time. Use {TIME_HELP}.",
            ))
            continue
        if end_time <= start_time:
            result.problems.append(RowProblem(
                number, "The end time is not after the start time.",
            ))
            continue

        # ── Days ─────────────────────────────────────────────────────────────
        try:
            wanted = weekday_codes.parse_codes(value("days"))
        except weekday_codes.BadDayCode as bad:
            result.problems.append(RowProblem(number, str(bad)))
            continue

        dates = weekday_codes.dates_in_range(start_date, end_date, wanted)
        if not dates:
            result.problems.append(RowProblem(
                number,
                f"No {weekday_codes.format_codes(wanted)} falls between "
                f"{start_date:%d %b %Y} and {end_date:%d %b %Y}.",
            ))
            continue

        # ── Clashes, against the calendar and against this file ──────────────
        clashes = []
        duplicate_dates = []
        for day in dates:
            if ScheduleOverride.objects.filter(
                doctor=doctor, date=day, cabin=cabin,
                start_time=start_time, end_time=end_time,
            ).exists():
                duplicate_dates.append(day)
                continue

            found = clinic_calendar.find_conflicts(
                kind="override", doctor=doctor, cabin=cabin,
                start=start_time, end=end_time, date=day,
            )
            if found:
                clashes.append(str(found[0]))
                continue

            for other in claimed:
                if other["date"] != day:
                    continue
                if not (start_time < other["end"] and other["start"] < end_time):
                    continue
                if other["doctor"] == doctor.pk:
                    clashes.append(
                        f"{doctor.display_name} is already given other hours on "
                        f"{day:%a %d %b} by line {other['line']} of this file."
                    )
                    break
                if other["cabin"] == cabin.pk:
                    clashes.append(
                        f"{cabin.name} is already taken on {day:%a %d %b} by "
                        f"line {other['line']} of this file."
                    )
                    break

        if clashes:
            result.problems.append(RowProblem(number, clashes[0]))
            continue

        wanted_dates = [d for d in dates if d not in duplicate_dates]
        if duplicate_dates:
            result.duplicates.append((
                number,
                f"{len(duplicate_dates)} of {len(dates)} dates already have "
                f"exactly these hours and are left alone.",
            ))
        if not wanted_dates:
            continue

        for day in wanted_dates:
            claimed.append({
                "line": number, "date": day, "doctor": doctor.pk,
                "cabin": cabin.pk, "start": start_time, "end": end_time,
            })

        result.planned.append(PlannedRow(
            line=number, doctor=doctor, cabin=cabin, dates=wanted_dates,
            start_time=start_time, end_time=end_time,
            days=weekday_codes.format_codes(wanted),
        ))

    if not result.planned and not result.problems and not result.duplicates:
        result.fatal = "The file has headings but no schedules in it."

    return result


@transaction.atomic
def commit(result, created_by=None):
    """Write the rows that parsed cleanly, and nothing else."""
    if result.fatal:
        return []

    written = []
    for row in result.planned:
        for day in row.dates:
            written.append(ScheduleOverride.objects.create(
                doctor=row.doctor,
                date=day,
                cabin=row.cabin,
                start_time=row.start_time,
                end_time=row.end_time,
                note=f"Imported rota ({row.days})",
                created_by=created_by,
            ))
    return written
