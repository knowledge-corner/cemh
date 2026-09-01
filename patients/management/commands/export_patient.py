"""
Export one patient's complete record to a single portable JSON file.

    python manage.py export_patient KEC-26-00003

Meant for moving one real patient between two separate installs of this
system — a local copy and the clinic's live database — not for bulk transfer.
See import_patient for the other half, and _patient_transfer.py for the
format itself.
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from patients.models import Patient

from ._patient_transfer import encode_file, serialize_instance, username_of


class Command(BaseCommand):
    help = "Export one patient and their whole clinical record to a JSON file."

    def add_arguments(self, parser):
        parser.add_argument("patient_id", help="The patient's UHID, e.g. KEC-26-00003")
        parser.add_argument(
            "--out", help="Output file path. Defaults to patient_<UHID>_export.json"
        )

    def handle(self, *args, **options):
        patient_id = options["patient_id"]
        try:
            patient = Patient.objects.select_related("user", "history").get(
                patient_id=patient_id
            )
        except Patient.DoesNotExist:
            raise CommandError(f"No patient with UHID {patient_id!r}.")

        data = {
            "format_version": 1,
            "patient": serialize_instance(
                patient, fk_natural_keys={"user": username_of}
            ),
            "history": None,
            "visits": [],
            "investigations": [],
            "diagnoses": [],
            "reference_letters": [],
            "prescriptions": [],
            "measurements": [],
        }
        # patient_id is a real field on Patient, kept as-is above — import
        # decides separately whether to reuse it or mint a fresh one.

        history = getattr(patient, "history", None)
        if history is not None:
            data["history"] = serialize_instance(history)

        visit_index_by_pk = {}
        visits = list(patient.visits.order_by("scheduled_start").select_related(
            "doctor", "booked_by"
        ))
        for index, visit in enumerate(visits):
            visit_index_by_pk[visit.pk] = index
            entry = serialize_instance(
                visit,
                fk_natural_keys={"doctor": username_of, "booked_by": username_of},
            )
            entry["status_events"] = [
                serialize_instance(event, fk_natural_keys={"changed_by": username_of})
                for event in visit.status_events.order_by("created_at")
            ]

            note = getattr(visit, "note", None)
            entry["note"] = (
                serialize_instance(note, fk_natural_keys={"author": username_of})
                if note is not None else None
            )

            charge = getattr(visit, "charge", None)
            if charge is not None:
                charge_data = serialize_instance(
                    charge, fk_natural_keys={"set_by": username_of}
                )
                charge_data["payments"] = []
                for payment in charge.payments.order_by("received_at"):
                    payment_data = serialize_instance(
                        payment, fk_natural_keys={"received_by": username_of}
                    )
                    receipt = getattr(payment, "receipt", None)
                    payment_data["receipt"] = (
                        serialize_instance(receipt) if receipt is not None else None
                    )
                    charge_data["payments"].append(payment_data)
                entry["charge"] = charge_data
            else:
                entry["charge"] = None

            data["visits"].append(entry)

        def visit_ref(visit_id):
            return visit_index_by_pk.get(visit_id)

        for investigation in patient.investigations.select_related("lab_test", "recorded_by"):
            entry = serialize_instance(
                investigation,
                fk_natural_keys={
                    "lab_test": lambda lt: lt.code,
                    "recorded_by": username_of,
                },
            )
            entry["visit_ref"] = visit_ref(investigation.visit_id)
            entry["report_file_name"], entry["report_file_b64"] = encode_file(
                investigation.report_file
            )
            data["investigations"].append(entry)

        for diagnosis in patient.diagnoses.all():
            entry = serialize_instance(diagnosis)
            entry["visit_ref"] = visit_ref(diagnosis.visit_id)
            data["diagnoses"].append(entry)

        for letter in patient.reference_letters.select_related("doctor"):
            entry = serialize_instance(letter, fk_natural_keys={"doctor": username_of})
            entry["visit_ref"] = visit_ref(letter.visit_id)
            data["reference_letters"].append(entry)

        for prescription in patient.prescriptions.select_related("doctor").prefetch_related("items"):
            entry = serialize_instance(
                prescription, fk_natural_keys={"doctor": username_of}
            )
            entry["visit_ref"] = visit_ref(prescription.visit_id)
            entry["scanned_file_name"], entry["scanned_file_b64"] = encode_file(
                prescription.scanned_file
            )
            entry["items"] = [
                serialize_instance(item) for item in prescription.items.order_by("order", "id")
            ]
            data["prescriptions"].append(entry)

        if "growth" in settings.INSTALLED_APPS:
            for measurement in patient.measurements.select_related("recorded_by"):
                entry = serialize_instance(
                    measurement, fk_natural_keys={"recorded_by": username_of}
                )
                entry["visit_ref"] = visit_ref(measurement.visit_id)
                data["measurements"].append(entry)

        out_path = options["out"] or f"patient_{patient.patient_id}_export.json"
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)

        self.stdout.write(self.style.SUCCESS(
            f"Exported {patient.patient_id} ({patient.full_name}) to {out_path}\n"
            f"  {len(data['visits'])} visit(s), "
            f"{len(data['investigations'])} investigation(s), "
            f"{len(data['prescriptions'])} prescription(s), "
            f"{len(data['diagnoses'])} diagnosis/es, "
            f"{len(data['reference_letters'])} reference letter(s), "
            f"{len(data['measurements'])} measurement(s)."
        ))
        self.stdout.write(
            "Not included, by design: audit log entries (who viewed this file "
            "on this machine — a fact about this database, not the patient)."
        )
