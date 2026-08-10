"""
KAN-24 — clinic holidays from a CSV, and one at a time.

The dangerous part of this story is not the upload, it is the date column.
``03/04/2026`` is the third of April in Mumbai and the fourth of March in a
US-locale export, and nothing in the file says which. Guessing shuts the clinic
on the wrong day and nobody finds out until patients arrive at a locked door, so
the ambiguous formats are refused rather than interpreted.

The other thing worth pinning down is that this importer takes the good rows and
leaves the bad ones (FR-4) — deliberately the opposite of the patient importer,
which is all-or-nothing. A test that passes for both would be testing neither.
"""

import io
from datetime import date, time, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments import holidays
from appointments.models import ClinicHoliday, DoctorSchedule

from .factories import make_doctor, make_receptionist


def csv_file(body, name="holidays.csv"):
    upload = io.BytesIO(body.encode("utf-8") if isinstance(body, str) else body)
    upload.name = name
    return upload


HEADER = "holiday_date,holiday_name,notes\n"


class ImportTestCase(TestCase):
    def setUp(self):
        self.client.force_login(make_receptionist())

    def check(self, body, name="holidays.csv"):
        """First pass — read the file and report, writing nothing."""
        return self.client.post(
            reverse("reception_import_holidays"), {"file": csv_file(body, name)}
        )

    def confirm(self, body, name="holidays.csv"):
        """Second pass — actually write."""
        return self.client.post(
            reverse("reception_import_holidays"),
            {"file": csv_file(body, name), "confirm": "1"},
        )


# ── The template ─────────────────────────────────────────────────────────────

class TestTheTemplate(ImportTestCase):
    def test_it_can_be_downloaded(self):
        response = self.client.get(reverse("reception_holiday_template"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])

    def test_it_carries_the_documented_headings(self):
        body = self.client.get(reverse("reception_holiday_template")).content.decode()
        self.assertTrue(body.startswith("holiday_date,holiday_name,notes"))

    def test_it_states_the_date_format_in_the_file_itself(self):
        # AC-1. Nobody reads the screen they downloaded from a week later.
        body = self.client.get(reverse("reception_holiday_template")).content.decode()
        self.assertIn("YYYY-MM-DD", body)

    def test_it_carries_examples(self):
        body = self.client.get(reverse("reception_holiday_template")).content.decode()
        self.assertIn("Republic Day", body)
        self.assertIn("Diwali", body)

    def test_the_template_imports_cleanly_as_it_stands(self):
        # The template shipping its own instructions is only safe if the help
        # row is recognised and skipped rather than imported as a holiday.
        body = self.client.get(reverse("reception_holiday_template")).content.decode()
        self.confirm(body)
        self.assertEqual(ClinicHoliday.objects.count(), 2)
        self.assertFalse(ClinicHoliday.objects.filter(name__icontains="Required").exists())


# ── Importing (FR-2, FR-3, FR-4) ─────────────────────────────────────────────

class TestImporting(ImportTestCase):
    def test_a_clean_file_imports(self):
        self.confirm(HEADER + "2026-01-26,Republic Day,\n2026-11-08,Diwali,\n")
        self.assertEqual(ClinicHoliday.objects.count(), 2)

    def test_the_first_pass_writes_nothing(self):
        self.check(HEADER + "2026-01-26,Republic Day,\n")
        self.assertEqual(ClinicHoliday.objects.count(), 0)

    def test_the_notes_column_is_kept(self):
        self.confirm(HEADER + "2026-11-08,Diwali,Closed all day\n")
        self.assertEqual(ClinicHoliday.objects.get().note, "Closed all day")

    def test_the_notes_column_is_optional(self):
        self.confirm(HEADER + "2026-11-08,Diwali,\n")
        self.assertTrue(ClinicHoliday.objects.filter(name="Diwali").exists())

    def test_the_good_rows_import_when_others_fail(self):
        # FR-4 / AC-3, and the opposite of the patient importer next door.
        self.confirm(
            HEADER
            + "2026-01-26,Republic Day,\n"
            + "not-a-date,Broken,\n"
            + "2026-11-08,Diwali,\n"
        )
        self.assertEqual(
            set(ClinicHoliday.objects.values_list("name", flat=True)),
            {"Republic Day", "Diwali"},
        )

    def test_the_bad_row_is_reported_with_its_line_number(self):
        response = self.check(
            HEADER + "2026-01-26,Republic Day,\nnot-a-date,Broken,\n"
        )
        problems = response.context["result"].problems
        self.assertEqual([p.line for p in problems], [3])

    def test_a_row_with_no_name_is_rejected(self):
        # An unnamed closure on the calendar tells nobody why the clinic is shut.
        self.confirm(HEADER + "2026-11-08,,\n")
        self.assertFalse(ClinicHoliday.objects.exists())

    def test_a_blank_line_is_ignored_rather_than_reported(self):
        response = self.check(HEADER + "2026-11-08,Diwali,\n\n\n")
        self.assertEqual(response.context["result"].problems, [])

    def test_one_row_is_enough(self):
        self.confirm(HEADER + "2026-11-08,Diwali,\n")
        self.assertEqual(ClinicHoliday.objects.count(), 1)

    def test_a_leap_day_is_accepted(self):
        self.confirm(HEADER + "2028-02-29,Leap day,\n")
        self.assertTrue(ClinicHoliday.objects.filter(date=date(2028, 2, 29)).exists())

    def test_a_file_spanning_a_year_boundary_lands_on_the_right_dates(self):
        self.confirm(HEADER + "2026-12-25,Christmas,\n2027-01-01,New Year,\n")
        self.assertEqual(
            sorted(ClinicHoliday.objects.values_list("date", flat=True)),
            [date(2026, 12, 25), date(2027, 1, 1)],
        )

    def test_a_past_date_is_allowed(self):
        # A historical record is legitimate, and refusing it would make last
        # year's list impossible to load.
        self.confirm(HEADER + "2020-11-14,Diwali 2020,\n")
        self.assertTrue(ClinicHoliday.objects.filter(date=date(2020, 11, 14)).exists())


# ── The date format, which is the whole risk ─────────────────────────────────

class TestTheDateFormat(ImportTestCase):
    def test_the_documented_format_works(self):
        self.confirm(HEADER + "2026-11-08,Diwali,\n")
        self.assertTrue(ClinicHoliday.objects.filter(date=date(2026, 11, 8)).exists())

    def test_an_ambiguous_slash_date_is_refused_not_guessed(self):
        # 03/04/2026 is two different days depending on who exported the file.
        # Importing either reading would shut the clinic on a day it is open.
        self.confirm(HEADER + "03/04/2026,Ambiguous,\n")
        self.assertFalse(ClinicHoliday.objects.exists())

    def test_the_refusal_says_what_to_use_instead(self):
        response = self.check(HEADER + "03/04/2026,Ambiguous,\n")
        message = response.context["result"].problems[0].message
        self.assertIn("YYYY-MM-DD", message)

    def test_an_unambiguous_slash_date_is_refused_too(self):
        # 25/12/2026 can only be one day, but accepting it and refusing
        # 03/04/2026 would make the rule "sometimes" — which is worse than a
        # rule that is always the same.
        self.confirm(HEADER + "25/12/2026,Christmas,\n")
        self.assertFalse(ClinicHoliday.objects.exists())

    def test_a_date_that_does_not_exist_is_refused(self):
        self.confirm(HEADER + "2026-02-30,Nonsense,\n")
        self.assertFalse(ClinicHoliday.objects.exists())


# ── Duplicates (FR-8, AC-5) ──────────────────────────────────────────────────

class TestDuplicates(ImportTestCase):
    def test_a_date_already_recorded_is_not_added_twice(self):
        ClinicHoliday.objects.create(date=date(2026, 11, 8), name="Diwali")
        self.confirm(HEADER + "2026-11-08,Diwali,\n")
        self.assertEqual(ClinicHoliday.objects.count(), 1)

    def test_the_existing_name_is_kept(self):
        ClinicHoliday.objects.create(date=date(2026, 11, 8), name="Diwali")
        self.confirm(HEADER + "2026-11-08,Something else,\n")
        self.assertEqual(ClinicHoliday.objects.get().name, "Diwali")

    def test_the_same_date_twice_in_one_file_takes_the_first(self):
        self.confirm(HEADER + "2026-11-08,Diwali,\n2026-11-08,Diwali again,\n")
        self.assertEqual(ClinicHoliday.objects.count(), 1)
        self.assertEqual(ClinicHoliday.objects.get().name, "Diwali")

    def test_the_repeat_is_reported_rather_than_silently_dropped(self):
        response = self.check(HEADER + "2026-11-08,Diwali,\n2026-11-08,Again,\n")
        self.assertEqual(len(response.context["result"].duplicates), 1)

    def test_re_uploading_a_corrected_file_is_safe(self):
        # The normal way this gets used: import, spot a bad row, fix it, upload
        # the whole file again.
        body = HEADER + "2026-01-26,Republic Day,\nbad,Broken,\n"
        self.confirm(body)
        self.confirm(HEADER + "2026-01-26,Republic Day,\n2026-11-08,Diwali,\n")

        self.assertEqual(ClinicHoliday.objects.count(), 2)


# ── Files that are not the template (AC-7, T-7) ──────────────────────────────

class TestBadFiles(ImportTestCase):
    def _fatal(self, body, name="holidays.csv"):
        return self.check(body, name).context["result"].fatal

    def test_wrong_headings_reject_the_whole_file(self):
        self.confirm("date,name\n2026-11-08,Diwali\n")
        self.assertFalse(ClinicHoliday.objects.exists())

    def test_the_rejection_names_the_expected_headings(self):
        message = self._fatal("date,name\n2026-11-08,Diwali\n")
        self.assertIn("holiday_date", message)

    def test_an_empty_file_is_rejected(self):
        self.assertTrue(self._fatal(""))

    def test_headings_with_no_rows_are_rejected(self):
        self.assertTrue(self._fatal(HEADER))

    def test_a_renamed_binary_file_is_rejected_clearly(self):
        response = self.client.post(
            reverse("reception_import_holidays"),
            {"file": csv_file(b"PK\x03\x04\x00\x00binary rubbish", "holidays.csv")},
        )
        self.assertIn("not a CSV file", response.context["result"].fatal)

    def test_an_excel_byte_order_mark_does_not_break_the_headings(self):
        # Excel writes a BOM, which turns the first heading into "﻿holiday_date"
        # and makes a perfectly correct file look as though it had no columns.
        self.confirm("﻿" + HEADER + "2026-11-08,Diwali,\n")
        self.assertTrue(ClinicHoliday.objects.filter(name="Diwali").exists())

    def test_an_accented_name_survives(self):
        self.confirm(HEADER + "2026-11-08,Gudi Padwa — clinic shut,\n")
        self.assertTrue(ClinicHoliday.objects.filter(name__contains="—").exists())

    def test_an_enormous_file_is_refused(self):
        body = HEADER + "".join(
            f"{date(2026, 1, 1) + timedelta(days=n):%Y-%m-%d},Day {n},\n"
            for n in range(holidays.MAX_ROWS + 5)
        )
        self.confirm(body)
        self.assertFalse(ClinicHoliday.objects.exists())

    def test_no_file_at_all_does_not_crash(self):
        response = self.client.post(reverse("reception_import_holidays"), {})
        self.assertEqual(response.status_code, 200)


# ── Access ───────────────────────────────────────────────────────────────────

class TestAccess(ImportTestCase):
    def test_a_doctor_cannot_import_holidays(self):
        self.client.force_login(make_doctor())
        response = self.confirm(HEADER + "2026-11-08,Diwali,\n")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ClinicHoliday.objects.exists())

    def test_a_doctor_cannot_download_the_template(self):
        self.client.force_login(make_doctor())
        response = self.client.get(reverse("reception_holiday_template"))
        self.assertEqual(response.status_code, 403)


# ── On the calendar (FR-6, and the accessibility requirement) ────────────────

class TestHolidaysOnTheCalendar(TestCase):
    def setUp(self):
        self.client.force_login(make_receptionist())
        self.doctor = make_doctor()
        self.day = timezone.localdate() + timedelta(days=3)
        self.holiday = ClinicHoliday.objects.create(date=self.day, name="Diwali")

    def _page(self, view):
        return self.client.get(
            reverse("reception_calendar"),
            {"view": view, "date": self.day.isoformat()},
        )

    def test_it_shows_in_the_month_view(self):
        self.assertContains(self._page("month"), "Diwali")

    def test_it_shows_in_the_day_view(self):
        self.assertContains(self._page("day"), "Diwali")

    def test_it_is_marked_by_a_word_and_not_only_by_colour(self):
        # KAN-24's accessibility requirement. Orange alone fails both contrast
        # and colour-blindness, and "the clinic is shut" is not a fact to
        # convey by hue.
        self.assertContains(self._page("month"), "Closed")

    def test_the_clinic_being_shut_empties_the_day(self):
        DoctorSchedule.objects.create(
            doctor=self.doctor, date=self.day,
            start_time=time(10), end_time=time(13),
        )
        response = self._page("day")
        charted = [
            entry
            for column in response.context["columns"]
            for entry in column["entries"]
        ]
        self.assertEqual(charted, [])

    def test_it_can_be_removed_from_the_day_view(self):
        self.client.post(
            reverse("reception_delete_calendar_entry", args=["holiday", self.holiday.pk])
        )
        self.assertFalse(ClinicHoliday.objects.filter(pk=self.holiday.pk).exists())
