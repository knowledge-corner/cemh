from django.contrib import admin

from .models import ClinicHoliday, DoctorSchedule, Visit, VisitStatusEvent


class VisitStatusEventInline(admin.TabularInline):
    model = VisitStatusEvent
    extra = 0
    can_delete = False
    readonly_fields = ("from_status", "to_status", "changed_by", "note", "created_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ("patient", "doctor", "scheduled_start", "status", "is_follow_up")
    list_filter = ("status", "is_follow_up", "doctor")
    search_fields = ("patient__patient_id", "patient__first_name", "patient__last_name")
    autocomplete_fields = ("patient", "doctor", "booked_by")
    date_hierarchy = "scheduled_start"
    readonly_fields = (
        "arrived_at", "entered_cabin_at", "consulted_at", "completed_at",
        "created_at", "updated_at",
    )
    inlines = [VisitStatusEventInline]

    fieldsets = (
        (None, {"fields": ("patient", "doctor", "status")}),
        ("Schedule", {"fields": ("scheduled_start", "scheduled_end", "reason", "is_follow_up", "booked_by")}),
        ("Progress through the clinic", {
            "fields": ("arrived_at", "entered_cabin_at", "consulted_at", "completed_at"),
        }),
        ("Record", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )


@admin.register(VisitStatusEvent)
class VisitStatusEventAdmin(admin.ModelAdmin):
    """Read-only: this is a historical record, not editable data."""

    list_display = ("visit", "from_status", "to_status", "changed_by", "created_at")
    list_filter = ("to_status", "created_at")
    search_fields = ("visit__patient__patient_id",)
    readonly_fields = ("visit", "from_status", "to_status", "changed_by", "note", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ── Doctor availability ───────────────────────────────────────────────────────
#
# Reception has its own screen for both of these, which is where the day-to-day
# work happens. They are registered here as well because the admin is where the
# clinic's own administrator sets things up in bulk — a year of public
# holidays, or a doctor's whole first month at go-live — and because being able
# to see the rows directly is what makes "why was that slot not offered?"
# answerable.


@admin.register(ClinicHoliday)
class ClinicHolidayAdmin(admin.ModelAdmin):
    list_display = ("date", "name", "note")
    list_filter = ("date",)
    search_fields = ("name", "note")
    date_hierarchy = "date"
    ordering = ("-date",)


@admin.register(DoctorSchedule)
class DoctorScheduleAdmin(admin.ModelAdmin):
    """One doctor's hours on one date — the only shape this table has now."""

    list_display = ("doctor", "date", "start_time", "end_time",
                    "effective_slot_minutes", "cabin", "note")
    list_filter = ("date", "doctor")
    search_fields = ("doctor__first_name", "doctor__last_name", "note")
    autocomplete_fields = ("doctor", "created_by")
    date_hierarchy = "date"
    readonly_fields = ("created_at",)
    ordering = ("-date",)

    @admin.display(description="Slot", ordering="slot_minutes")
    def effective_slot_minutes(self, obj):
        """Show what will actually be used, not an empty cell."""
        from django.conf import settings

        if obj.slot_minutes:
            return f"{obj.slot_minutes} min"
        return f"{settings.CLINIC.SLOT_MINUTES} min (clinic default)"
