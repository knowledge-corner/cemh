from django.contrib import admin

from .models import Visit, VisitStatusEvent


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
