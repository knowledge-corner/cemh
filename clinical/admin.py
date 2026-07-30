from django.contrib import admin

from .models import ClinicalNote, Diagnosis, FormDefinition, Investigation


@admin.register(ClinicalNote)
class ClinicalNoteAdmin(admin.ModelAdmin):
    list_display = ("patient", "author", "created_at")
    list_filter = ("author", "created_at")
    search_fields = ("patient__patient_id", "patient__first_name", "complaints", "assessment")
    autocomplete_fields = ("visit", "patient", "author")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"

    fieldsets = (
        (None, {"fields": ("visit", "patient", "author")}),
        ("Consultation", {"fields": ("complaints", "examination", "assessment", "plan")}),
        ("Vitals", {"fields": ("systolic_bp", "diastolic_bp", "pulse", "temperature_c")}),
        ("Clinic-specific fields", {"classes": ("collapse",), "fields": ("extra", "form_version")}),
        ("Record", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )


@admin.register(Investigation)
class InvestigationAdmin(admin.ModelAdmin):
    list_display = ("patient", "test_name", "display_value", "performed_on", "is_abnormal")
    list_filter = ("category", "is_abnormal", "performed_on")
    search_fields = ("patient__patient_id", "patient__first_name", "test_name", "lab_name")
    autocomplete_fields = ("patient", "visit", "recorded_by")
    date_hierarchy = "performed_on"

    @admin.display(description="Result")
    def display_value(self, obj):
        return obj.display_value


@admin.register(Diagnosis)
class DiagnosisAdmin(admin.ModelAdmin):
    list_display = ("patient", "description", "status", "diagnosed_on")
    list_filter = ("status", "diagnosed_on")
    search_fields = ("patient__patient_id", "description", "icd10_code")
    autocomplete_fields = ("patient", "visit")


@admin.register(FormDefinition)
class FormDefinitionAdmin(admin.ModelAdmin):
    list_display = ("target", "version", "is_active", "created_at")
    list_filter = ("target", "is_active")
    readonly_fields = ("created_at",)
