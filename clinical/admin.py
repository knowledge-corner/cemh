from django import forms
from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse

from . import lab_reference_csv
from .models import (
    ClinicalNote, Diagnosis, FormDefinition, ICD10Code, Investigation, LabReferenceRange,
    LabTest, LabUnitConversion, ReferenceLetter,
)


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
        ("Consultation", {"fields": ("clinical_notes", "prescription_note")}),
        ("Vitals", {"fields": ("systolic_bp", "diastolic_bp", "pulse", "temperature_c")}),
        ("Earlier format", {
            "classes": ("collapse",),
            "fields": ("complaints", "examination", "assessment", "plan"),
        }),
        ("Clinic-specific fields", {"classes": ("collapse",), "fields": ("extra", "form_version")}),
        ("Record", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )


@admin.register(Investigation)
class InvestigationAdmin(admin.ModelAdmin):
    list_display = ("patient", "test_name", "display_value", "performed_on", "is_abnormal")
    list_filter = ("category", "is_abnormal", "performed_on")
    search_fields = ("patient__patient_id", "patient__first_name", "test_name", "lab_name")
    autocomplete_fields = ("patient", "visit", "recorded_by", "lab_test")
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


@admin.register(ReferenceLetter)
class ReferenceLetterAdmin(admin.ModelAdmin):
    list_display = ("patient", "to", "doctor", "created_at", "printed_at")
    list_filter = ("doctor", "created_at")
    search_fields = ("patient__patient_id", "patient__first_name", "to")
    autocomplete_fields = ("patient", "doctor")
    readonly_fields = ("printed_at", "created_at", "updated_at")


@admin.register(FormDefinition)
class FormDefinitionAdmin(admin.ModelAdmin):
    list_display = ("target", "version", "is_active", "created_at")
    list_filter = ("target", "is_active")
    readonly_fields = ("created_at",)


@admin.register(ICD10Code)
class ICD10CodeAdmin(admin.ModelAdmin):
    """Loaded once from the WHO classification — browsable, not editable."""

    list_display = ("code", "description")
    search_fields = ("code", "description")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    """
    The master list of orderable tests — loaded once from the seed
    catalogue. Editable, unlike ICD10Code: this is a locally curated
    working list, not a frozen external standard, so a name or category
    can be corrected here directly.
    """

    list_display = ("code", "name", "category", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("code", "name")


class ReferenceUploadForm(forms.Form):
    file = forms.FileField(label="Filled-in template (CSV)")


@admin.register(LabReferenceRange)
class LabReferenceRangeAdmin(admin.ModelAdmin):
    list_display = (
        "lab_test", "sex", "age_min", "age_max", "low", "high", "unit", "status",
    )
    list_filter = ("status", "sex")
    search_fields = ("lab_test__name", "lab_test__code", "source")
    autocomplete_fields = ("lab_test",)

    def get_urls(self):
        extra = [
            path(
                "download-template/",
                self.admin_site.admin_view(self.download_template),
                name="clinical_labreferencerange_download_template",
            ),
            path(
                "upload/",
                self.admin_site.admin_view(self.upload),
                name="clinical_labreferencerange_upload",
            ),
        ]
        return extra + super().get_urls()

    def download_template(self, request):
        category = request.GET.get("category") or None
        response = HttpResponse(
            lab_reference_csv.template_csv(category=category), content_type="text/csv",
        )
        response["Content-Disposition"] = 'attachment; filename="lab_reference_ranges_template.csv"'
        return response

    def upload(self, request):
        result = None
        if request.method == "POST":
            form = ReferenceUploadForm(request.POST, request.FILES)
            if form.is_valid():
                result = lab_reference_csv.parse(request.FILES["file"])
                if result.can_import:
                    created, updated = lab_reference_csv.commit(result)
                    messages.success(
                        request,
                        f"{created} reference range{'s' if created != 1 else ''} added, "
                        f"{updated} updated.",
                    )
                    if not result.problems:
                        return redirect("admin:clinical_labreferencerange_changelist")
                elif result.fatal:
                    messages.error(request, result.fatal)
        else:
            form = ReferenceUploadForm()

        return render(request, "admin/clinical/labreferencerange/upload.html", {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "form": form,
            "result": result,
            "template_url": reverse("admin:clinical_labreferencerange_download_template"),
        })


@admin.register(LabUnitConversion)
class LabUnitConversionAdmin(admin.ModelAdmin):
    list_display = ("lab_test", "from_unit", "to_unit", "multiplier", "offset")
    search_fields = ("lab_test__name", "lab_test__code", "from_unit", "to_unit")
    autocomplete_fields = ("lab_test",)
