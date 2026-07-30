from django.urls import path

from . import views_doctor

urlpatterns = [
    path("", views_doctor.doctor_home, name="doctor_home"),
    path("patient/<str:patient_id>/", views_doctor.patient_dashboard, name="doctor_patient_dashboard"),
    path("patient/<str:patient_id>/tab/<str:tab>/", views_doctor.patient_tab, name="doctor_patient_tab"),
]
