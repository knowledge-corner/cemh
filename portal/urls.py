from django.urls import path

from . import views_calendar, views_doctors, views_reception

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

    # KAN-50 removed the separate Doctor availability screen. The calendar is
    # now the one place a doctor's schedule is viewed and edited, so a second
    # screen writing the same tables was two answers to one question.

    # ── The availability calendar (KAN-22) ───────────────────────────────
    # Doctors reach this too, scoped to themselves inside the view.
    path("calendar/", views_calendar.calendar_view, name="reception_calendar"),
    path("calendar/cabins/add/", views_calendar.add_cabin, name="reception_add_cabin"),
    path("calendar/cabins/<int:pk>/retire/", views_calendar.retire_cabin,
         name="reception_retire_cabin"),
    path("calendar/event/add/", views_calendar.add_calendar_event,
         name="reception_add_calendar_event"),
    path("calendar/<str:kind>/<int:pk>/delete/", views_calendar.delete_calendar_entry,
         name="reception_delete_calendar_entry"),

    # ── Clinic holidays from a spreadsheet (KAN-24) ──────────────────────
    path("calendar/holidays/import/", views_calendar.import_holidays,
         name="reception_import_holidays"),
    path("calendar/holidays/template.csv", views_calendar.holiday_template,
         name="reception_holiday_template"),
    path("calendar/holidays/<int:pk>/edit/", views_calendar.edit_holiday,
         name="reception_edit_holiday"),

    # ── Doctor rotas from a spreadsheet (KAN-22) ─────────────────────────
    path("calendar/schedules/import/", views_calendar.import_schedules,
         name="reception_import_schedules"),
    path("calendar/schedules/template.csv", views_calendar.schedule_template,
         name="reception_schedule_template"),

    # ── Callback requests from the public website ────────────────────────
    path("reception/callbacks/", views_calendar.callbacks, name="reception_callbacks"),
    path("reception/callbacks/<int:pk>/close/", views_calendar.close_callback,
         name="reception_close_callback"),

    # KAN-36 removed the standalone Full Bill page. Money is taken in the
    # Generate receipt pop-up on the board and nowhere else — two screens
    # writing payments against one charge is two places for a part payment to
    # be recorded twice.
    path("reception/billing/<int:pk>/receipt/", views_reception.generate_receipt,
         name="reception_generate_receipt"),
    path("reception/billing/<int:pk>/complete/",
         views_reception.complete_visit, name="reception_complete_visit"),

    path("print/prescription/<int:pk>/", views_reception.print_prescription, name="print_prescription"),
    path("print/receipt/<int:pk>/", views_reception.print_receipt, name="print_receipt"),

    # There is deliberately no patient portal. Patients book by telephone or
    # WhatsApp from the public page; nothing about their record is online.
]
