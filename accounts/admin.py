"""
Admin screens for user management.

Until dedicated staff-management pages exist, the clinic creates doctors,
receptionists and patient logins here.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import DoctorProfile, Role, User


class ClinicUserCreationForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email", "phone", "role")


class ClinicUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


class DoctorProfileInline(admin.StackedInline):
    model = DoctorProfile
    can_delete = False
    extra = 0
    verbose_name_plural = "Doctor details"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    add_form = ClinicUserCreationForm
    form = ClinicUserChangeForm
    model = User

    list_display = ("username", "display_name", "email", "phone", "role", "is_active")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("username", "first_name", "last_name", "email", "phone")
    ordering = ("first_name", "last_name")

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal details", {"fields": ("first_name", "last_name", "email", "phone")}),
        ("Role & access", {"fields": ("role", "is_active", "is_staff", "is_superuser")}),
        ("Permissions", {"classes": ("collapse",), "fields": ("groups", "user_permissions")}),
        ("Important dates", {"classes": ("collapse",), "fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "email",
                    "phone",
                    "role",
                    "first_name",
                    "last_name",
                    "password1",
                    "password2",
                ),
            },
        ),
    )

    @admin.display(description="Name", ordering="first_name")
    def display_name(self, obj):
        return obj.display_name

    def get_inlines(self, request, obj=None):
        # Only doctors have a doctor profile; showing the inline for a
        # receptionist would just invite bad data.
        if obj and obj.role == Role.DOCTOR:
            return [DoctorProfileInline]
        return []


@admin.register(DoctorProfile)
class DoctorProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "qualification", "speciality", "registration_number")
    search_fields = ("user__first_name", "user__last_name", "registration_number")
    autocomplete_fields = ("user",)
