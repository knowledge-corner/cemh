from django.urls import path

from . import views_doctor, views_edit

urlpatterns = [
    path("", views_doctor.doctor_home, name="doctor_home"),
    path("queue/", views_doctor.doctor_queue, name="doctor_queue"),
    path("send/<int:pk>/", views_doctor.send_for_patient, name="doctor_send_for_patient"),
    path("search/", views_doctor.doctor_patient_search, name="doctor_patient_search"),
    path(
        "patient/<str:patient_id>/",
        views_doctor.patient_dashboard,
        name="doctor_patient_dashboard",
    ),
    path(
        "patient/<str:patient_id>/tab/<str:tab>/",
        views_doctor.patient_tab,
        name="doctor_patient_tab",
    ),
    # Create a record of the given kind …
    path(
        "patient/<str:patient_id>/add/<str:kind>/",
        views_edit.edit_record,
        name="doctor_add_record",
    ),
    # … or edit an existing one.
    path(
        "patient/<str:patient_id>/edit/<str:kind>/<int:pk>/",
        views_edit.edit_record,
        name="doctor_edit_record",
    ),
    path(
        "patient/<str:patient_id>/prescription/<int:pk>/generate/",
        views_edit.generate_prescription,
        name="doctor_generate_prescription",
    ),
    path(
        "patient/<str:patient_id>/complete/",
        views_edit.complete_consultation,
        name="doctor_complete_consultation",
    ),
]
