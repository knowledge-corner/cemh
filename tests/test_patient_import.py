"""
KAN-11 — the CSV template, and loading a filled-in one.

Listed under both In scope and Out of scope on the ticket. Built, because the
in-scope line spells out what to build and the out-of-scope line is the generic
phrase it was copied from — see the comment on the ticket.

The rule that matters most here is that a file is written whole or not at all.
A half-finished import is the worst available outcome: nobody can tell which
rows landed, running it again duplicates the ones that did, and the clinic ends
up reconciling a spreadsheet against a database by eye.
"""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from patients import importing
from patients.models import Patient, Sex

from .factories import make_doctor, make_receptionist

HEADER = ",".join(importing.COLUMNS)


def csv_file(*rows, name="patients.csv"):
    body = HEADER + "\n" + "\n".join(rows) + "\n"
    return SimpleUploadedFile(name, body.encode("utf-8"), content_type="text/csv")


def parse(*rows):
    return importing.parse(io.BytesIO((HEADER + "\n" + "\n".join(rows) + "\n").encode()))


ADULT = "Meera,Kulkarni,1990-04-23,Female,9820012345,,"
CHILD = "Rohan,Kulkarni,2015-07-02,Male,9820012345,Meera Kulkarni,Mother"


class TestTheTemplate(TestCase):
    def setUp(self):
        self.client.force_login(make_receptionist())

    def test_it_downloads_as_a_csv(self):
        response = self.client.get(reverse("reception_patient_template"))
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])

    def test_it_carries_every_column_the_importer_reads(self):
        body = self.client.get(reverse("reception_patient_template")).content.decode()
        for column in importing.COLUMNS:
            self.assertIn(column, body)

    def test_the_template_is_itself_importable(self):
        # The example rows in the template must survive the importer, or the
        # first thing anybody does with it fails.
        body = self.client.get(reverse("reception_patient_template")).content
        result = importing.parse(io.BytesIO(body))
        self.assertTrue(result.ok, [p.message for p in result.problems])
        self.assertEqual(len(result.created), 2)

    def test_its_help_row_is_not_imported_as_a_patient(self):
        body = self.client.get(reverse("reception_patient_template")).content
        result = importing.parse(io.BytesIO(body))
        self.assertNotIn("Required", [p.first_name for p in result.created])


class TestReadingAFile(TestCase):
    def test_a_clean_file_parses(self):
        result = parse(ADULT, CHILD)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.created), 2)

    def test_nothing_is_written_by_parsing(self):
        parse(ADULT)
        self.assertEqual(Patient.objects.count(), 0)

    def test_a_missing_column_is_refused_with_a_reason(self):
        result = importing.parse(io.BytesIO(b"first_name,last_name\nMeera,Kulkarni\n"))
        self.assertFalse(result.ok)
        self.assertIn("missing", result.problems[0].message)

    def test_an_empty_file_is_refused(self):
        self.assertFalse(importing.parse(io.BytesIO(b"")).ok)

    def test_blank_lines_are_skipped_rather_than_reported(self):
        result = parse(ADULT, ",,,,,,")
        self.assertTrue(result.ok, [p.message for p in result.problems])
        self.assertEqual(len(result.created), 1)

    def test_a_bad_date_names_its_line(self):
        result = parse(ADULT, "Rohan,Kulkarni,not-a-date,Male,9820011111,A,Mother")
        self.assertFalse(result.ok)
        self.assertEqual(result.problems[0].line, 3)

    def test_a_day_first_date_is_accepted(self):
        # What a spreadsheet in India hands you more often than not.
        result = parse("Meera,Kulkarni,23/04/1990,Female,9820012345,,")
        self.assertTrue(result.ok, [p.message for p in result.problems])

    def test_a_child_without_a_guardian_is_a_problem(self):
        result = parse("Rohan,Kulkarni,2015-07-02,Male,9820012345,,")
        self.assertFalse(result.ok)
        self.assertIn("guardian", result.problems[0].message)

    def test_a_short_phone_number_is_a_problem(self):
        result = parse("Meera,Kulkarni,1990-04-23,Female,12345,,")
        self.assertFalse(result.ok)
        self.assertIn("not a full number", result.problems[0].message)

    def test_a_decorated_phone_number_is_fine(self):
        result = parse("Meera,Kulkarni,1990-04-23,Female,+91 98200 12345,,")
        self.assertTrue(result.ok, [p.message for p in result.problems])

    def test_gender_is_read_loosely(self):
        for written, stored in (("Male", Sex.MALE), ("f", Sex.FEMALE),
                                ("FEMALE", Sex.FEMALE),
                                ("Prefer not to say", Sex.NOT_STATED),
                                ("Prefer not to mention", Sex.NOT_STATED)):
            with self.subTest(gender=written):
                result = parse(f"A,B,1990-04-23,{written},9820012345,,")
                self.assertTrue(result.ok, [p.message for p in result.problems])
                self.assertEqual(result.created[0].sex, stored)

    def test_an_unreadable_gender_is_a_problem(self):
        self.assertFalse(parse("A,B,1990-04-23,banana,9820012345,,").ok)

    def test_the_same_person_twice_in_one_file_is_caught(self):
        # Neither row exists yet, so nothing in the database can catch this.
        result = parse(ADULT, ADULT)
        self.assertFalse(result.ok)
        self.assertIn("line 2", result.problems[0].message)

    def test_a_future_date_of_birth_is_a_problem(self):
        tomorrow = (timezone.localdate() + timezone.timedelta(days=1)).isoformat()
        self.assertFalse(parse(f"A,B,{tomorrow},Female,9820012345,,").ok)


class TestAgainstWhatIsAlreadyRegistered(TestCase):
    def setUp(self):
        self.existing = Patient.objects.create(
            first_name="Meera", last_name="Kulkarni",
            date_of_birth=timezone.localdate().replace(year=1990),
            sex=Sex.FEMALE, phone="9820012345",
        )

    def test_a_row_that_is_already_registered_is_skipped_not_rejected(self):
        # Re-importing a file after adding a few rows is the normal way this
        # gets used. Refusing the whole thing because the first hundred are
        # already in would make that impossible.
        result = parse(ADULT, "Priya,Shah,1995-01-01,Female,9769087654,,")
        self.assertTrue(result.ok, [p.message for p in result.problems])
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(len(result.created), 1)

    def test_the_skipped_row_names_the_patient_it_matched(self):
        result = parse(ADULT)
        _, matched = result.skipped[0]
        self.assertEqual(matched.pk, self.existing.pk)

    def test_the_match_ignores_case_and_phone_formatting(self):
        result = parse("MEERA,kulkarni,1990-04-23,Female,+91 98200 12345,,")
        self.assertEqual(len(result.skipped), 1)


class TestWritingTheFile(TestCase):
    def setUp(self):
        self.receptionist = make_receptionist()
        self.client.force_login(self.receptionist)

    def _upload(self, *rows, confirm=False):
        data = {"file": csv_file(*rows)}
        if confirm:
            data["confirm"] = "1"
        return self.client.post(reverse("reception_import_patients"), data)

    def test_the_first_pass_writes_nothing(self):
        response = self._upload(ADULT, CHILD)
        self.assertEqual(Patient.objects.count(), 0)
        self.assertContains(response, "Ready to import")

    def test_confirming_writes_them(self):
        self._upload(ADULT, CHILD, confirm=True)
        self.assertEqual(Patient.objects.count(), 2)

    def test_every_imported_patient_gets_an_id(self):
        # bulk_create would skip the save that allocates one.
        self._upload(ADULT, CHILD, confirm=True)
        self.assertTrue(all(p.patient_id for p in Patient.objects.all()))
        self.assertEqual(len({p.patient_id for p in Patient.objects.all()}), 2)

    def test_one_bad_row_stops_the_whole_file(self):
        self._upload(ADULT, "Rohan,Kulkarni,not-a-date,Male,9820011111,A,Mother",
                     confirm=True)
        self.assertEqual(Patient.objects.count(), 0)

    def test_the_bad_row_is_named(self):
        response = self._upload(ADULT, "x,y,not-a-date,Male,9820011111,A,Mother")
        self.assertContains(response, "Nothing was imported")
        self.assertContains(response, "not a date")

    def test_the_import_is_recorded_in_the_audit_log(self):
        from audit.models import AccessLog

        self._upload(ADULT, confirm=True)
        self.assertTrue(
            AccessLog.objects.filter(description__contains="imported").exists()
        )

    def test_a_doctor_cannot_import_patients(self):
        self.client.force_login(make_doctor())
        self.assertEqual(self._upload(ADULT).status_code, 403)

    def test_a_doctor_cannot_take_the_template_either(self):
        self.client.force_login(make_doctor())
        self.assertEqual(
            self.client.get(reverse("reception_patient_template")).status_code, 403
        )

    def test_pressing_upload_with_no_file_says_so(self):
        response = self.client.post(reverse("reception_import_patients"), {}, follow=True)
        self.assertContains(response, "Choose a CSV file")
