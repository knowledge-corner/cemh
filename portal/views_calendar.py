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
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.models import Role, Specialisation, User
from accounts.permissions import role_required
from appointments import calendar as clinic_calendar
from appointments import holidays
from appointments.models import (
    Cabin, ClinicHoliday, DoctorLeave, DoctorSchedule, ScheduleOverride,
)
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
    if specialisation_id:
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

    if view == DAY:
        span_start = span_end = anchor
    else:
        span_start, span_end = clinic_calendar.month_range(anchor)

    schedule = clinic_calendar.Schedule(doctors, start=span_start, end=span_end)

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
        "event_form": clinic_forms.CalendarEventForm(
            initial={"date": anchor, "repeat": clinic_forms.CalendarEventForm.ONCE}
        ),
        "cabin_form": clinic_forms.CabinForm(),
        # Retired cabins included, so a cabin that has vanished from the
        # dropdowns can be found and brought back rather than added twice.
        "all_cabins": Cabin.objects.all(),
        # Kept on every navigation link so a filter survives moving month
        # (AC-9). Built here rather than in the template: a half-built query
        # string in three places is how one of them loses the filter.
        "filter_query": _filter_query(request),
        "has_doctors": bool(doctors),
    }

    if view == DAY:
        context.update({
            "columns": clinic_calendar.day_columns(anchor, schedule, cabins),
            "holiday": schedule.holidays.get(anchor),
            "leave": schedule.leave.get(anchor, []),
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
    if not form.is_valid():
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


# ── Deleting, which is how leave is recorded (FR-15, FR-16) ──────────────────

@role_required(Role.RECEPTIONIST)
def delete_calendar_entry(request, kind, pk):
    """
    Remove working hours — for one date, or the whole weekly pattern.

    KAN-22 records leave by deleting entries, and warns in its own open
    questions that this leaves nothing to report on afterwards. Deleting a
    single occurrence of a weekly pattern cannot delete the row anyway — the row
    is every other week too — so that case writes a :class:`DoctorLeave` for the
    date instead. That both answers FR-16 and leaves the positive record the
    ticket says is missing.
    """
    if request.method != "POST":
        return redirect("reception_calendar")

    scope = request.POST.get("scope", "series")

    if kind == "holiday":
        holiday = get_object_or_404(ClinicHoliday, pk=pk)
        description = str(holiday)
        holiday.delete()
        record(request, AuditAction.DELETE, description=f"Holiday removed: {description}")
        messages.success(request, f"{description} removed.")
        return _back(request)

    if kind == "leave":
        leave = get_object_or_404(DoctorLeave, pk=pk)
        description = str(leave)
        leave.delete()
        record(request, AuditAction.DELETE, description=f"Leave removed: {description}")
        messages.success(request, "Leave removed.")
        return _back(request)

    if kind == "override":
        override = get_object_or_404(ScheduleOverride, pk=pk)
        description = str(override)
        override.delete()
        record(request, AuditAction.DELETE, description=f"Hours removed: {description}")
        messages.success(request, "Those hours were removed.")
        return _back(request)

    if kind != "schedule":
        return redirect("reception_calendar")

    sitting = get_object_or_404(DoctorSchedule, pk=pk)

    if scope == "series":
        description = str(sitting)
        sitting.delete()
        record(request, AuditAction.DELETE,
               description=f"Weekly hours removed: {description}")
        messages.success(request, "The whole weekly pattern was removed.")
        return _back(request)

    try:
        day = datetime.strptime(request.POST.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        messages.error(
            request,
            "Which date to remove was not given, so nothing was changed.",
        )
        return _back(request)

    leave = DoctorLeave.objects.create(
        doctor=sitting.doctor,
        date=day,
        start_time=sitting.start_time,
        end_time=sitting.end_time,
        reason="Removed from the calendar",
        created_by=request.user,
    )
    record(request, AuditAction.CREATE, obj=leave,
           description=f"One occurrence removed: {leave}")

    stranded = leave.affected_visits()
    if stranded:
        names = ", ".join(
            f"{v.patient.full_name} ({timezone.localtime(v.scheduled_start):%H:%M})"
            for v in stranded
        )
        messages.warning(
            request,
            f"{len(stranded)} patient{'' if len(stranded) == 1 else 's'} already "
            f"booked with {sitting.doctor.display_name} on {day:%d %b} must be "
            f"rung and moved: {names}",
        )
    else:
        messages.success(
            request,
            f"{sitting.doctor.display_name} is off on {day:%d %b}. The rest of "
            f"the weekly pattern is untouched.",
        )
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
