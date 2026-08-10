"""
The availability calendar (KAN-22).

A month view for "who is in this week" and a cabin-oriented day view for "which
room is free at three o'clock" — the second being the question a list ordered by
doctor cannot answer.

Both draw from :mod:`appointments.calendar`, which is also what the conflict
check consults, so what reception is shown and what the system will accept
cannot disagree.

Doctors reach the same screen. It is scoped to them **here**, from
``request.user``, not from a filter they could change: FR-7 is a rule about who
may see what, and a rule enforced by a dropdown is not enforced at all.
"""

from datetime import date, datetime, timedelta

from django.contrib import messages
from django.db.models import ProtectedError
from django.http import HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Role, Specialisation, User
from accounts.permissions import role_required
from appointments import calendar as clinic_calendar
from appointments import holidays
from appointments import schedules_csv
from appointments.models import Cabin, ClinicHoliday, DoctorSchedule
from audit.models import AuditAction
from audit.services import record
from website.models import CallbackRequest, CallbackStatus

from . import forms as clinic_forms

MONTH = "month"
DAY = "day"


def _requested_date(request):
    """The date the calendar is anchored on — today unless one was asked for."""
    raw = request.GET.get("date", "")
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return timezone.localdate()


def _is_doctor(user):
    return user.role == Role.DOCTOR and not user.is_superuser


def _visible_doctors(request):
    """
    Which doctors this user may see, before any filter they chose is applied.

    A doctor sees themselves and nobody else. Reception sees everyone active.
    """
    if _is_doctor(request.user):
        return User.objects.filter(pk=request.user.pk)
    return User.objects.filter(role=Role.DOCTOR, is_active=True).select_related(
        "doctor_profile", "doctor_profile__specialisation"
    )


def _pending_doctors(request):
    """
    Doctors added but not yet activated (KAN-21 FR-7).

    Empty for a doctor's own view — whose invitation is outstanding is
    reception's business, not something to show one doctor about another.
    """
    if _is_doctor(request.user):
        return User.objects.none()
    return User.objects.filter(
        role=Role.DOCTOR, doctor_profile__activated_at__isnull=True,
    ).select_related("doctor_profile").order_by("first_name")


def _apply_filters(request, doctors):
    """The doctor and specialisation filters, each defaulting to All (FR-5)."""
    chosen_doctor = None
    chosen_specialisation = None

    doctor_id = request.GET.get("doctor") or ""
    if doctor_id and not _is_doctor(request.user):
        chosen_doctor = doctors.filter(pk=doctor_id).first()
        if chosen_doctor is not None:
            doctors = doctors.filter(pk=chosen_doctor.pk)

    specialisation_id = request.GET.get("specialisation") or ""
    if specialisation_id and not _is_doctor(request.user):
        # A single doctor's own calendar is already scoped to themselves —
        # filtering that one-doctor list by a specialisation is meaningless at
        # best, and at worst a stray URL parameter empties their own calendar.
        chosen_specialisation = Specialisation.objects.filter(
            pk=specialisation_id
        ).first()
        if chosen_specialisation is not None:
            doctors = doctors.filter(
                doctor_profile__specialisation=chosen_specialisation
            )

    return doctors, chosen_doctor, chosen_specialisation


@role_required(Role.RECEPTIONIST, Role.DOCTOR)
def calendar_view(request):
    """The calendar itself, in whichever view was asked for."""
    view = DAY if request.GET.get("view") == DAY else MONTH
    anchor = _requested_date(request)

    doctors, chosen_doctor, chosen_specialisation = _apply_filters(
        request, _visible_doctors(request)
    )
    doctors = list(doctors)

    cabins = clinic_calendar.active_cabins()

    # Retired cabins included, so a cabin that has vanished from the dropdowns
    # can be found and brought back rather than added twice. Each one carries
    # whether it still has a future time slot against it, so the template can
    # refuse to offer Retire on a cabin doctors are still working out of.
    still_scheduled = _cabins_still_scheduled()
    all_cabins = list(Cabin.objects.all())
    for cabin in all_cabins:
        cabin.still_scheduled = cabin.pk in still_scheduled

    if view == DAY:
        span_start = span_end = anchor
    else:
        span_start, span_end = clinic_calendar.month_range(anchor)

    schedule = clinic_calendar.Schedule(doctors, start=span_start, end=span_end)

    # A submission that came back with conflicts to resolve — see
    # add_calendar_event, which stashes the raw POST data here rather than
    # rendering a response itself, so the add-event pop-up can be reopened
    # exactly as it was left, values intact, with the Conflict Detected
    # dialog on top of it. Read once and discarded, the same as a Django
    # message, so refreshing this page never reopens it a second time.
    pending = request.session.pop("pending_event_conflict", None)
    if pending:
        event_form = clinic_forms.CalendarEventForm(QueryDict(pending))
        event_form.is_valid()
        event_conflicts = getattr(event_form, "_conflicts", [])
    else:
        event_form = clinic_forms.CalendarEventForm(initial={"date": anchor})
        event_conflicts = []

    context = {
        "view": view,
        "anchor": anchor,
        "today": timezone.localdate(),
        "cabins": cabins,
        "doctors": _visible_doctors(request),
        "specialisations": Specialisation.objects.filter(is_active=True),
        "chosen_doctor": chosen_doctor,
        "chosen_specialisation": chosen_specialisation,
        "is_doctor_view": _is_doctor(request.user),
        "event_form": event_form,
        "reopen_event_modal": bool(pending),
        "event_conflicts": event_conflicts,
        "cabin_form": clinic_forms.CabinForm(),
        "all_cabins": all_cabins,
        # Kept on every navigation link so a filter survives moving month
        # (AC-9). Built here rather than in the template: a half-built query
        # string in three places is how one of them loses the filter.
        "filter_query": _filter_query(request),
        "has_doctors": bool(doctors),
        # KAN-21 AC-3, and KAN-50's half of it: doctors who have not set a
        # password are deliberately absent from the pickers here, and a name
        # that is simply missing reads as the system having lost a doctor
        # rather than as a step nobody has finished. The availability screen
        # used to say so; this is the only screen left that can.
        "pending_doctors": _pending_doctors(request),
    }

    if view == DAY:
        context.update({
            "columns": clinic_calendar.day_columns(anchor, schedule, cabins),
            "holiday": schedule.holidays.get(anchor),
            "previous": anchor - timedelta(days=1),
            "next": anchor + timedelta(days=1),
        })
    else:
        context.update({
            "weeks": clinic_calendar.month_weeks(anchor, schedule),
            "previous": _shift_month(anchor, -1),
            "next": _shift_month(anchor, +1),
        })

    return render(request, "portal/reception/calendar.html", context)


def _filter_query(request):
    parts = []
    for key in ("doctor", "specialisation", "view"):
        value = request.GET.get(key)
        if value:
            parts.append(f"{key}={value}")
    return ("&" + "&".join(parts)) if parts else ""


def _shift_month(anchor, months):
    """The same day-of-month one month either side, clamped to the 1st."""
    month = anchor.month + months
    year = anchor.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, 1)


def _back(request, fallback="reception_calendar"):
    """Return to the calendar the user was looking at."""
    target = request.POST.get("next") or ""
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(fallback)


def _cabins_still_scheduled():
    """Which cabins have a future dated entry against them."""
    return set(
        DoctorSchedule.objects.filter(
            date__gte=timezone.localdate(), cabin_id__isnull=False,
        ).values_list("cabin_id", flat=True)
    )


# ── Cabins (FR-1, FR-2) ──────────────────────────────────────────────────────

@role_required(Role.RECEPTIONIST)
def add_cabin(request):
    if request.method != "POST":
        return redirect("reception_calendar")

    form = clinic_forms.CabinForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return _back(request)

    cabin = form.save(commit=False)
    cabin.created_by = request.user
    cabin.save()
    record(request, AuditAction.CREATE, obj=cabin, description=f"Cabin added: {cabin}")
    messages.success(request, f"{cabin.name} added.")
    return _back(request)


@role_required(Role.RECEPTIONIST)
def retire_cabin(request, pk):
    """
    Take a cabin out of the dropdowns without touching what it was used for.

    Deleting is offered only while nothing references it; the database refuses
    the rest, and it is right to — a room's history is not something to lose in
    order to tidy a list.
    """
    if request.method != "POST":
        return redirect("reception_calendar")

    cabin = get_object_or_404(Cabin, pk=pk)
    if request.POST.get("action") == "delete":
        try:
            name = cabin.name
            cabin.delete()
        except ProtectedError:
            messages.error(
                request,
                f"{cabin.name} is on doctors' working hours, so it cannot be "
                f"deleted. Retire it instead — it stays where it is already "
                f"used and stops being offered for new entries.",
            )
            return _back(request)
        record(request, AuditAction.DELETE, description=f"Cabin deleted: {name}")
        messages.success(request, f"{name} deleted.")
        return _back(request)

    if cabin.is_active and cabin.pk in _cabins_still_scheduled():
        # The button is disabled for exactly this reason, but a disabled
        # button is only a hint — the rule has to hold here too.
        messages.error(
            request,
            f"{cabin.name} still has a doctor's hours booked against it in "
            f"the future, so it cannot be retired yet. Remove those first.",
        )
        return _back(request)

    cabin.is_active = not cabin.is_active
    cabin.save(update_fields=["is_active"])
    record(request, AuditAction.UPDATE, obj=cabin,
           description=f"Cabin {'brought back' if cabin.is_active else 'retired'}: {cabin}")
    messages.success(
        request,
        f"{cabin.name} {'is available again' if cabin.is_active else 'retired'}.",
    )
    return _back(request)


# ── The add-event pop-up (FR-8 … FR-13, FR-20) ───────────────────────────────

@role_required(Role.RECEPTIONIST)
def add_calendar_event(request):
    if request.method != "POST":
        return redirect("reception_calendar")

    form = clinic_forms.CalendarEventForm(request.POST)
    is_valid = form.is_valid()

    if getattr(form, "_conflicts", None):
        # Nothing has been written. The submission is held rather than
        # refused outright — calendar_view reopens this same form, values
        # intact, with the conflicting dates named and a choice: skip just
        # those dates, or go back and change the booking.
        request.session["pending_event_conflict"] = request.POST.urlencode()
        return _back(request)

    if not is_valid:
        for field, errors in form.errors.items():
            for error in errors:
                label = "" if field == "__all__" else f"{form.fields[field].label}: "
                messages.error(request, f"{label}{error}")
        return _back(request)

    created = form.save(created_by=request.user)
    for obj in created:
        record(request, AuditAction.CREATE, obj=obj, description=f"Calendar: {obj}")

    if not created:
        messages.warning(request, "Nothing new to add — that was already recorded.")
    elif len(created) == 1:
        messages.success(request, f"Added: {created[0]}.")
    else:
        messages.success(request, f"Added {len(created)} entries.")

    return _back(request)


# ── Clinic holidays from a spreadsheet (KAN-24) ──────────────────────────────

@role_required(Role.RECEPTIONIST)
def holiday_template(request):
    """The blank CSV, with its own instructions and the date format in it."""
    response = HttpResponse(holidays.template_csv(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="clinic-holidays.csv"'
    return response


@role_required(Role.RECEPTIONIST)
def import_holidays(request):
    """
    Load a year of holidays from the template.

    Two passes, like the patient import: the first reads the file and reports
    what it found, and nothing is written until reception presses the second
    button having seen it.

    Where this differs is what happens when some rows are wrong. The patient
    import refuses the whole file; this one offers to take the good rows and
    lists the rest (KAN-24 FR-4). A holiday that failed to import is a date
    visibly absent from the calendar, and the duplicate check makes re-running
    the corrected file safe — neither of which is true of a patient.
    """
    result = None

    if request.method == "POST":
        upload = request.FILES.get("file")

        if upload is None:
            messages.error(request, "Choose a CSV file first.")
        else:
            result = holidays.parse(upload)

            if result.fatal:
                messages.error(request, result.fatal)
            elif request.POST.get("confirm") and result.can_import:
                created = holidays.commit(result)
                for holiday in created:
                    record(request, AuditAction.CREATE, obj=holiday,
                           description=f"Holiday imported: {holiday}")
                messages.success(
                    request,
                    f"Imported {len(created)} holiday"
                    f"{'' if len(created) == 1 else 's'}."
                    + (f" {len(result.duplicates)} were already recorded."
                       if result.duplicates else "")
                    + (f" {len(result.problems)} row"
                       f"{'' if len(result.problems) == 1 else 's'} could not be "
                       f"read and were left out."
                       if result.problems else ""),
                )
                return redirect("reception_calendar")

    return render(request, "portal/reception/import_holidays.html", {
        "result": result,
        "columns": [(name, holidays.COLUMN_HELP[name]) for name in holidays.COLUMNS],
        "date_help": holidays.DATE_HELP,
    })


# ── Deleting a schedule entry (FR-15, FR-16) ──────────────────────────────────

@role_required(Role.RECEPTIONIST, Role.DOCTOR)
def delete_calendar_entry(request, kind, pk):
    """
    Remove a schedule entry — just this date, or every date in its booking.

    Every entry is one dated row now, so "just this date" is simply deleting
    that row. A recurring booking's rows share one ``series_id``, generated
    when it was added; "the whole booking" is deleting every row with that id.

    A doctor reaching this is editing their own calendar, not reception's: the
    calendar already shows them only their own entries (see
    ``_visible_doctors``), and the querysets below are additionally scoped to
    ``request.user`` so a crafted request against somebody else's pk 404s
    exactly as if it did not exist, rather than saying whose it really is. A
    clinic holiday is never a doctor's to remove, and removing a whole booking
    in one action is reception's call, not offered here.
    """
    if request.method != "POST":
        return redirect("reception_calendar")

    scope = request.POST.get("scope", "date")
    is_doctor = _is_doctor(request.user)

    if is_doctor:
        if kind != "schedule":
            return redirect("reception_calendar")
        if scope == "series":
            messages.error(
                request,
                "Removing a whole booking isn't available here — ask reception, "
                "or upload a corrected schedule for your own recurring hours.",
            )
            return _back(request)

    if kind == "holiday":
        holiday = get_object_or_404(ClinicHoliday, pk=pk)
        description = str(holiday)
        holiday.delete()
        record(request, AuditAction.DELETE, description=f"Holiday removed: {description}")
        messages.success(request, f"{description} removed.")
        return _back(request)

    if kind != "schedule":
        return redirect("reception_calendar")

    schedule_qs = (
        DoctorSchedule.objects.filter(doctor=request.user)
        if is_doctor else DoctorSchedule.objects.all()
    )
    entry = get_object_or_404(schedule_qs, pk=pk)

    if scope == "series" and entry.series_id:
        series_qs = DoctorSchedule.objects.filter(
            doctor=entry.doctor, series_id=entry.series_id,
        )
        count = series_qs.count()
        series_qs.delete()
        record(
            request, AuditAction.DELETE,
            description=f"Whole booking removed: {entry.doctor.display_name}, "
                        f"{count} date{'' if count == 1 else 's'}",
        )
        messages.success(
            request,
            f"The whole booking was removed — {count} "
            f"date{'' if count == 1 else 's'} for {entry.doctor.display_name}.",
        )
        return _back(request)

    description = str(entry)
    entry.delete()
    record(request, AuditAction.DELETE, description=f"Hours removed: {description}")
    messages.success(request, "Those hours were removed.")
    return _back(request)


# ── Callback requests from the public page ───────────────────────────────────

@role_required(Role.RECEPTIONIST)
def callbacks(request):
    """
    People who asked, on the public website, to be rung back.

    This screen is what makes the form on that page mean anything. A request
    that lands in a table nobody opens is worse than no form at all: the
    visitor has been told the clinic will call, and it will not.
    """
    outstanding = CallbackRequest.objects.filter(status=CallbackStatus.NEW)
    return render(request, "portal/reception/callbacks.html", {
        "outstanding": outstanding,
        "handled": CallbackRequest.objects.exclude(
            status=CallbackStatus.NEW
        ).select_related("handled_by")[:50],
        "nav_active": "callbacks",
    })


@role_required(Role.RECEPTIONIST)
def close_callback(request, pk):
    """Mark a callback dealt with, recording who did it and when."""
    if request.method != "POST":
        return redirect("reception_callbacks")

    wanted = request.POST.get("status")
    if wanted not in (CallbackStatus.DONE, CallbackStatus.IGNORED):
        return redirect("reception_callbacks")

    callback = get_object_or_404(CallbackRequest, pk=pk)
    callback.close(wanted, by_user=request.user)
    record(request, AuditAction.UPDATE, obj=callback,
           description=f"Callback {callback.get_status_display().lower()}: {callback}")
    messages.success(
        request,
        f"{callback.name} marked {callback.get_status_display().lower()}.",
    )
    return _back(request, "reception_callbacks")


# ── Doctor rotas from a spreadsheet (KAN-22) ─────────────────────────────────

@role_required(Role.RECEPTIONIST, Role.DOCTOR)
def schedule_template(request):
    """The blank CSV, with its own instructions and the day codes in it."""
    response = HttpResponse(schedules_csv.template_csv(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="doctor-schedules.csv"'
    return response


@role_required(Role.RECEPTIONIST)
def import_schedules(request):
    """
    Load a month of rotas from the template.

    Two passes, like the other two importers: the first reads the file and
    reports what it found — including how many dated entries each row will
    become — and nothing is written until reception has seen that and pressed
    the second button. One line saying "M-W-F through September" turning into
    thirteen entries is exactly the sort of thing somebody should see the size
    of before it happens.
    """
    result = None
    replace = request.POST.get("replace") == "1"

    if request.method == "POST":
        upload = request.FILES.get("file")

        if upload is None:
            messages.error(request, "Choose a CSV file first.")
        else:
            result = schedules_csv.parse(upload, replace=replace)

            if result.fatal:
                messages.error(request, result.fatal)
            elif request.POST.get("confirm") and result.can_import:
                written, removed = schedules_csv.commit(
                    result, created_by=request.user, replace=replace,
                )
                for entry in written:
                    record(request, AuditAction.CREATE, obj=entry,
                           description=f"Rota imported: {entry}")
                if removed:
                    record(
                        request, AuditAction.UPDATE, obj=None,
                        description=f"Rota replace: {removed} existing entr"
                                    f"{'y' if removed == 1 else 'ies'} removed before import",
                    )

                replaced_note = (
                    f"Replaced {removed} existing entr{'y' if removed == 1 else 'ies'} and i"
                    if removed else "I"
                )
                left_out_note = (
                    f" {len(result.problems)} row{'' if len(result.problems) == 1 else 's'} "
                    f"could not be read and were left out." if result.problems else ""
                )
                messages.success(
                    request,
                    f"{replaced_note}mported {len(written)} working-hours "
                    f"entr{'y' if len(written) == 1 else 'ies'} from "
                    f"{len(result.planned)} row{'' if len(result.planned) == 1 else 's'}."
                    f"{left_out_note}",
                )
                return redirect("reception_calendar")

    return render(request, "portal/reception/import_schedules.html", {
        "result": result,
        "replace": replace,
        "columns": [(name, schedules_csv.COLUMN_HELP[name])
                    for name in schedules_csv.COLUMNS],
        "day_codes": schedules_csv.weekday_codes.DAY_CODES,
        "date_help": schedules_csv.DATE_HELP,
    })


@role_required(Role.DOCTOR)
def import_own_schedule(request):
    """
    A doctor's own version of ``import_schedules`` — same file format, same
    two-pass check-then-confirm, same "Replace my schedule for these dates"
    option, but fenced to their own hours only.

    Reception's importer trusts whoever is filling in the email column
    because reception is entering someone else's hours by design. Here the
    doctor is the one at the keyboard, so any row for a different email is
    refused rather than imported — a doctor's own upload must never be able
    to move another doctor's hours, on purpose or by a copied-down row.
    """
    result = None
    replace = request.POST.get("replace") == "1"

    if request.method == "POST":
        upload = request.FILES.get("file")

        if upload is None:
            messages.error(request, "Choose a CSV file first.")
        else:
            result = schedules_csv.parse(upload, replace=replace)

            if not result.fatal:
                mine, foreign = [], []
                for row in result.planned:
                    (mine if row.doctor.pk == request.user.pk else foreign).append(row)
                for row in foreign:
                    result.problems.append(schedules_csv.RowProblem(
                        row.line,
                        f"Row {row.line} is for {row.doctor.display_name} — "
                        f"you can only upload your own schedule.",
                    ))
                result.planned = mine
                # Anything the replace preview found for another doctor must
                # never have been computed from this doctor's own upload —
                # drop it along with their rows.
                result.to_remove = [
                    item for item in result.to_remove if item.doctor.pk == request.user.pk
                ]

            if result.fatal:
                messages.error(request, result.fatal)
            elif request.POST.get("confirm") and result.can_import:
                written, removed = schedules_csv.commit(
                    result, created_by=request.user, replace=replace,
                )
                for entry in written:
                    record(request, AuditAction.CREATE, obj=entry,
                           description=f"Own rota imported: {entry}")

                replaced_note = (
                    f"Replaced {removed} existing entr{'y' if removed == 1 else 'ies'} and i"
                    if removed else "I"
                )
                left_out_note = (
                    f" {len(result.problems)} row{'' if len(result.problems) == 1 else 's'} "
                    f"could not be used." if result.problems else ""
                )
                messages.success(
                    request,
                    f"{replaced_note}mported {len(written)} working-hours "
                    f"entr{'y' if len(written) == 1 else 'ies'}.{left_out_note}",
                )
                return redirect("reception_calendar")

    return render(request, "portal/doctor/import_own_schedule.html", {
        "result": result,
        "replace": replace,
        "columns": [(name, schedules_csv.COLUMN_HELP[name])
                    for name in schedules_csv.COLUMNS],
        "day_codes": schedules_csv.weekday_codes.DAY_CODES,
        "date_help": schedules_csv.DATE_HELP,
    })


# ── Editing a holiday (KAN-24) ───────────────────────────────────────────────

@role_required(Role.RECEPTIONIST)
def edit_holiday(request, pk):
    """
    Change a holiday's name or date.

    KAN-24 asks for add, edit and delete; only add and delete were built, so a
    holiday entered on the wrong date had to be deleted and re-added. That is
    the same two clicks, but it loses who recorded it and when.
    """
    holiday = get_object_or_404(ClinicHoliday, pk=pk)
    before = str(holiday)

    if request.method == "POST":
        form = clinic_forms.ClinicHolidayForm(request.POST, instance=holiday)
        if form.is_valid():
            form.save()
            record(request, AuditAction.UPDATE, obj=holiday,
                   description=f"Holiday changed: {before} -> {holiday}")
            messages.success(request, f"{holiday.name} updated.")
            return _back(request)
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    else:
        form = clinic_forms.ClinicHolidayForm(instance=holiday)

    return render(request, "portal/reception/edit_holiday.html", {
        "form": form,
        "holiday": holiday,
    })
