"""Login, logout and the role dispatcher."""

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse

from .models import Role


class ClinicLoginView(auth_views.LoginView):
    """
    One login page for every role.

    Where a user goes afterwards is decided by :func:`dashboard_redirect`, so
    the login page itself never needs to know about roles.
    """

    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse("dashboard")


@login_required
def dashboard_redirect(request):
    """
    Send each user to the dashboard for their role.

    Every post-login path funnels through here, so there is exactly one place
    that maps a role to a landing page.
    """
    user = request.user

    if user.role == Role.DOCTOR:
        return redirect("doctor_home")
    if user.role == Role.RECEPTIONIST:
        return redirect("reception_home")
    if user.role == Role.PATIENT:
        # There is no patient portal — patients book by telephone or WhatsApp.
        # Such an account has nothing to sign in for, so send it to the public
        # page rather than leaving it on a dead end.
        return redirect("website_home")

    # Administrators, and superusers with no clinical role, get the admin site.
    return redirect(f"/{settings.ADMIN_URL}")
