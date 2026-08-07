from django.contrib import admin

from .models import Measurement


@admin.register(Measurement)
class MeasurementAdmin(admin.ModelAdmin):
    list_display = ("patient", "measured_on", "height_cm", "weight_kg", "bmi_display", "puberty_stage")
    list_filter = ("measured_on", "puberty_stage")
    search_fields = ("patient__patient_id", "patient__first_name", "patient__last_name")
    autocomplete_fields = ("patient", "visit", "recorded_by")
    date_hierarchy = "measured_on"
    readonly_fields = ("created_at", "bmi_display", "mid_parental_height_cm")

    fieldsets = (
        (None, {"fields": ("patient", "visit", "measured_on", "recorded_by")}),
        ("Measurements", {
            "fields": ("height_cm", "weight_kg", "bmi_display", "head_circumference_cm", "waist_cm"),
        }),
        ("Puberty & parental heights", {
            "fields": (
                "puberty_stage", "bone_age_years",
                "mother_height_cm", "father_height_cm", "mid_parental_height_cm",
            ),
        }),
        ("Other", {"fields": ("notes", "created_at")}),
    )

    @admin.display(description="BMI")
    def bmi_display(self, obj):
        return obj.bmi or "—"
