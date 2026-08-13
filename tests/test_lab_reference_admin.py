"""
The admin-side download-template / upload workflow for reference ranges,
and that ICD10Code/LabTest/LabReferenceRange/LabUnitConversion are actually
reachable in the admin at all.
"""

from django.test import TestCase
from django.urls import reverse

from clinical.models import ICD10Code, LabReferenceRange, LabTest, LabUnitConversion

from .factories import make_doctor, make_receptionist, make_user


class TestAdminRegistration(TestCase):
    """Every model a clinician might need to browse or correct is reachable."""

    def setUp(self):
        self.staff = make_user(
            username="opsstaff", email="ops@example.in", is_staff=True, is_superuser=True,
        )
        self.client.force_login(self.staff)

    def test_icd10_changelist_loads(self):
        response = self.client.get(reverse("admin:clinical_icd10code_changelist"))
        self.assertEqual(response.status_code, 200)

    def test_lab_test_changelist_loads(self):
        response = self.client.get(reverse("admin:clinical_labtest_changelist"))
        self.assertEqual(response.status_code, 200)

    def test_lab_reference_range_changelist_loads(self):
        response = self.client.get(reverse("admin:clinical_labreferencerange_changelist"))
        self.assertEqual(response.status_code, 200)

    def test_lab_unit_conversion_changelist_loads(self):
        response = self.client.get(reverse("admin:clinical_labunitconversion_changelist"))
        self.assertEqual(response.status_code, 200)

    def test_icd10_cannot_be_added_or_deleted_in_admin(self):
        self.assertEqual(self.client.get(reverse("admin:clinical_icd10code_add")).status_code, 403)

    def test_the_download_and_upload_links_are_on_the_changelist(self):
        response = self.client.get(reverse("admin:clinical_labreferencerange_changelist"))
        self.assertContains(response, reverse("admin:clinical_labreferencerange_download_template"))
        self.assertContains(response, reverse("admin:clinical_labreferencerange_upload"))


class TestDownloadTemplate(TestCase):
    def setUp(self):
        self.staff = make_user(
            username="opsstaff2", email="ops2@example.in", is_staff=True, is_superuser=True,
        )
        self.client.force_login(self.staff)

    def test_it_downloads_a_csv(self):
        response = self.client.get(reverse("admin:clinical_labreferencerange_download_template"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode()
        self.assertIn("test_code", body.splitlines()[0])
        self.assertEqual(len(body.strip().splitlines()), 2 + LabTest.objects.filter(is_active=True).count())

    def test_a_non_staff_user_is_refused(self):
        doctor = make_doctor()
        self.client.force_login(doctor)
        response = self.client.get(reverse("admin:clinical_labreferencerange_download_template"))
        self.assertNotEqual(response.status_code, 200)


class TestUploadView(TestCase):
    def setUp(self):
        self.staff = make_user(
            username="opsstaff3", email="ops3@example.in", is_staff=True, is_superuser=True,
        )
        self.client.force_login(self.staff)
        self.test = LabTest.objects.get(code="LAB0213")

    def upload_url(self):
        return reverse("admin:clinical_labreferencerange_upload")

    def csv_file(self, **overrides):
        from django.core.files.uploadedfile import SimpleUploadedFile

        row = {
            "test_code": self.test.code, "test_name": self.test.name,
            "sex": "ANY", "age_min": "", "age_max": "", "age_unit": "years",
            "pregnancy_status": "", "fasting_status": "",
            "low": "0.4", "high": "4.0", "unit": "mIU/L",
            "source": "test source", "source_year": "2023",
            "notes": "", "status": "VALIDATED",
        }
        row.update(overrides)
        from clinical import lab_reference_csv
        header = ",".join(lab_reference_csv.COLUMNS)
        line = ",".join(row[c] for c in lab_reference_csv.COLUMNS)
        return SimpleUploadedFile("ranges.csv", f"{header}\n{line}\n".encode(), content_type="text/csv")

    def test_the_page_loads(self):
        response = self.client.get(self.upload_url())
        self.assertEqual(response.status_code, 200)

    def test_uploading_a_valid_row_creates_it(self):
        # A fully clean upload (nothing skipped) redirects straight back to
        # the changelist rather than sitting on a "0 rows skipped" report.
        response = self.client.post(self.upload_url(), {"file": self.csv_file()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LabReferenceRange.objects.count(), 1)

    def test_a_non_staff_user_cannot_upload(self):
        receptionist = make_receptionist()
        self.client.force_login(receptionist)
        response = self.client.post(self.upload_url(), {"file": self.csv_file()})
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(LabReferenceRange.objects.count(), 0)
