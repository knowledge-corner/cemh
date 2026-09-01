"""
export_patient / import_patient — moving one patient's whole record between
two separate installs of this system.

Tested as a round trip: build a patient with one of everything, export, wipe
every trace of them, import the file back, and check what comes back matches
what went in — including the things that are easy to get wrong silently
(historical timestamps auto_now/auto_now_add would otherwise overwrite, file
contents, and a doctor/lab-test reference re-resolved by name rather than by
a raw id that means nothing in a different database).
"""

from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from appointments.models import Visit, VisitStatus
from billing.models import Charge, Payment, Receipt
from clinical.models import ClinicalNote, Diagnosis, Investigation, LabTest, ReferenceLetter
from patients.models import Patient, PatientHistory
from pharmacy.models import Prescription, PrescriptionItem

from .factories import make_doctor, make_patient, make_receptionist


class TestPatientExportImportRoundTrip(TestCase):
    def setUp(self):
        self.doctor = make_doctor(username="dr.round.trip")
        self.receptionist = make_receptionist(username="recep.round.trip")
        self.patient = make_patient(
            first_name="Meera", last_name="Joshi", phone="9820099999",
            email="meera@example.in", blood_group="B+",
        )
        PatientHistory.objects.create(
            patient=self.patient, allergies="Penicillin",
            family_history="Mother — hypothyroid",
        )

        start = timezone.now() - timedelta(days=10)
        self.visit = Visit.objects.create(
            patient=self.patient, doctor=self.doctor,
            scheduled_start=start, scheduled_end=start + timedelta(minutes=20),
            status=VisitStatus.COMPLETED, reason="Thyroid follow-up",
            arrived_at=start, consulted_at=start + timedelta(minutes=5),
        )
        self.visit.status_events.create(
            from_status=VisitStatus.BOOKED, to_status=VisitStatus.ARRIVED,
            changed_by=self.receptionist, note="Checked in at the desk",
        )
        ClinicalNote.objects.create(
            visit=self.visit, patient=self.patient, author=self.doctor,
            clinical_notes="Feeling well.", prescription_note="Continue current dose.",
        )

        lab_test = LabTest.objects.first()
        self.assertIsNotNone(lab_test, "seed migration should have loaded lab tests")
        Investigation.objects.create(
            patient=self.patient, visit=self.visit, lab_test=lab_test,
            test_name=lab_test.name, value="2.1", value_numeric=Decimal("2.1"),
            unit="mIU/L", recorded_by=self.doctor,
            report_file=SimpleUploadedFile("tsh_report.txt", b"TSH: 2.1 mIU/L"),
        )
        Diagnosis.objects.create(
            patient=self.patient, visit=self.visit,
            description="Hypothyroidism", icd10_code="E03.9",
        )
        ReferenceLetter.objects.create(
            patient=self.patient, visit=self.visit, doctor=self.doctor,
            to="School", note="Fit to attend school.",
        )

        prescription = Prescription.objects.create(
            visit=self.visit, patient=self.patient, doctor=self.doctor,
            investigations_advised="Repeat TSH in 3 months",
            scanned_file=SimpleUploadedFile("scan.txt", b"handwritten scan bytes"),
        )
        PrescriptionItem.objects.create(
            prescription=prescription, drug_name="Levothyroxine",
            strength="50 mcg", dosage="1 tablet", frequency="Once daily", order=0,
        )

        charge = Charge.objects.create(
            visit=self.visit, patient=self.patient,
            consultation_fee=Decimal("800.00"), set_by=self.doctor,
        )
        payment = Payment.objects.create(
            charge=charge, amount=Decimal("800.00"), received_by=self.receptionist,
        )
        Receipt.objects.create(payment=payment)

        self.original_created_at = self.patient.created_at
        self.original_visit_created_at = self.visit.created_at

    def _export(self, out_path):
        call_command("export_patient", self.patient.patient_id, "--out", out_path)

    def _forget_locally(self):
        """
        Simulate this patient never having existed here — the real-world
        equivalent of "this database is separate from the source machine".

        Charge/Payment/Receipt/Visit all PROTECT the row above them from
        deletion (deliberately — money owed is a task, not tidying), so a
        plain patient.delete() cannot cascade through them; each has to go
        in dependency order first.
        """
        Receipt.objects.filter(payment__charge__patient=self.patient).delete()
        Payment.objects.filter(charge__patient=self.patient).delete()
        Charge.objects.filter(patient=self.patient).delete()
        Visit.objects.filter(patient=self.patient).delete()
        self.patient.delete()

    def test_export_then_import_recreates_the_whole_record(self):
        out_path = "/tmp/test_patient_export.json"
        self._export(out_path)

        original_uhid = self.patient.patient_id
        # Wipe every trace, as if this were a genuinely separate database —
        # the doctor and lab test stay, since those exist independently on
        # both sides and are re-resolved by name, not carried in the file.
        self._forget_locally()

        call_command("import_patient", out_path)

        imported = Patient.objects.get(patient_id=original_uhid)
        self.assertEqual(imported.full_name, "Meera Joshi")
        self.assertEqual(imported.phone, "9820099999")
        self.assertEqual(imported.blood_group, "B+")
        # auto_now_add would otherwise silently reset this to "now".
        self.assertEqual(imported.created_at, self.original_created_at)

        self.assertEqual(imported.history.allergies, "Penicillin")

        visit = imported.visits.get()
        self.assertEqual(visit.doctor_id, self.doctor.pk)
        self.assertEqual(visit.status, VisitStatus.COMPLETED)
        self.assertEqual(visit.created_at, self.original_visit_created_at)

        event = visit.status_events.get()
        self.assertEqual(event.changed_by_id, self.receptionist.pk)
        self.assertEqual(event.note, "Checked in at the desk")

        self.assertEqual(visit.note.author_id, self.doctor.pk)
        self.assertEqual(visit.note.prescription_note, "Continue current dose.")

        investigation = imported.investigations.get()
        self.assertEqual(investigation.value, "2.1")
        self.assertEqual(investigation.value_numeric, Decimal("2.1"))
        self.assertEqual(investigation.lab_test_id, LabTest.objects.first().pk)
        self.assertEqual(investigation.recorded_by_id, self.doctor.pk)
        investigation.report_file.open("rb")
        self.assertEqual(investigation.report_file.read(), b"TSH: 2.1 mIU/L")
        investigation.report_file.close()

        diagnosis = imported.diagnoses.get()
        self.assertEqual(diagnosis.icd10_code, "E03.9")

        letter = imported.reference_letters.get()
        self.assertEqual(letter.doctor_id, self.doctor.pk)
        self.assertEqual(letter.to, "School")

        prescription = imported.prescriptions.get()
        self.assertEqual(prescription.doctor_id, self.doctor.pk)
        prescription.scanned_file.open("rb")
        self.assertEqual(prescription.scanned_file.read(), b"handwritten scan bytes")
        prescription.scanned_file.close()
        item = prescription.items.get()
        self.assertEqual(item.drug_name, "Levothyroxine")

        charge = visit.charge
        self.assertEqual(charge.consultation_fee, Decimal("800.00"))
        self.assertEqual(charge.set_by_id, self.doctor.pk)
        payment = charge.payments.get()
        self.assertEqual(payment.amount, Decimal("800.00"))
        self.assertEqual(payment.received_by_id, self.receptionist.pk)
        # A fresh, valid receipt number — not required to match the original,
        # but must exist and be well-formed.
        self.assertTrue(payment.receipt.receipt_number)

    def test_it_refuses_when_the_phone_number_already_exists(self):
        out_path = "/tmp/test_patient_export_dup_phone.json"
        self._export(out_path)
        # Patient is left in place this time — same phone number, so a second
        # import must refuse rather than silently create a duplicate person.
        with self.assertRaises(Exception):
            call_command("import_patient", out_path)
        self.assertEqual(
            Patient.objects.filter(phone="9820099999").count(), 1,
            "a refused import must not leave a partial row behind",
        )

    def test_it_mints_a_new_uhid_on_request_when_the_original_is_taken(self):
        out_path = "/tmp/test_patient_export_dup_uhid.json"
        self._export(out_path)
        original_uhid = self.patient.patient_id
        # Same UHID already taken, different phone number this time.
        self.patient.phone = "9820000000"
        self.patient.save()

        call_command("import_patient", out_path, "--allow-new-uhid")

        imported = Patient.objects.get(phone="9820099999")
        self.assertNotEqual(imported.patient_id, original_uhid)

    def test_it_refuses_a_missing_doctor_username_before_writing_anything(self):
        out_path = "/tmp/test_patient_export_missing_doctor.json"
        self._export(out_path)
        self._forget_locally()
        self.doctor.delete()

        with self.assertRaises(Exception):
            call_command("import_patient", out_path)
        self.assertFalse(Patient.objects.filter(phone="9820099999").exists())
