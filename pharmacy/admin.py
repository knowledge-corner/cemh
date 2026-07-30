from django.contrib import admin

from .models import Prescription, PrescriptionItem


class PrescriptionItemInline(admin.TabularInline):
    model = PrescriptionItem
    extra = 1


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):
    list_display = ("patient", "doctor", "created_at", "generated_at", "follow_up_date")
    list_filter = ("doctor", "created_at")
    search_fields = ("patient__patient_id", "patient__first_name", "patient__last_name")
    autocomplete_fields = ("visit", "patient", "doctor")
    readonly_fields = ("created_at", "updated_at")
    inlines = [PrescriptionItemInline]
    date_hierarchy = "created_at"
