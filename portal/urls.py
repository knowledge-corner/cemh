from django.urls import path

from . import views_doctors, views_reception

urlpatterns = [
    # ── Reception ────────────────────────────────────────────────────────
    path("reception/", views_reception.reception_home, name="reception_home"),
    path("reception/board/", views_reception.queue_board, name="reception_board"),
    path("reception/visit/<int:pk>/move/<str:to_status>/",
         views_reception.move_visit, name="reception_move_visit"),

    path("reception/close-day/", views_reception.close_day, name="reception_close_day"),
    path("reception/visit/<int:pk>/back/", views_reception.move_visit_back,
         name="reception_move_visit_back"),
    path("reception/visit/<int:pk>/settled/", views_reception.settled_visit,
         name="reception_settled_visit"),

    path("reception/bookings/", views_reception.bookings, name="reception_bookings"),
    path("reception/bookings/export/", views_reception.export_bookings,
         name="reception_export_bookings"),
    path("reception/bookings/new/", views_reception.new_booking, name="reception_new_booking"),
    path("reception/bookings/<int:pk>/edit/", views_reception.edit_booking,
         name="reception_edit_booking"),
    path("reception/bookings/slots/", views_reception.slot_options, name="reception_slots"),
    path("reception/bookings/search/", views_reception.patient_lookup, name="reception_patient_lookup"),
    path("reception/bookings/register/", views_reception.register_patient, name="reception_register_patient"),
    # Bringing an existing patient list in from a spreadsheet.
    path("reception/patients/import/", views_reception.import_patients,
         name="reception_import_patients"),
    path("reception/patients/template.csv", views_reception.patient_template,
         name="reception_patient_template"),

    # ── Doctors ──────────────────────────────────────────────────────────
    path("reception/doctors/", views_doctors.doctor_list, name="reception_doctors"),
    path("reception/doctors/add/", views_doctors.add_doctor, name="reception_add_doctor"),
    path("reception/doctors/<int:pk>/resend/", views_doctors.resend_invitation,
         name="reception_resend_invitation"),
    # Followed by the doctor from their email, so no sign-in required.
    path("activate/<str:token>/", views_doctors.activate_doctor, name="doctor_activate"),

    # ── Doctor availability ──────────────────────────────────────────────
    path("reception/availability/", views_reception.availability,
         name="reception_availability"),
    path("reception/availability/add/<str:kind>/", views_reception.add_availability,
         name="reception_add_availability"),
    path("reception/availability/<str:kind>/<int:pk>/remove/",
         views_reception.remove_availability, name="reception_remove_availability"),

    path("reception/billing/<int:pk>/", views_reception.billing, name="reception_billing"),
    # Taking the fee in a pop-up on the board, rather than on the billing page.
    path("reception/billing/<int:pk>/receipt/", views_reception.generate_receipt,
         name="reception_generate_receipt"),
    path("reception/billing/<int:pk>/complete/",
         views_reception.complete_visit, name="reception_complete_visit"),

    path("print/prescription/<int:pk>/", views_reception.print_prescription, name="print_prescription"),
    path("print/receipt/<int:pk>/", views_reception.print_receipt, name="print_receipt"),

    # There is deliberately no patient portal. Patients book by telephone or
    # WhatsApp from the public page; nothing about their record is online.
]
