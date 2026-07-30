"""
Role-based access control.

Access is enforced *here*, in the view layer. Hiding a link in a template is
presentation, not security — anyone can type the URL. Every view that reaches a
patient record goes through one of these.
"""

from functools import wraps

from django.core.exceptions import PermissionDenied
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import resolve_url
from django.conf import settings

from .models import Role


def _user_has_role(user, roles):
    if not user.is_authenticated:
        return False
    # A superuser is the clinic's own administrator and may reach any screen.
    if user.is_superuser:
        return True
    return user.role in roles


def role_required(*roles):
    """
    Restrict a function view to the given roles.

    Anonymous users are sent to the login page; a signed-in user with the wrong
    role gets 403 rather than a redirect, so a receptionist who lands on a
    doctor URL is told plainly instead of being bounced around.
    """

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(
                    request.get_full_path(), resolve_url(settings.LOGIN_URL)
                )
            if not _user_has_role(request.user, roles):
                raise PermissionDenied(
                    "Your account does not have access to this area."
                )
            return view_func(request, *args, **kwargs)

        return _wrapped

    return decorator


class RoleRequiredMixin:
    """Class-based-view equivalent of :func:`role_required`."""

    allowed_roles: tuple = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(
                request.get_full_path(), resolve_url(settings.LOGIN_URL)
            )
        if not _user_has_role(request.user, self.allowed_roles):
            raise PermissionDenied("Your account does not have access to this area.")
        return super().dispatch(request, *args, **kwargs)


class DoctorRequiredMixin(RoleRequiredMixin):
    allowed_roles = (Role.DOCTOR,)


class ReceptionistRequiredMixin(RoleRequiredMixin):
    allowed_roles = (Role.RECEPTIONIST,)


class ClinicStaffRequiredMixin(RoleRequiredMixin):
    """Doctor or receptionist — staff who legitimately see patient records."""

    allowed_roles = (Role.DOCTOR, Role.RECEPTIONIST)
