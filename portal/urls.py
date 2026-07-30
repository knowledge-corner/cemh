from django.urls import path

from . import views_patient, views_reception

urlpatterns = [
    # ── Reception ────────────────────────────────────────────────────────
    path("reception/", views_reception.reception_home, name="reception_home"),
    path("reception/board/", views_reception.queue_board, name="reception_board"),
    path("reception/visit/<int:pk>/move/<str:to_status>/",
         views_reception.move_visit, name="reception_move_visit"),

    path("reception/bookings/", views_reception.bookings, name="reception_bookings"),
    path("reception/bookings/new/", views_reception.new_booking, name="reception_new_booking"),
    path("reception/bookings/slots/", views_reception.slot_options, name="reception_slots"),
    path("reception/bookings/search/", views_reception.patient_lookup, name="reception_patient_lookup"),
    path("reception/bookings/register/", views_reception.register_patient, name="reception_register_patient"),

    path("reception/billing/<int:pk>/", views_reception.billing, name="reception_billing"),
    path("reception/billing/<int:pk>/complete/",
         views_reception.complete_visit, name="reception_complete_visit"),

    path("print/prescription/<int:pk>/", views_reception.print_prescription, name="print_prescription"),
    path("print/receipt/<int:pk>/", views_reception.print_receipt, name="print_receipt"),

    # ── Patient ──────────────────────────────────────────────────────────
    path("my/", views_patient.patient_home, name="patient_home"),
    path("my/book/", views_patient.book, name="patient_book"),
    path("my/book/slots/", views_patient.patient_slot_options, name="patient_slots"),
    path("my/visit/<int:pk>/cancel/", views_patient.cancel_visit, name="patient_cancel_visit"),
]
