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
from .models import Cabin, DoctorSchedule, Visit

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
class ReplacedDoctor:
    """What "Replace my schedule for these dates" would remove for one doctor."""

    doctor: object
    dates: list
    existing_count: int
    active_visits: int


@dataclass
class ImportResult:
    planned: list = field(default_factory=list)     # PlannedRow
    problems: list = field(default_factory=list)    # RowProblem
    duplicates: list = field(default_factory=list)  # (line, message)
    #: Dates a row named that clash with hours already on record — for the
    #: doctor themselves, another doctor's cabin, or another row of this same
    #: file. Unlike ``problems``, a conflict does not take the whole row out:
    #: the row is still planned, minus just these dates — see parse(), which
    #: mirrors ``duplicates`` in that respect. RowProblem is reused as the
    #: shape rather than a new dataclass, since "which line, what happened" is
    #: exactly what both need to say.
    conflicts: list = field(default_factory=list)   # RowProblem
    fatal: str = ""
    #: Populated only when parsed with ``replace=True`` — see ReplacedDoctor.
    to_remove: list = field(default_factory=list)

    @property
    def can_import(self):
        return not self.fatal and bool(self.planned)

    @property
    def total_entries(self):
        return sum(row.count for row in self.planned)

    @property
    def total_to_remove(self):
        return sum(item.existing_count for item in self.to_remove)


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


def parse(file_obj, replace=False):
    """
    Read and check the file without writing anything.

    ``replace`` is "Replace my schedule for these dates": when set, a row
    that would otherwise clash with *the same doctor's own* existing hours on
    a date the row covers is no longer refused — those existing entries are
    what commit() removes before writing the new ones. A clash with another
    doctor already holding the cabin still refuses the row; replacing one
    doctor's hours must never silently take a room from somebody else.
    """
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
    # Retired cabins are kept, never deleted — see Cabin's own docstring — so
    # a name that used to work still matches something. Looked up separately
    # so a row naming one gets told it was retired rather than "no such cabin",
    # which is what every other cabin picker in the app already restricts to.
    cabins = {c.name.casefold(): c for c in Cabin.objects.filter(is_active=True)}
    retired_cabins = {c.name.casefold(): c for c in Cabin.objects.filter(is_active=False)}

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
            if cabin_name.casefold() in retired_cabins:
                result.problems.append(RowProblem(
                    number,
                    f"'{cabin_name}' has been retired and can no longer be "
                    f"scheduled into. Choose an active cabin.",
                ))
            else:
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
        #
        # A clash takes out only the dates it actually names, not the whole
        # row — the same "mention the conflict, offer to book what's free"
        # shape as the pop-up's own Conflict Detected dialog. Confirming the
        # import is the moment that choice is made: the preview names every
        # conflicting date before anything is written, and pressing Confirm
        # is choosing to skip them and keep the rest.
        clashed_dates = []   # [(day, message)]
        duplicate_dates = []
        for day in dates:
            # In replace mode commit() deletes every existing entry across
            # the dates this row covers and writes the file fresh, so an
            # exact match today is not left alone — it would be deleted and
            # never rewritten if skipped here as "already there".
            if not replace and DoctorSchedule.objects.filter(
                doctor=doctor, date=day, cabin=cabin,
                start_time=start_time, end_time=end_time,
            ).exists():
                duplicate_dates.append(day)
                continue

            found = clinic_calendar.find_conflicts(
                doctor=doctor, cabin=cabin,
                start=start_time, end=end_time, dates=[day],
            )
            if replace:
                # A "doctor" conflict is always this same doctor's own
                # existing entry — see Conflict.reason in appointments.calendar
                # — which commit() removes before writing the new one. A
                # "cabin" conflict is somebody *else* already holding the
                # room, which replacing this doctor's own hours cannot excuse.
                found = [c for c in found if c.reason != "doctor"]
            if found:
                clashed_dates.append((day, str(found[0])))
                continue

            for other in claimed:
                if other["date"] != day:
                    continue
                if not (start_time < other["end"] and other["start"] < end_time):
                    continue
                if other["doctor"] == doctor.pk:
                    clashed_dates.append((day,
                        f"{doctor.display_name} is already given other hours on "
                        f"{day:%a %d %b} by line {other['line']} of this file."
                    ))
                    break
                if other["cabin"] == cabin.pk:
                    clashed_dates.append((day,
                        f"{cabin.name} is already taken on {day:%a %d %b} by "
                        f"line {other['line']} of this file."
                    ))
                    break

        if clashed_dates:
            shown = ", ".join(f"{d:%d %b} ({start_time:%I:%M %p}-{end_time:%I:%M %p})"
                              for d, _msg in clashed_dates[:5])
            if len(clashed_dates) > 5:
                shown += f" and {len(clashed_dates) - 5} more"
            result.conflicts.append(RowProblem(
                number,
                f"{len(clashed_dates)} of {len(dates)} date"
                f"{'' if len(clashed_dates) == 1 else 's'} conflict"
                f"{'s' if len(clashed_dates) == 1 else ''} with hours already on "
                f"record and will be skipped if you continue: {shown}.",
            ))

        wanted_dates = [
            d for d in dates
            if d not in duplicate_dates and d not in {c[0] for c in clashed_dates}
        ]
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

    if not (result.planned or result.problems or result.duplicates or result.conflicts):
        result.fatal = "The file has headings but no schedules in it."

    if replace and result.planned:
        by_doctor_dates = {}
        for row in result.planned:
            by_doctor_dates.setdefault(row.doctor, set()).update(row.dates)
        for row_doctor, row_dates in by_doctor_dates.items():
            existing_count = DoctorSchedule.objects.filter(
                doctor=row_doctor, date__in=row_dates,
            ).count()
            if not existing_count:
                continue
            active_visits = Visit.objects.filter(
                doctor=row_doctor, scheduled_start__date__in=row_dates,
            ).active().count()
            result.to_remove.append(ReplacedDoctor(
                doctor=row_doctor, dates=sorted(row_dates),
                existing_count=existing_count, active_visits=active_visits,
            ))

    return result


@transaction.atomic
def commit(result, created_by=None, replace=False):
    """
    Write the rows that parsed cleanly, and nothing else.

    In replace mode, first clears every existing entry for each doctor across
    exactly the dates their rows in this file cover — not the whole calendar,
    only the dates this upload actually mentions — then writes the file
    fresh. Returns ``(written, removed)``.
    """
    if result.fatal:
        return [], 0

    removed = 0
    if replace:
        by_doctor_dates = {}
        for row in result.planned:
            by_doctor_dates.setdefault(row.doctor, set()).update(row.dates)
        for row_doctor, row_dates in by_doctor_dates.items():
            deleted, _detail = DoctorSchedule.objects.filter(
                doctor=row_doctor, date__in=row_dates,
            ).delete()
            removed += deleted

    written = []
    for row in result.planned:
        for day in row.dates:
            written.append(DoctorSchedule.objects.create(
                doctor=row.doctor,
                date=day,
                cabin=row.cabin,
                start_time=row.start_time,
                end_time=row.end_time,
                note=f"Imported rota ({row.days})",
                created_by=created_by,
            ))
    return written, removed
