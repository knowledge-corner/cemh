from django.urls import path

from . import views_placeholder

urlpatterns = [
    path("reception/", views_placeholder.reception_home, name="reception_home"),
    path("my/", views_placeholder.patient_home, name="patient_home"),
]
