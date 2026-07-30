from django.contrib import admin

from .models import Patient, PatientHistory


class PatientHistoryInline(admin.StackedInline):
    model = PatientHistory
    can_delete = False
    extra = 0
    verbose_name_plural = "Background history"


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("patient_id", "full_name", "age_display", "sex", "phone", "is_active")
    list_filter = ("sex", "is_active", "blood_group", "registered_on")
    search_fields = ("patient_id", "first_name", "last_name", "phone", "guardian_phone", "email")
    readonly_fields = ("patient_id", "created_at", "updated_at", "age_display")
    autocomplete_fields = ("user",)
    date_hierarchy = "registered_on"
    inlines = [PatientHistoryInline]

    fieldsets = (
        (None, {"fields": ("patient_id", "is_active", "registered_on")}),
        ("Identity", {
            "fields": ("first_name", "last_name", "date_of_birth", "age_display", "sex", "blood_group"),
        }),
        ("Contact", {
            "fields": ("phone", "alternate_phone", "email", "address", "city", "pincode"),
        }),
        ("Guardian (paediatric)", {
            "fields": ("guardian_name", "guardian_relation", "guardian_phone"),
        }),
        ("Other", {"fields": ("referred_by", "user", "created_at", "updated_at")}),
    )

    @admin.display(description="Name", ordering="first_name")
    def full_name(self, obj):
        return obj.full_name

    @admin.display(description="Age")
    def age_display(self, obj):
        return obj.age_display


@admin.register(PatientHistory)
class PatientHistoryAdmin(admin.ModelAdmin):
    list_display = ("patient", "updated_at")
    search_fields = ("patient__patient_id", "patient__first_name", "patient__last_name")
    autocomplete_fields = ("patient",)
