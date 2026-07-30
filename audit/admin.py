from django.contrib import admin

from .models import AccessLog


@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    """
    Strictly read-only.

    An audit trail that can be edited from the admin is not an audit trail.
    """

    list_display = ("created_at", "username", "user_role", "action", "object_type", "patient_id_ref")
    list_filter = ("action", "user_role", "created_at")
    search_fields = ("username", "patient_id_ref", "object_type", "description")
    date_hierarchy = "created_at"
    readonly_fields = (
        "user", "username", "user_role", "action", "object_type", "object_id",
        "patient_id_ref", "description", "ip_address", "path", "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
