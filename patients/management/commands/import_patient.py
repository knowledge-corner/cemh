"""
Import one patient's complete record from a file produced by export_patient.

    python manage.py import_patient patient_KEC-26-00003_export.json

Runs as a single all-or-nothing transaction: if anything fails partway
(a doctor username this database doesn't have, a scheduling clash), nothing
from this file is left behind, and the file can simply be run again once
whatever it complained about is fixed. See export_patient for the other half.
"""

import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from appointments.models import Visit, VisitStatusEvent
from billing.models import Charge, Payment, Receipt
from clinical.models import ClinicalNote, Diagnosis, Investigation, LabTest, ReferenceLetter
from patients.models import Patient, PatientHistory
from pharmacy.models import Prescription, PrescriptionItem

from ._patient_transfer import apply_fields, force_timestamps, restore_file

User = get_user_model()


class Command(BaseCommand):
    help = "Import one patient's complete record from an export_patient JSON file."

    def add_arguments(self, parser):
        parser.add_argument("file", help="A file produced by export_patient.")
        parser.add_argument(
            "--allow-new-uhid", action="store_true",
            help="If the exported UHID is already taken here, mint a fresh one "
                 "instead of refusing to import.",
        )

    def handle(self, *args, **options):
        with open(options["file"], encoding="utf-8") as fh:
            data = json.load(fh)

        if data.get("format_version") != 1:
            raise CommandError("Unrecognised export file format.")

        self._preflight(data, allow_new_uhid=options["allow_new_uhid"])

        with transaction.atomic():
            summary = self._import(data)

        self.stdout.write(self.style.SUCCESS(
            f"Imported {summary['patient_id']} ({summary['full_name']}).\n"
            f"  {summary['visits']} visit(s), "
            f"{summary['investigations']} investigation(s), "
            f"{summary['prescriptions']} prescription(s), "
            f"{summary['diagnoses']} diagnosis/es, "
            f"{summary['reference_letters']} reference letter(s), "
            f"{summary['measurements']} measurement(s), "
            f"{summary['payments']} payment(s)."
        ))
        if summary["uhid_changed"]:
            self.stdout.write(self.style.WARNING(
                f"The original UHID {data['patient']['patient_id']} was already "
                f"in use here — this patient was given a new one instead: "
                f"{summary['patient_id']}."
            ))
        if summary["measurements_skipped"]:
            self.stdout.write(self.style.WARNING(
                "Growth measurements were in the file but the 'growth' app is "
                "not enabled here, so they were not imported."
            ))

    # ── Checks that must pass before anything is written ────────────────────

    def _preflight(self, data, *, allow_new_uhid):
        phone = data["patient"]["phone"]
        existing = Patient.objects.filter(phone=phone).first()
        if existing is not None:
            raise CommandError(
                f"A patient with phone number {phone} already exists here "
                f"({existing.patient_id} — {existing.full_name}). Refusing to "
                f"guess whether this is the same person; check by hand first."
            )

        uhid = data["patient"]["patient_id"]
        if Patient.objects.filter(patient_id=uhid).exists() and not allow_new_uhid:
            raise CommandError(
                f"UHID {uhid} is already in use here. Re-run with "
                f"--allow-new-uhid to import this patient under a freshly "
                f"generated UHID instead, or investigate the clash first."
            )

        usernames = self._usernames_in(data)
        missing = sorted(usernames - set(
            User.objects.filter(username__in=usernames).values_list("username", flat=True)
        ))
        if missing:
            raise CommandError(
                "These staff usernames appear in the export but do not exist "
                "here — create these accounts first, or they will not match "
                "the same real doctor: " + ", ".join(missing)
            )

        codes = {
            inv["lab_test"] for inv in data["investigations"] if inv.get("lab_test")
        }
        missing_codes = sorted(codes - set(
            LabTest.objects.filter(code__in=codes).values_list("code", flat=True)
        ))
        if missing_codes:
            raise CommandError(
                "These lab test codes appear in the export but do not exist "
                "here: " + ", ".join(missing_codes)
            )

    def _usernames_in(self, data):
        names = set()

        def add(value):
            if value:
                names.add(value)

        add(data["patient"].get("user"))
        for visit in data["visits"]:
            add(visit.get("doctor"))
            add(visit.get("booked_by"))
            for event in visit["status_events"]:
                add(event.get("changed_by"))
            if visit["note"]:
                add(visit["note"].get("author"))
            if visit["charge"]:
                add(visit["charge"].get("set_by"))
                for payment in visit["charge"]["payments"]:
                    add(payment.get("received_by"))
        for inv in data["investigations"]:
            add(inv.get("recorded_by"))
        for letter in data["reference_letters"]:
            add(letter.get("doctor"))
        for prescription in data["prescriptions"]:
            add(prescription.get("doctor"))
        for measurement in data["measurements"]:
            add(measurement.get("recorded_by"))
        return names

    # ── The import itself ────────────────────────────────────────────────────

    def _import(self, data):
        summary = {
            "visits": 0, "investigations": 0, "prescriptions": 0, "diagnoses": 0,
            "reference_letters": 0, "measurements": 0, "payments": 0,
            "uhid_changed": False, "measurements_skipped": False,
        }

        patient_data = dict(data["patient"])
        original_uhid = patient_data.pop("patient_id")
        patient_data.pop("user", None)  # a portal login is not part of this migration

        patient = Patient()
        apply_fields(patient, patient_data, Patient)
        if not Patient.objects.filter(patient_id=original_uhid).exists():
            patient.patient_id = original_uhid
        else:
            summary["uhid_changed"] = True
        patient.save()
        force_timestamps(Patient, patient.pk, data["patient"])
        summary["patient_id"] = patient.patient_id
        summary["full_name"] = patient.full_name

        if data["history"]:
            history = PatientHistory(patient=patient)
            apply_fields(history, data["history"], PatientHistory)
            history.save()
            force_timestamps(PatientHistory, history.pk, data["history"])

        visit_pk_by_ref = {}
        for ref, visit_data in enumerate(data["visits"]):
            visit_pk_by_ref[ref] = self._import_visit(patient, visit_data, summary)

        for entry in data["investigations"]:
            self._import_investigation(patient, entry, visit_pk_by_ref)
            summary["investigations"] += 1

        for entry in data["diagnoses"]:
            self._import_diagnosis(patient, entry, visit_pk_by_ref)
            summary["diagnoses"] += 1

        for entry in data["reference_letters"]:
            self._import_reference_letter(patient, entry, visit_pk_by_ref)
            summary["reference_letters"] += 1

        for entry in data["prescriptions"]:
            self._import_prescription(patient, entry, visit_pk_by_ref)
            summary["prescriptions"] += 1

        if data["measurements"]:
            if "growth" in settings.INSTALLED_APPS:
                from growth.models import Measurement
                for entry in data["measurements"]:
                    self._import_measurement(Measurement, patient, entry, visit_pk_by_ref)
                    summary["measurements"] += 1
            else:
                summary["measurements_skipped"] = True

        return summary

    def _import_visit(self, patient, visit_data, summary):
        fields = dict(visit_data)
        status_events = fields.pop("status_events")
        note_data = fields.pop("note")
        charge_data = fields.pop("charge")

        visit = Visit(patient=patient)
        apply_fields(visit, fields, Visit, natural_key_lookups={
            "doctor": self._user_pk, "booked_by": self._user_pk,
        })
        visit.save()
        force_timestamps(Visit, visit.pk, visit_data)
        summary["visits"] += 1

        for event_data in status_events:
            event = VisitStatusEvent(visit=visit)
            apply_fields(event, event_data, VisitStatusEvent,
                         natural_key_lookups={"changed_by": self._user_pk})
            event.save()
            force_timestamps(VisitStatusEvent, event.pk, event_data)

        if note_data:
            note = ClinicalNote(visit=visit, patient=patient)
            apply_fields(note, note_data, ClinicalNote,
                         natural_key_lookups={"author": self._user_pk})
            note.save()
            force_timestamps(ClinicalNote, note.pk, note_data)

        if charge_data:
            payments = charge_data.pop("payments")
            charge = Charge(visit=visit, patient=patient)
            apply_fields(charge, charge_data, Charge,
                         natural_key_lookups={"set_by": self._user_pk})
            charge.save()
            force_timestamps(Charge, charge.pk, charge_data)

            for payment_data in payments:
                receipt_data = payment_data.pop("receipt")
                payment = Payment(charge=charge)
                apply_fields(payment, payment_data, Payment,
                             natural_key_lookups={"received_by": self._user_pk})
                payment.save()
                summary["payments"] += 1

                if receipt_data:
                    receipt_number = receipt_data.pop("receipt_number", None)
                    receipt = Receipt(payment=payment)
                    apply_fields(receipt, receipt_data, Receipt)
                    if receipt_number and not Receipt.objects.filter(
                        receipt_number=receipt_number
                    ).exists():
                        receipt.receipt_number = receipt_number
                    receipt.save()
                    force_timestamps(Receipt, receipt.pk, receipt_data)

        return visit.pk

    def _import_investigation(self, patient, entry, visit_pk_by_ref):
        fields = dict(entry)
        visit_ref = fields.pop("visit_ref")
        report_name = fields.pop("report_file_name")
        report_b64 = fields.pop("report_file_b64")
        lab_test_code = fields.get("lab_test")

        investigation = Investigation(
            patient=patient,
            visit_id=visit_pk_by_ref.get(visit_ref) if visit_ref is not None else None,
        )
        apply_fields(investigation, fields, Investigation, natural_key_lookups={
            "recorded_by": self._user_pk,
            "lab_test": (lambda code: LabTest.objects.get(code=code).pk) if lab_test_code else (lambda code: None),
        })
        restore_file(investigation.report_file, report_name, report_b64)
        investigation.save()
        force_timestamps(Investigation, investigation.pk, entry)

    def _import_diagnosis(self, patient, entry, visit_pk_by_ref):
        fields = dict(entry)
        visit_ref = fields.pop("visit_ref")
        diagnosis = Diagnosis(
            patient=patient,
            visit_id=visit_pk_by_ref.get(visit_ref) if visit_ref is not None else None,
        )
        apply_fields(diagnosis, fields, Diagnosis)
        diagnosis.save()
        force_timestamps(Diagnosis, diagnosis.pk, entry)

    def _import_reference_letter(self, patient, entry, visit_pk_by_ref):
        fields = dict(entry)
        visit_ref = fields.pop("visit_ref")
        letter = ReferenceLetter(
            patient=patient,
            visit_id=visit_pk_by_ref.get(visit_ref) if visit_ref is not None else None,
        )
        apply_fields(letter, fields, ReferenceLetter,
                     natural_key_lookups={"doctor": self._user_pk})
        letter.save()
        force_timestamps(ReferenceLetter, letter.pk, entry)

    def _import_prescription(self, patient, entry, visit_pk_by_ref):
        fields = dict(entry)
        visit_ref = fields.pop("visit_ref")
        scanned_name = fields.pop("scanned_file_name")
        scanned_b64 = fields.pop("scanned_file_b64")
        items = fields.pop("items")

        prescription = Prescription(
            patient=patient,
            visit_id=visit_pk_by_ref.get(visit_ref) if visit_ref is not None else None,
        )
        apply_fields(prescription, fields, Prescription,
                     natural_key_lookups={"doctor": self._user_pk})
        restore_file(prescription.scanned_file, scanned_name, scanned_b64)
        prescription.save()
        force_timestamps(Prescription, prescription.pk, entry)

        for item_data in items:
            item = PrescriptionItem(prescription=prescription)
            apply_fields(item, item_data, PrescriptionItem)
            item.save()

    def _import_measurement(self, Measurement, patient, entry, visit_pk_by_ref):
        fields = dict(entry)
        visit_ref = fields.pop("visit_ref")
        measurement = Measurement(
            patient=patient,
            visit_id=visit_pk_by_ref.get(visit_ref) if visit_ref is not None else None,
        )
        apply_fields(measurement, fields, Measurement,
                     natural_key_lookups={"recorded_by": self._user_pk})
        measurement.save()
        force_timestamps(Measurement, measurement.pk, entry)

    def _user_pk(self, username):
        return User.objects.get(username=username).pk
