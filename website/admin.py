from django.contrib import admin

from .models import CallbackRequest


@admin.register(CallbackRequest)
class CallbackRequestAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "preferred_doctor", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("name", "phone", "concern")
    date_hierarchy = "created_at"

    #: Everything the public typed is a record of what they said, not a field to
    #: tidy. Only the handling state is editable, and that is done from the
    #: reception screen where the person doing the ringing actually is.
    readonly_fields = ("name", "phone", "preferred_doctor", "concern",
                       "created_at", "handled_at", "handled_by")

    def has_add_permission(self, request):
        return False
