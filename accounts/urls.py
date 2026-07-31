from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    # "/" belongs to the public website; staff enter through /login/.
    path("login/", views.ClinicLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("dashboard/", views.dashboard_redirect, name="dashboard"),
]
