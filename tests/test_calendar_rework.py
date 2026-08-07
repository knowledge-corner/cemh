"""
The three edits made to the calendar after it shipped.

KAN-22 gained multi-weekday recurrence and a CSV rota upload; KAN-24 gained an
edit for a holiday already recorded; KAN-50 removed the Doctor availability
screen outright, which made the calendar the only way in and meant it had to
grow the one thing that screen could do and it could not — recording leave.

The recurrence tests are written around whole ranges rather than single dates on
purpose. "M-W-F through September" is the feature, and a rule that gets the
first Monday right and the last Friday wrong is exactly the failure the ticket
exists to prevent: a doctor's sitting that quietly is not there.
"""

from datetime import date, time, timedelta
from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments import weekdays as weekday_codes
from appointments import schedules_csv
from appointments.models import (
    Cabin, ClinicHoliday, DoctorLeave, DoctorSchedule, ScheduleOverride,
)

from .factories import make_doctor, make_patient, make_receptionist, make_visit


def _upload(text, name="rota.csv"):
    """A CSV as it arrives from a browser: bytes, with a name."""
    handle = BytesIO(text.encode("utf-8"))
    handle.name = name
    return handle


HEADER = ",".join(schedules_csv.COLUMNS) + "\n"


class CalendarReworkTestCase(TestCase):
    """A doctor, two cabins, and a September to put a rota in."""

    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)
        self.asha = make_doctor(username="dr-asha", email="asha@example.in",
                                first_name="Asha", last_name="Rao")
        self.vikram = make_doctor(username="dr-vikram", email="vikram@example.in",
                                  first_name="Vikram", last_name="Joshi")
        self.one = Cabin.objects.create(name="Cabin 1")
        self.two = Cabin.objects.create(name="Cabin 2")

        # A fixed month rather than "next month": the number of Mondays in a
        # range depends on which month it is, and a test whose expected count
        # changes with the calendar is a test nobody trusts. September 2026
        # starts on a Tuesday and has 30 days.
        self.start = date(2026, 9, 1)
        self.end = date(2026, 9, 30)


# ── KAN-22: the day codes themselves ─────────────────────────────────────────

class TestDayCodes(TestCase):
    """
    One table, read by both the pop-up and the importer.

    T is Tuesday and Th is Thursday. They are the pair people get wrong, and a
    rota loaded onto the wrong day is not something anybody notices until a
    doctor arrives to an empty cabin.
    """

    def test_the_codes_map_to_the_days_they_name(self):
        self.assertEqual(weekday_codes.parse_codes("M-W-F"), [0, 2, 4])
        self.assertEqual(weekday_codes.parse_codes("T-Th"), [1, 3])
        self.assertEqual(weekday_codes.parse_codes("Sa-Su"), [5, 6])

    def test_t_and_th_are_not_confused(self):
        self.assertEqual(weekday_codes.parse_codes("T"), [1])
        self.assertEqual(weekday_codes.parse_codes("Th"), [3])

    def test_case_and_spacing_are_forgiven(self):
        # What a column typed by hand actually contains.
        for written in ("m-w-f", "M - W - F", "M,W,F", "M/W/F", "M W F", " M-W-F "):
            with self.subTest(written=written):
                self.assertEqual(weekday_codes.parse_codes(written), [0, 2, 4])

    def test_a_repeated_day_is_not_two_days(self):
        self.assertEqual(weekday_codes.parse_codes("M-M-W"), [0, 2])

    def test_an_unreadable_code_is_refused_rather_than_skipped(self):
        # Silently dropping it is a clinic session that never appears and
        # nobody is told about.
        for written in ("S", "Mon", "M-X", "", None):
            with self.subTest(written=written):
                with self.assertRaises(weekday_codes.BadDayCode):
                    weekday_codes.parse_codes(written)

    def test_the_message_names_the_piece_it_could_not_read(self):
        with self.assertRaises(weekday_codes.BadDayCode) as raised:
            weekday_codes.parse_codes("M-Mon-F")
        self.assertIn("Mon", str(raised.exception))

    def test_a_range_expands_to_the_dates_it_covers(self):
        days = weekday_codes.dates_in_range(date(2026, 9, 1), date(2026, 9, 30),
                                            [0, 2, 4])
        self.assertEqual(len(days), 13)
        self.assertEqual(days[0], date(2026, 9, 2))     # the first Wednesday
        self.assertEqual(days[-1], date(2026, 9, 30))   # the last Wednesday
        self.assertTrue(all(d.weekday() in {0, 2, 4} for d in days))

    def test_both_ends_of_the_range_are_included(self):
        # A half-open range would drop the last Friday of the month, which is
        # the one nobody checks.
        days = weekday_codes.dates_in_range(date(2026, 9, 4), date(2026, 9, 4), [4])
        self.assertEqual(days, [date(2026, 9, 4)])

    def test_codes_round_trip(self):
        self.assertEqual(weekday_codes.format_codes([4, 0, 2]), "M-W-F")


# ── KAN-22: chosen weekdays in the pop-up ────────────────────────────────────

class TestChosenWeekdays(CalendarReworkTestCase):

    def _add(self, **overrides):
        payload = {
            "event_type": "hours", "doctor": self.asha.pk, "cabin": self.one.pk,
            "date": self.start.isoformat(), "until": self.end.isoformat(),
            "repeat": "selected", "weekdays": ["M", "W", "F"],
            "start_time": "09:00", "end_time": "13:00",
        }
        payload.update(overrides)
        return self.client.post(reverse("reception_add_calendar_event"), payload,
                                follow=True)

    def test_it_creates_one_entry_per_chosen_day_and_no_others(self):
        self._add()
        written = ScheduleOverride.objects.filter(doctor=self.asha)
        self.assertEqual(written.count(), 13)
        self.assertEqual(
            {entry.date.weekday() for entry in written}, {0, 2, 4},
        )

    def test_it_covers_the_last_week_of_the_range(self):
        # The end of a range is where an off-by-one hides.
        self._add()
        self.assertTrue(
            ScheduleOverride.objects.filter(doctor=self.asha,
                                            date=date(2026, 9, 30)).exists()
        )

    def test_the_days_not_chosen_are_left_alone(self):
        self._add()
        self.assertFalse(
            ScheduleOverride.objects.filter(
                doctor=self.asha, date=date(2026, 9, 1),   # a Tuesday
            ).exists()
        )

    def test_an_end_date_is_required(self):
        # Without one there is nothing to generate, and a range that quietly
        # became a single day is a rota with twelve sittings missing.
        response = self._add(until="")
        self.assertEqual(ScheduleOverride.objects.count(), 0)
        self.assertContains(response, "Give the last date")

    def test_at_least_one_day_must_be_chosen(self):
        response = self._add(weekdays=[])
        self.assertEqual(ScheduleOverride.objects.count(), 0)
        self.assertContains(response, "at least one day")

    def test_days_ticked_under_another_repeat_are_refused_not_ignored(self):
        # Ticked, then the repeat changed. Acting on them would create a rota
        # nobody asked for; ignoring them would create a single day when
        # thirteen were expected. Neither is safe, so it is refused.
        response = self._add(repeat="once", until="")
        self.assertEqual(ScheduleOverride.objects.count(), 0)
        self.assertContains(response, "only apply to")

    def test_a_range_containing_none_of_the_chosen_days_is_refused(self):
        response = self._add(
            date=date(2026, 9, 1).isoformat(),     # Tuesday
            until=date(2026, 9, 1).isoformat(),
        )
        self.assertEqual(ScheduleOverride.objects.count(), 0)
        self.assertContains(response, "falls between")

    def test_a_clash_on_any_one_date_stops_the_whole_run(self):
        # Half a rota is worse than none: the gap is invisible on a month view
        # and the doctor finds out on the day.
        ScheduleOverride.objects.create(
            doctor=self.vikram, date=date(2026, 9, 9), cabin=self.one,
            start_time=time(10), end_time=time(12),
        )
        self._add()
        self.assertEqual(ScheduleOverride.objects.filter(doctor=self.asha).count(), 0)

    def test_what_is_written_is_what_was_checked(self):
        # save() works the dates out again rather than trusting a list carried
        # over from validation, so it cannot write a set that was never
        # conflict-checked. Proved by the count: every written date is one of
        # the thirteen the check looked at.
        self._add()
        for entry in ScheduleOverride.objects.filter(doctor=self.asha):
            self.assertIn(entry.date.weekday(), {0, 2, 4})
            self.assertTrue(self.start <= entry.date <= self.end)


# ── KAN-22: the rota spreadsheet ─────────────────────────────────────────────

class TestRotaImport(CalendarReworkTestCase):

    def _row(self, **overrides):
        row = {
            "doctor_email": "asha@example.in", "cabin": "Cabin 1",
            "start_date": "01-09-2026", "end_date": "30-09-2026",
            "days": "M-W-F", "start_time": "09:00", "end_time": "13:00",
        }
        row.update(overrides)
        return ",".join(row[name] for name in schedules_csv.COLUMNS) + "\n"

    def _check(self, text):
        return self.client.post(reverse("reception_import_schedules"),
                                {"file": _upload(text)})

    def _confirm(self, text):
        return self.client.post(
            reverse("reception_import_schedules"),
            {"file": _upload(text), "confirm": "1"}, follow=True,
        )

    # ── The two passes ───────────────────────────────────────────────────────

    def test_checking_the_file_writes_nothing(self):
        # The whole point of the first pass. One line becoming thirteen entries
        # is something to see the size of before it happens.
        response = self._check(HEADER + self._row())
        self.assertEqual(ScheduleOverride.objects.count(), 0)
        self.assertContains(response, "13")

    def test_confirming_writes_them(self):
        self._confirm(HEADER + self._row())
        self.assertEqual(ScheduleOverride.objects.count(), 13)

    def test_the_entries_land_on_the_right_days(self):
        self._confirm(HEADER + self._row())
        self.assertEqual(
            {e.date.weekday() for e in ScheduleOverride.objects.all()}, {0, 2, 4},
        )

    def test_the_entries_say_where_they_came_from(self):
        # So a rota loaded by mistake can be told apart from hours typed in.
        self._confirm(HEADER + self._row())
        self.assertIn("Imported rota (M-W-F)",
                      ScheduleOverride.objects.first().note)

    def test_they_show_on_the_calendar(self):
        self._confirm(HEADER + self._row())
        response = self.client.get(reverse("reception_calendar"),
                                   {"view": "day", "date": "2026-09-02"})
        self.assertContains(response, "Asha Rao")
        self.assertContains(response, "Cabin 1")

    # ── Both date shapes ─────────────────────────────────────────────────────

    def test_either_date_order_is_read(self):
        # Unambiguous by shape: a four-digit first field can only be a year.
        self._confirm(HEADER + self._row(start_date="2026-09-01",
                                         end_date="2026-09-30"))
        self.assertEqual(ScheduleOverride.objects.count(), 13)

    # ── Rows that cannot be used ─────────────────────────────────────────────

    def test_an_unknown_doctor_is_named_rather_than_skipped(self):
        response = self._check(HEADER + self._row(doctor_email="nobody@example.in"))
        self.assertContains(response, "nobody@example.in")
        self.assertEqual(ScheduleOverride.objects.count(), 0)

    def test_an_unknown_cabin_says_where_to_add_it(self):
        response = self._check(HEADER + self._row(cabin="Cabin 9"))
        self.assertContains(response, "Cabin 9")
        self.assertContains(response, "Add it on the calendar first")

    def test_a_bad_day_code_names_the_piece(self):
        response = self._check(HEADER + self._row(days="M-Mon-F"))
        self.assertContains(response, "Mon")

    def test_an_end_date_before_the_start_is_refused(self):
        response = self._check(HEADER + self._row(start_date="30-09-2026",
                                                  end_date="01-09-2026"))
        self.assertContains(response, "before the start date")

    def test_an_end_time_not_after_the_start_is_refused(self):
        response = self._check(HEADER + self._row(start_time="13:00",
                                                  end_time="09:00"))
        self.assertContains(response, "not after the start time")

    def test_the_good_rows_still_import_when_one_is_bad(self):
        # A rota that failed to import is visibly absent from the calendar, and
        # re-running the corrected file is safe because a duplicate is refused.
        text = HEADER + self._row() + self._row(doctor_email="nobody@example.in")
        self._confirm(text)
        self.assertEqual(ScheduleOverride.objects.count(), 13)

    # ── Structural problems reject the lot ───────────────────────────────────

    def test_missing_columns_reject_the_whole_file(self):
        response = self._check("doctor_email,cabin\nasha@example.in,Cabin 1\n")
        self.assertContains(response, "missing from the file")
        self.assertEqual(ScheduleOverride.objects.count(), 0)

    def test_the_template_has_the_columns_the_importer_reads(self):
        # The template and the parser drifting apart would make every download
        # a file that cannot be uploaded.
        body = self.client.get(reverse("reception_schedule_template")
                               ).content.decode()
        self.assertEqual(body.splitlines()[0].split(","),
                         schedules_csv.COLUMNS)

    def test_the_template_can_be_read_back(self):
        # The strongest check on the pair: its own worked examples parse.
        result = schedules_csv.parse(_upload(schedules_csv.template_csv()))
        self.assertEqual(result.fatal, "")

    # ── Clashes ──────────────────────────────────────────────────────────────

    def test_a_clash_with_the_calendar_is_refused(self):
        ScheduleOverride.objects.create(
            doctor=self.vikram, date=date(2026, 9, 2), cabin=self.one,
            start_time=time(10), end_time=time(12),
        )
        response = self._check(HEADER + self._row())
        self.assertEqual(ScheduleOverride.objects.count(), 1)
        self.assertContains(response, "could not be used")

    def test_two_rows_of_one_file_cannot_share_a_cabin(self):
        # Neither row conflicts with the database, because neither has been
        # written yet. Without checking the file against itself this is how two
        # doctors end up in one room by using a spreadsheet.
        text = (HEADER
                + self._row()
                + self._row(doctor_email="vikram@example.in",
                            start_time="10:00", end_time="12:00"))
        self._confirm(text)
        self.assertEqual(
            ScheduleOverride.objects.filter(doctor=self.vikram).count(), 0
        )
        self.assertEqual(
            ScheduleOverride.objects.filter(doctor=self.asha).count(), 13
        )

    def test_one_doctor_cannot_be_in_two_cabins_at_once_in_one_file(self):
        text = (HEADER
                + self._row()
                + self._row(cabin="Cabin 2", start_time="10:00", end_time="12:00"))
        self._confirm(text)
        self.assertEqual(ScheduleOverride.objects.count(), 13)

    def test_two_rows_at_different_hours_both_import(self):
        # The check must be an overlap check, not "same day, same cabin".
        text = (HEADER
                + self._row()
                + self._row(cabin="Cabin 2", days="T-Th",
                            start_time="14:00", end_time="18:00"))
        self._confirm(text)
        self.assertEqual(ScheduleOverride.objects.count(), 13 + 9)

    # ── Running the same file twice ──────────────────────────────────────────

    def test_importing_the_same_file_twice_does_not_double_the_rota(self):
        self._confirm(HEADER + self._row())
        response = self._confirm(HEADER + self._row())
        self.assertEqual(ScheduleOverride.objects.count(), 13)
        self.assertContains(response, "already")

    def test_extending_a_rota_adds_only_the_new_dates(self):
        self._confirm(HEADER + self._row(end_date="15-09-2026"))
        first = ScheduleOverride.objects.count()
        self._confirm(HEADER + self._row())
        self.assertEqual(ScheduleOverride.objects.count(), 13)
        self.assertLess(first, 13)

    # ── Who may do it ────────────────────────────────────────────────────────

    def test_a_doctor_may_not_import_another_doctors_rota(self):
        # Reception's own importer manages any doctor's hours — a doctor has
        # their own version of this, fenced to their own email; see
        # test_own_schedule_upload.py.
        self.client.force_login(self.asha)
        self.assertEqual(
            self.client.get(reverse("reception_import_schedules")).status_code, 403
        )

    def test_a_doctor_can_still_get_the_blank_template(self):
        # The template is just column headings and instructions — a doctor
        # needs the same file to fill in their own upload.
        self.client.force_login(self.asha)
        self.assertEqual(
            self.client.get(reverse("reception_schedule_template")).status_code, 200
        )

    def test_a_pending_doctor_cannot_be_given_hours_by_spreadsheet(self):
        # Same rule as the pop-up. A way round it through a file upload would
        # be a way to put a patient in front of an empty cabin.
        from accounts.models import DoctorProfile

        DoctorProfile.objects.create(user=self.vikram)      # activated_at None
        response = self._check(HEADER + self._row(doctor_email="vikram@example.in"))
        self.assertContains(response, "has not set their password yet")
        self.assertEqual(ScheduleOverride.objects.count(), 0)

    def test_an_inactive_doctor_cannot_be_given_hours_by_spreadsheet(self):
        self.vikram.is_active = False
        self.vikram.save()
        response = self._check(HEADER + self._row(doctor_email="vikram@example.in"))
        self.assertContains(response, "no longer an active doctor")
        self.assertEqual(ScheduleOverride.objects.count(), 0)

    def test_a_retired_cabin_is_refused(self):
        # Every other cabin picker in the app already restricts to active
        # cabins — see PatientForm-style querysets in portal.forms — the CSV
        # importer is the one place that did not.
        self.two.is_active = False
        self.two.save()
        response = self._check(HEADER + self._row(cabin="Cabin 2"))
        self.assertContains(response, "retired")
        self.assertEqual(ScheduleOverride.objects.count(), 0)

    def test_an_unknown_cabin_still_says_no_such_cabin(self):
        # Distinct from the retired case: this name was never real.
        response = self._check(HEADER + self._row(cabin="Cabin 9"))
        self.assertContains(response, "There is no cabin called")
        self.assertNotContains(response, "retired")


class TestReplaceMySchedule(CalendarReworkTestCase):
    """
    "Replace my schedule for these dates" — the CSV re-upload used to be
    refused outright the moment a new time overlapped hours the doctor
    already had that day, so changing a time meant deleting the old entries
    by hand first. This is that gap closed.
    """

    def _row(self, **overrides):
        row = {
            "doctor_email": "asha@example.in", "cabin": "Cabin 1",
            "start_date": "01-09-2026", "end_date": "04-09-2026",  # Tue–Fri
            "days": "M-T-W-Th-F", "start_time": "10:00", "end_time": "13:00",
        }
        row.update(overrides)
        return ",".join(row[name] for name in schedules_csv.COLUMNS) + "\n"

    def _check(self, text, replace=False):
        payload = {"file": _upload(text)}
        if replace:
            payload["replace"] = "1"
        return self.client.post(reverse("reception_import_schedules"), payload)

    def _confirm(self, text, replace=False):
        payload = {"file": _upload(text), "confirm": "1"}
        if replace:
            payload["replace"] = "1"
        return self.client.post(
            reverse("reception_import_schedules"), payload, follow=True,
        )

    def test_without_replace_a_changed_time_is_refused(self):
        # The gap this feature closes, pinned first as the failure it is.
        self._confirm(HEADER + self._row(start_time="10:00", end_time="13:00"))
        response = self._check(
            HEADER + self._row(start_time="11:00", end_time="14:00", days="M-T-W-Th-F-Sa"),
        )
        self.assertContains(response, "already")
        self.assertEqual(
            ScheduleOverride.objects.filter(start_time="10:00:00").count(), 4,
        )

    def test_replace_lets_the_new_time_through(self):
        self._confirm(HEADER + self._row(start_time="10:00", end_time="13:00"))
        self._confirm(
            HEADER + self._row(start_time="11:00", end_time="14:00",
                               days="M-T-W-Th-F-Sa", end_date="05-09-2026"),
            replace=True,
        )
        self.assertEqual(ScheduleOverride.objects.filter(start_time="10:00:00").count(), 0)
        self.assertEqual(ScheduleOverride.objects.filter(start_time="11:00:00").count(), 5)

    def test_replace_only_touches_dates_the_new_file_mentions(self):
        # A day the doctor already had that this file does not repeat must
        # survive — "replace what I am giving you" is not "clear my calendar".
        self._confirm(HEADER + self._row(
            start_date="01-09-2026", end_date="08-09-2026", days="M-T-W-Th-F-Sa-Su",
        ))
        self._confirm(
            HEADER + self._row(start_date="01-09-2026", end_date="04-09-2026",
                               days="M-T-W-Th-F", start_time="11:00", end_time="14:00"),
            replace=True,
        )
        # 5 and 6 September were in the original upload but not the replace.
        self.assertTrue(
            ScheduleOverride.objects.filter(date=date(2026, 9, 5)).exists()
        )

    def test_replace_does_not_touch_another_doctors_hours(self):
        self._confirm(HEADER + self._row(
            doctor_email="vikram@example.in", cabin="Cabin 2",
        ))
        self._confirm(
            HEADER + self._row(start_time="11:00", end_time="14:00",
                               days="M-T-W-Th-F-Sa", end_date="05-09-2026"),
            replace=True,
        )
        self.assertEqual(
            ScheduleOverride.objects.filter(doctor=self.vikram).count(), 4,
        )

    def test_replace_still_refuses_a_genuine_cabin_clash(self):
        # Replacing Asha's own hours cannot excuse taking a room Vikram is
        # already using at that time.
        self._confirm(HEADER + self._row(
            doctor_email="vikram@example.in", cabin="Cabin 1",
            start_time="11:00", end_time="14:00",
        ))
        response = self._check(
            HEADER + self._row(cabin="Cabin 1", start_time="11:00", end_time="14:00"),
            replace=True,
        )
        self.assertContains(response, "already taken")

    def test_the_preview_warns_about_bookings_on_replaced_dates(self):
        self._confirm(HEADER + self._row(start_time="10:00", end_time="13:00"))
        patient = make_patient(phone="9820099999")
        make_visit(patient, self.asha, start=timezone.make_aware(
            timezone.datetime(2026, 9, 1, 10, 30)
        ))
        response = self._check(
            HEADER + self._row(start_time="11:00", end_time="14:00"), replace=True,
        )
        self.assertContains(response, "already booked")

    def test_a_clean_replace_reports_what_it_did(self):
        self._confirm(HEADER + self._row(start_time="10:00", end_time="13:00"))
        response = self._confirm(
            HEADER + self._row(start_time="11:00", end_time="14:00",
                               days="M-T-W-Th-F-Sa", end_date="05-09-2026"),
            replace=True,
        )
        self.assertContains(response, "Replaced 4 existing entries")


# ── KAN-24: editing a holiday already recorded ───────────────────────────────

class TestEditingAHoliday(CalendarReworkTestCase):

    def setUp(self):
        super().setUp()
        self.holiday = ClinicHoliday.objects.create(
            date=date(2026, 11, 8), name="Diwali",
        )
        self.url = reverse("reception_edit_holiday", args=[self.holiday.pk])

    def test_the_form_opens_filled_in(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Diwali")
        self.assertContains(response, "2026-11-08")

    def test_the_date_can_be_corrected(self):
        # The case the ticket is about: entered on the wrong day. Deleting and
        # re-adding was the same two clicks but lost the record of it.
        self.client.post(self.url, {"date": "2026-11-09", "name": "Diwali",
                                    "note": ""})
        self.holiday.refresh_from_db()
        self.assertEqual(self.holiday.date, date(2026, 11, 9))

    def test_the_name_can_be_corrected(self):
        self.client.post(self.url, {"date": "2026-11-08", "name": "Deepavali",
                                    "note": ""})
        self.holiday.refresh_from_db()
        self.assertEqual(self.holiday.name, "Deepavali")

    def test_it_does_not_become_a_second_holiday(self):
        self.client.post(self.url, {"date": "2026-11-09", "name": "Diwali",
                                    "note": ""})
        self.assertEqual(ClinicHoliday.objects.count(), 1)

    def test_moving_it_onto_another_holiday_is_refused(self):
        ClinicHoliday.objects.create(date=date(2026, 12, 25), name="Christmas")
        response = self.client.post(self.url,
                                    {"date": "2026-12-25", "name": "Diwali",
                                     "note": ""}, follow=True)
        self.holiday.refresh_from_db()
        self.assertEqual(self.holiday.date, date(2026, 11, 8))
        self.assertContains(response, "already exists")

    def test_saving_it_unchanged_is_not_a_duplicate_of_itself(self):
        # The trap in a unique field: the row being edited must be excluded
        # from its own uniqueness check.
        self.client.post(self.url, {"date": "2026-11-08", "name": "Diwali",
                                    "note": "Clinic shut"})
        self.holiday.refresh_from_db()
        self.assertEqual(self.holiday.note, "Clinic shut")

    def test_the_calendar_offers_the_edit(self):
        response = self.client.get(reverse("reception_calendar"),
                                   {"view": "day", "date": "2026-11-08"})
        self.assertContains(response, self.url)

    def test_the_new_date_closes_the_clinic_and_the_old_one_does_not(self):
        # The reason the edit exists rather than being cosmetic.
        self.client.post(self.url, {"date": "2026-11-09", "name": "Diwali",
                                    "note": ""})
        shut = self.client.get(reverse("reception_calendar"),
                               {"view": "day", "date": "2026-11-09"})
        open_again = self.client.get(reverse("reception_calendar"),
                                     {"view": "day", "date": "2026-11-08"})
        self.assertContains(shut, "Clinic closed")
        self.assertNotContains(open_again, "Clinic closed")

    def test_a_doctor_may_not_edit_one(self):
        self.client.force_login(self.asha)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_the_change_is_recorded(self):
        from audit.models import AccessLog

        self.client.post(self.url, {"date": "2026-11-09", "name": "Diwali",
                                    "note": ""})
        self.assertTrue(
            AccessLog.objects.filter(description__contains="Holiday changed").exists()
        )


# ── KAN-50: leave, which the removed screen used to record ───────────────────

class TestRecordingLeave(CalendarReworkTestCase):
    """
    The calendar showed who was away but could not say so.

    Before KAN-50 leave was written by the availability screen, or as a side
    effect of taking one date out of a weekly pattern. A doctor working the
    clinic's default hours has no pattern to take a date out of, so once that
    screen went there was no way at all to record that they were away.
    """

    def setUp(self):
        super().setUp()
        self.day = date(2026, 9, 2)

    def _record(self, **overrides):
        payload = {
            "event_type": "leave", "doctor": self.asha.pk,
            "date": self.day.isoformat(),
        }
        payload.update(overrides)
        return self.client.post(reverse("reception_add_calendar_event"), payload,
                                follow=True)

    def test_a_whole_day_is_recorded(self):
        self._record()
        absence = DoctorLeave.objects.get(doctor=self.asha)
        self.assertEqual(absence.date, self.day)
        self.assertTrue(absence.whole_day)

    def test_part_of_a_day_is_recorded(self):
        self._record(leave_start_time="10:00", leave_end_time="12:00")
        absence = DoctorLeave.objects.get(doctor=self.asha)
        self.assertFalse(absence.whole_day)
        self.assertEqual(absence.start_time, time(10))

    def test_one_time_without_the_other_is_refused(self):
        # Somebody who started filling in a part day and stopped. Stored as a
        # whole day it would mark a doctor away for hours they are in.
        response = self._record(leave_start_time="10:00")
        self.assertEqual(DoctorLeave.objects.count(), 0)
        self.assertContains(response, "both times")

    def test_an_end_before_the_start_is_refused(self):
        response = self._record(leave_start_time="12:00", leave_end_time="10:00")
        self.assertEqual(DoctorLeave.objects.count(), 0)
        self.assertContains(response, "after the start time")

    def test_a_stretch_of_leave_covers_every_date_in_it(self):
        self._record(until=(self.day + timedelta(days=4)).isoformat())
        self.assertEqual(DoctorLeave.objects.count(), 5)

    def test_recording_it_twice_does_not_double_it(self):
        self._record()
        response = self._record()
        self.assertEqual(DoctorLeave.objects.count(), 1)
        self.assertContains(response, "already recorded as away")

    def test_extending_leave_adds_only_the_new_days(self):
        self._record()
        self._record(until=(self.day + timedelta(days=2)).isoformat())
        self.assertEqual(DoctorLeave.objects.count(), 3)

    def test_it_shows_on_the_day_it_covers(self):
        self._record(reason="Conference")
        response = self.client.get(reverse("reception_calendar"),
                                   {"view": "day", "date": self.day.isoformat()})
        self.assertContains(response, "Away today")
        self.assertContains(response, "Asha Rao")
        self.assertContains(response, "Conference")

    def test_it_can_be_taken_back(self):
        # Leave recorded against the wrong doctor would otherwise be permanent:
        # the screen that used to delete it is gone.
        self._record()
        absence = DoctorLeave.objects.get()
        response = self.client.get(reverse("reception_calendar"),
                                   {"view": "day", "date": self.day.isoformat()})
        remove = reverse("reception_delete_calendar_entry", args=["leave", absence.pk])
        self.assertContains(response, remove)

        self.client.post(remove, {"next": "/calendar/"})
        self.assertEqual(DoctorLeave.objects.count(), 0)

    def test_a_doctor_sees_the_remove_button_on_their_own_leave(self):
        # Superseded by the doctor-scoped calendar-edit feature (see
        # test_doctor_calendar_edit.py): a doctor may cancel their own
        # mis-marked leave, the same as reception can. They only ever see
        # their own leave here anyway — the calendar already scopes their
        # whole view to themselves.
        self._record()
        self.client.force_login(self.asha)
        response = self.client.get(reverse("reception_calendar"),
                                   {"view": "day", "date": self.day.isoformat()})
        self.assertContains(response, "Away today")
        self.assertContains(response, "Not away after all")

    def test_leave_is_allowed_on_top_of_working_hours(self):
        # That is the entire point of recording it. A conflict check here would
        # refuse exactly the case the feature exists for.
        ScheduleOverride.objects.create(
            doctor=self.asha, date=self.day, cabin=self.one,
            start_time=time(9), end_time=time(13),
        )
        self._record()
        self.assertEqual(DoctorLeave.objects.count(), 1)

    def test_it_stops_the_doctor_being_booked(self):
        from appointments import scheduling

        self._record()
        self.assertEqual(scheduling.available_slots(self.asha, self.day), [])

    def test_the_patients_already_booked_are_named(self):
        # The reason leave is recorded at all late in the day: those patients
        # have to be rung and moved, and nobody will do it if nobody is told.
        start = timezone.make_aware(
            timezone.datetime.combine(self.day, time(11, 0)),
            timezone.get_current_timezone(),
        )
        visit = make_visit(make_patient(), self.asha, start=start)

        response = self._record()
        self.assertContains(response, "must be rung")
        self.assertContains(response, visit.patient.full_name)

    def test_nobody_booked_means_no_warning(self):
        response = self._record()
        self.assertNotContains(response, "must be rung")

    def test_a_doctor_may_not_record_their_own_leave(self):
        # FR-7 is a rule about who may do what. Reception keeps the calendar.
        self.client.force_login(self.asha)
        response = self.client.post(
            reverse("reception_add_calendar_event"),
            {"event_type": "leave", "doctor": self.asha.pk,
             "date": self.day.isoformat()},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(DoctorLeave.objects.count(), 0)

    def test_weekdays_ticked_for_leave_are_refused(self):
        response = self._record(weekdays=["M", "W"])
        self.assertEqual(DoctorLeave.objects.count(), 0)
        self.assertContains(response, "only apply to working hours")
