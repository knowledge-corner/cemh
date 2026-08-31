"""
Populate the database with realistic demo data.

Exists so the doctor's dashboard can be reviewed against something that looks
like a real endocrine practice — multi-year follow-up, growth trajectories,
thyroid trends — rather than one empty patient.

    python manage.py seed_demo

Safe to re-run: it clears the demo records it created and rebuilds them. It
refuses to run when DEBUG is off unless --force is given, so it can never be
pointed at the live clinic database by accident.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from accounts.models import DoctorProfile, Role
from appointments.models import Visit, VisitStatus
from billing.models import RECEIPT_SEQUENCE, Charge, Payment, Receipt
from clinical.models import ClinicalNote, Diagnosis, Investigation, InvestigationCategory
from patients.models import PATIENT_ID_SEQUENCE, Patient, PatientHistory
from pharmacy.models import Prescription, PrescriptionItem

User = get_user_model()

DEMO_PASSWORD = "clinicdemo2026"


class Command(BaseCommand):
    help = "Create demo doctors, receptionist and patients with clinical history."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow seeding when DEBUG is off. Never use on the clinic's live database.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to seed demo data with DEBUG off. Re-run with --force "
                "only if you are certain this is not the clinic's live database."
            )

        random.seed(20260730)  # Reproducible: the same demo data every run.

        with transaction.atomic():
            self._clear()
            doctors = self._create_staff()
            self._create_patients(doctors)

        self.stdout.write(self.style.SUCCESS("\nDemo data created."))
        self.stdout.write(f"\n  Sign in at /login/ — password for every demo account: {DEMO_PASSWORD}\n")
        self.stdout.write("    adway       Dr. Adway Kulkarni     (doctor)")
        self.stdout.write("    vrushali    Dr. Vrushali Kulkarni  (doctor, paediatric)")
        self.stdout.write("    reception   Sunita Rane            (receptionist)")
        self.stdout.write("")
        for patient in Patient.objects.order_by("patient_id"):
            self.stdout.write(
                f"    {patient.patient_id}  {patient.full_name:24s} "
                f"{patient.age_display:>7s}  {patient.get_sex_display()}"
            )

    # ── Teardown ──────────────────────────────────────────────────────────

    def _clear(self):
        """
        Remove previously seeded demo records so the command is re-runnable.

        Order matters: visits protect patients, and payments protect charges, so
        the chain has to come apart from the outside in. Deleting a visit takes
        its note, prescription and charge with it; deleting a patient takes their
        history, diagnoses, investigations and measurements.
        """
        Receipt.objects.all().delete()
        Payment.objects.all().delete()
        Visit.objects.all().delete()
        Patient.objects.all().delete()
        User.objects.filter(is_superuser=False).delete()

        # Restart UHID and receipt numbering so demo data is reproducible.
        # Only ever done here — in the real clinic the sequences must keep
        # climbing, because a reused identifier is far worse than a gap.
        with connection.cursor() as cursor:
            cursor.execute(f"ALTER SEQUENCE {PATIENT_ID_SEQUENCE} RESTART WITH 1")
            cursor.execute(f"ALTER SEQUENCE {RECEIPT_SEQUENCE} RESTART WITH 1")

    # ── Staff ─────────────────────────────────────────────────────────────

    def _create_staff(self):
        adway = User.objects.create_user(
            username="adway",
            email="adway@example.in",
            password=DEMO_PASSWORD,
            first_name="Adway",
            last_name="Kulkarni",
            phone="9137396433",
            role=Role.DOCTOR,
        )
        DoctorProfile.objects.create(
            user=adway,
            qualification="MBBS, DNB (Medicine), DrNB (Endocrinology)",
            speciality="Endocrinology Consultant",
            registration_number="MH-DEMO-11482",
        )

        vrushali = User.objects.create_user(
            username="vrushali",
            email="vrushali@example.in",
            password=DEMO_PASSWORD,
            first_name="Vrushali",
            last_name="Kulkarni",
            phone="7620351240",
            role=Role.DOCTOR,
        )
        DoctorProfile.objects.create(
            user=vrushali,
            qualification="MBBS, MD (Pediatrics), DNB (Pediatrics)",
            speciality="Pediatric Endocrinology Consultant",
            registration_number="MH-DEMO-11483",
        )

        receptionist = User.objects.create_user(
            username="reception",
            email="reception@example.in",
            password=DEMO_PASSWORD,
            first_name="Sunita",
            last_name="Rane",
            phone="9820011223",
            role=Role.RECEPTIONIST,
        )

        return {"adult": adway, "paediatric": vrushali, "reception": receptionist}

    # ── Patients ──────────────────────────────────────────────────────────

    def _create_patients(self, doctors):
        today = timezone.localdate()

        self._short_stature_child(doctors["paediatric"], today)
        self._type1_diabetes_child(doctors["paediatric"], today)
        self._hypothyroid_adult(doctors["adult"], today)
        self._type2_diabetes_adult(doctors["adult"], today)
        self._pcos_adult(doctors["adult"], today)

        self._awaiting_confirmation(doctors, today)
        self._ready_to_bill(doctors["adult"], today)

    # ── Extras that give the reception screens something to show ─────────────

    def _awaiting_confirmation(self, doctors, today):
        """
        Bookings reception has taken but not yet confirmed by telephone.

        These are what fills the queue board's "To confirm" column, so the
        calling workflow is visible in the demo.
        """
        for patient, doctor, days, hour, reason in [
            (Patient.objects.get(first_name="Priya"), doctors["adult"], 3, 11, "PCOS review"),
            (Patient.objects.get(first_name="Ishita"), doctors["paediatric"], 6, 15, "Insulin dose review"),
        ]:
            start = timezone.make_aware(
                timezone.datetime.combine(today + timedelta(days=days), timezone.datetime.min.time())
            ) + timedelta(hours=hour)
            Visit.objects.create(
                patient=patient, doctor=doctor,
                scheduled_start=start, scheduled_end=start + timedelta(minutes=20),
                reason=reason, is_follow_up=True,
                booked_by=doctors["reception"],
                # Left BOOKED: the confirmation call has not been made yet.
            )

    def _ready_to_bill(self, doctor, today):
        """A patient the doctor has finished with, waiting to pay."""
        patient = Patient.objects.get(first_name="Priya")
        now = timezone.localtime()
        start = now.replace(hour=9, minute=30, second=0, microsecond=0)

        visit = Visit.objects.create(
            patient=patient, doctor=doctor,
            scheduled_start=start, scheduled_end=start + timedelta(minutes=20),
            reason="PCOS follow-up", is_follow_up=True,
        )
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED,
                       VisitStatus.IN_CABIN, VisitStatus.CONSULTED):
            visit.transition_to(status, by_user=doctor)

        ClinicalNote.objects.create(
            visit=visit, patient=patient, author=doctor,
            complaints="Cycles regular over the last three months.",
            assessment="Good response to metformin and lifestyle change.",
            plan="Continue current regimen. Review in 3 months.",
        )

        prescription = Prescription.objects.create(
            visit=visit, patient=patient, doctor=doctor,
            advice="Continue daily walking. Maintain current diet.",
            follow_up_number=3, follow_up_unit=Prescription.FollowUpUnit.MONTH,
        )
        PrescriptionItem.objects.create(
            prescription=prescription, drug_name="Metformin", strength="500 mg",
            dosage="1 tablet", frequency="Twice daily after meals",
            duration="3 months", order=0,
        )
        prescription.generate()

        Charge.objects.create(
            visit=visit, patient=patient,
            consultation_fee=Decimal("800.00"), set_by=doctor,
        )

    # -- Case 1: paediatric short stature, the classic referral -------------

    def _short_stature_child(self, doctor, today):
        patient = Patient.objects.create(
            first_name="Aarav", last_name="Deshpande",
            date_of_birth=today - timedelta(days=int(9.2 * 365.25)),
            sex="M", phone="9820012345",
            guardian_name="Meera Deshpande", guardian_relation="Mother",
            guardian_phone="9820012345",
            city="Mumbai", pincode="400092",
            referred_by="Dr. S. Prabhu, Paediatrician",
            registered_on=today - timedelta(days=760),
        )
        PatientHistory.objects.create(
            patient=patient,
            presenting_complaints="Short stature — consistently shortest in class.",
            family_history="Father 165 cm, mother 152 cm. No known endocrine disease.",
            birth_history="Term, normal delivery, birth weight 2.9 kg. Milestones normal.",
            past_medical_history="Nil significant.",
            current_medications="Nil.",
        )
        Diagnosis.objects.create(
            patient=patient, description="Growth and short stature — under evaluation",
            diagnosed_on=today - timedelta(days=740),
        )

        # Height tracking below the 3rd centile with a slow velocity — the
        # pattern the growth chart is meant to make obvious.
        series = [
            (760, 112.0, 19.4), (580, 115.1, 20.3), (400, 118.0, 21.5),
            (215, 120.6, 22.4), (35, 123.1, 23.6),
        ]
        self._add_visits_with_measurements(
            patient, doctor, series,
            reason="Short stature follow-up",
            assessment="Height below 3rd centile, growth velocity subnormal.",
            plan="Bone age and IGF-1. Continue 6-monthly monitoring.",
            parents=(152.0, 165.0),
        )

        for days, tsh, igf in [(740, "3.1", "84"), (400, "2.8", "92"), (35, "2.6", "88")]:
            performed = today - timedelta(days=days)
            Investigation.objects.create(
                patient=patient, test_name="TSH", category=InvestigationCategory.ENDOCRINOLOGY,
                value=tsh, value_numeric=Decimal(tsh), unit="µIU/mL",
                reference_range="0.5 – 4.5", performed_on=performed, lab_name="Metropolis",
            )
            Investigation.objects.create(
                patient=patient, test_name="IGF-1", category=InvestigationCategory.ENDOCRINOLOGY,
                value=igf, value_numeric=Decimal(igf), unit="ng/mL",
                reference_range="110 – 565", is_abnormal=True,
                performed_on=performed, lab_name="Metropolis",
                notes="Below age-matched reference.",
            )

        self._todays_visit(patient, doctor, VisitStatus.IN_CABIN, "Short stature — 6-month review")

    # -- Case 2: paediatric type 1 diabetes --------------------------------

    def _type1_diabetes_child(self, doctor, today):
        patient = Patient.objects.create(
            first_name="Ishita", last_name="Kulkarni",
            date_of_birth=today - timedelta(days=int(12.6 * 365.25)),
            sex="F", phone="9930022334",
            guardian_name="Rohit Kulkarni", guardian_relation="Father",
            guardian_phone="9930022334",
            city="Mumbai", pincode="400092",
            registered_on=today - timedelta(days=1100),
        )
        PatientHistory.objects.create(
            patient=patient,
            presenting_complaints="Known type 1 diabetes, on basal-bolus insulin.",
            past_medical_history="Diagnosed at age 9 following DKA.",
            family_history="Maternal aunt with type 1 diabetes.",
            allergies="Sulfa drugs — rash.",
            current_medications="Insulin glargine 14 U at night; insulin aspart with meals.",
        )
        Diagnosis.objects.create(
            patient=patient, description="Childhood diabetes (type 1)",
            diagnosed_on=today - timedelta(days=1090),
        )

        series = [
            (1090, 132.0, 28.0), (720, 139.5, 32.5),
            (360, 146.0, 38.0), (25, 150.5, 42.5),
        ]
        self._add_visits_with_measurements(
            patient, doctor, series,
            reason="Type 1 diabetes review",
            assessment="Glycaemic control improving. Growth on target.",
            plan="Continue current regimen. Review HbA1c in 3 months.",
            parents=(158.0, 172.0),
        )

        for days, hba1c in [(1090, "11.2"), (720, "8.9"), (360, "8.1"), (25, "7.4")]:
            Investigation.objects.create(
                patient=patient, test_name="HbA1c", category=InvestigationCategory.CLINICAL_CHEMISTRY,
                value=hba1c, value_numeric=Decimal(hba1c), unit="%",
                reference_range="< 7.0 (target)", is_abnormal=float(hba1c) >= 7.0,
                performed_on=today - timedelta(days=days), lab_name="SRL Diagnostics",
            )

        self._todays_visit(patient, doctor, VisitStatus.ARRIVED, "Diabetes quarterly review")

    # -- Case 3: adult hypothyroidism --------------------------------------

    def _hypothyroid_adult(self, doctor, today):
        patient = Patient.objects.create(
            first_name="Sunita", last_name="Menon",
            date_of_birth=date(1979, 4, 14), sex="F", phone="9821123456",
            blood_group="B+", city="Mumbai", pincode="400092",
            registered_on=today - timedelta(days=1500),
        )
        PatientHistory.objects.create(
            patient=patient,
            presenting_complaints="Fatigue, weight gain, cold intolerance.",
            past_medical_history="Hypothyroidism since 2019.",
            family_history="Mother and sister both hypothyroid.",
            current_medications="Levothyroxine 75 mcg daily, fasting.",
            lifestyle_notes="Sedentary desk job. Walks 20 minutes most days.",
        )
        Diagnosis.objects.create(
            patient=patient, description="Thyroid disorders — primary hypothyroidism",
            diagnosed_on=today - timedelta(days=1480),
        )

        for days, tsh, abnormal in [(1480, "11.4", True), (1100, "6.2", True),
                                    (700, "3.8", False), (300, "2.9", False), (20, "2.4", False)]:
            Investigation.objects.create(
                patient=patient, test_name="TSH", category=InvestigationCategory.ENDOCRINOLOGY,
                value=tsh, value_numeric=Decimal(tsh), unit="µIU/mL",
                reference_range="0.5 – 4.5", is_abnormal=abnormal,
                performed_on=today - timedelta(days=days), lab_name="Thyrocare",
            )
        Investigation.objects.create(
            patient=patient, test_name="Vitamin D (25-OH)", category=InvestigationCategory.CLINICAL_CHEMISTRY,
            value="18", value_numeric=Decimal("18"), unit="ng/mL",
            reference_range="30 – 100", is_abnormal=True,
            performed_on=today - timedelta(days=300), lab_name="Thyrocare",
            notes="Deficient. Supplementation advised.",
        )

        self._add_simple_visits(
            patient, doctor,
            [(1480, "Fatigue and weight gain", "Primary hypothyroidism confirmed.",
              "Start levothyroxine 50 mcg. Repeat TFT in 8 weeks."),
             (700, "Thyroid review", "TSH normalising on 75 mcg.",
              "Continue same dose. Annual review."),
             (20, "Annual thyroid review", "Euthyroid on current dose. Vitamin D deficient.",
              "Continue levothyroxine 75 mcg. Start cholecalciferol 60,000 IU weekly x 8.")],
        )
        self._todays_visit(patient, doctor, VisitStatus.CONFIRMED, "Annual review")
        return patient

    # -- Case 4: adult type 2 diabetes -------------------------------------

    def _type2_diabetes_adult(self, doctor, today):
        patient = Patient.objects.create(
            first_name="Ramesh", last_name="Iyer",
            date_of_birth=date(1968, 11, 2), sex="M", phone="9769087654",
            blood_group="O+", city="Mumbai", pincode="400091",
            registered_on=today - timedelta(days=900),
        )
        PatientHistory.objects.create(
            patient=patient,
            presenting_complaints="Type 2 diabetes with suboptimal control.",
            past_medical_history="Type 2 diabetes 12 years. Hypertension 6 years.",
            family_history="Both parents diabetic.",
            current_medications="Metformin 1 g twice daily; Telmisartan 40 mg daily.",
            lifestyle_notes="Irregular meals due to travel. Minimal exercise.",
        )
        Diagnosis.objects.create(
            patient=patient, description="Diabetes and metabolic disorders (type 2)",
            diagnosed_on=today - timedelta(days=890),
        )
        Diagnosis.objects.create(
            patient=patient, description="Endocrine hypertension — under evaluation",
            diagnosed_on=today - timedelta(days=200),
        )

        for days, hba1c in [(890, "9.4"), (540, "8.6"), (240, "8.2"), (30, "7.8")]:
            Investigation.objects.create(
                patient=patient, test_name="HbA1c", category=InvestigationCategory.CLINICAL_CHEMISTRY,
                value=hba1c, value_numeric=Decimal(hba1c), unit="%",
                reference_range="< 7.0 (target)", is_abnormal=True,
                performed_on=today - timedelta(days=days), lab_name="SRL Diagnostics",
            )
        Investigation.objects.create(
            patient=patient, test_name="LDL Cholesterol", category=InvestigationCategory.CLINICAL_CHEMISTRY,
            value="142", value_numeric=Decimal("142"), unit="mg/dL",
            reference_range="< 100", is_abnormal=True,
            performed_on=today - timedelta(days=30), lab_name="SRL Diagnostics",
        )

        self._add_simple_visits(
            patient, doctor,
            [(890, "Diabetes review", "HbA1c 9.4%. Poor control.",
              "Optimise metformin. Dietitian referral."),
             (240, "Diabetes review", "Modest improvement. BP borderline.",
              "Add gliclazide. Continue telmisartan."),
             (30, "Diabetes and BP review", "HbA1c 7.8%. Dyslipidaemia noted.",
              "Start atorvastatin 10 mg. Screen for secondary hypertension.")],
            vitals=(148, 92, 82),
        )
        self._todays_visit(patient, doctor, VisitStatus.BOOKED, "Follow-up — lipids and BP")

    # -- Case 5: adult PCOS ------------------------------------------------

    def _pcos_adult(self, doctor, today):
        patient = Patient.objects.create(
            first_name="Priya", last_name="Shah",
            date_of_birth=date(1998, 7, 21), sex="F", phone="9004556677",
            city="Mumbai", pincode="400092",
            registered_on=today - timedelta(days=210),
        )
        PatientHistory.objects.create(
            patient=patient,
            presenting_complaints="Irregular cycles, acne, weight gain.",
            family_history="Mother with type 2 diabetes.",
            current_medications="Nil.",
            lifestyle_notes="Started walking 30 minutes daily three months ago.",
        )
        Diagnosis.objects.create(
            patient=patient, description="Polycystic ovarian syndrome (PCOS)",
            diagnosed_on=today - timedelta(days=190),
        )
        Investigation.objects.create(
            patient=patient, test_name="Fasting Insulin", category=InvestigationCategory.ENDOCRINOLOGY,
            value="24.6", value_numeric=Decimal("24.6"), unit="µIU/mL",
            reference_range="2.6 – 24.9", performed_on=today - timedelta(days=190),
            lab_name="Metropolis", notes="Upper end of range; insulin resistance likely.",
        )
        Investigation.objects.create(
            patient=patient, test_name="Testosterone (total)", category=InvestigationCategory.ENDOCRINOLOGY,
            value="68", value_numeric=Decimal("68"), unit="ng/dL",
            reference_range="15 – 70", performed_on=today - timedelta(days=190),
            lab_name="Metropolis",
        )

        self._add_simple_visits(
            patient, doctor,
            [(190, "Irregular cycles and acne", "Clinical and biochemical picture consistent with PCOS.",
              "Lifestyle modification. Start metformin 500 mg twice daily."),
             (25, "PCOS review", "Cycles more regular. 3 kg weight loss.",
              "Continue metformin and lifestyle measures. Review in 3 months.")],
        )

    # ── Shared builders ───────────────────────────────────────────────────

    def _add_visits_with_measurements(self, patient, doctor, series, *, reason,
                                      assessment, plan, parents):
        """Create a run of completed visits, each with a measurement attached."""
        from django.apps import apps

        Measurement = apps.get_model("growth", "Measurement") if apps.is_installed("growth") else None
        mother_height, father_height = parents

        for days_ago, height, weight in series:
            when = timezone.localdate() - timedelta(days=days_ago)
            visit = self._completed_visit(patient, doctor, when, reason)

            ClinicalNote.objects.create(
                visit=visit, patient=patient, author=doctor,
                complaints=reason, examination=f"Height {height} cm, weight {weight} kg.",
                assessment=assessment, plan=plan,
            )
            if Measurement:
                Measurement.objects.create(
                    patient=patient, visit=visit, measured_on=when,
                    height_cm=Decimal(str(height)), weight_kg=Decimal(str(weight)),
                    mother_height_cm=Decimal(str(mother_height)),
                    father_height_cm=Decimal(str(father_height)),
                    recorded_by=doctor,
                )

    def _add_simple_visits(self, patient, doctor, entries, vitals=None):
        for days_ago, reason, assessment, plan in entries:
            when = timezone.localdate() - timedelta(days=days_ago)
            visit = self._completed_visit(patient, doctor, when, reason)

            note_kwargs = {}
            if vitals:
                note_kwargs = {
                    "systolic_bp": vitals[0], "diastolic_bp": vitals[1], "pulse": vitals[2]
                }
            ClinicalNote.objects.create(
                visit=visit, patient=patient, author=doctor,
                complaints=reason, assessment=assessment, plan=plan, **note_kwargs
            )

            prescription = Prescription.objects.create(
                visit=visit, patient=patient, doctor=doctor,
                advice="Diet and activity advice reinforced.",
                follow_up_number=6, follow_up_unit=Prescription.FollowUpUnit.MONTH,
                generated_at=timezone.make_aware(
                    timezone.datetime.combine(when, timezone.datetime.min.time())
                ) + timedelta(hours=11),
            )
            PrescriptionItem.objects.create(
                prescription=prescription, drug_name="As per plan above",
                instructions=plan, order=0,
            )

    def _completed_visit(self, patient, doctor, when, reason):
        """A visit in the past, walked through the full lifecycle."""
        start = timezone.make_aware(
            timezone.datetime.combine(when, timezone.datetime.min.time())
        ) + timedelta(hours=random.randint(10, 17))

        visit = Visit.objects.create(
            patient=patient, doctor=doctor,
            scheduled_start=start, scheduled_end=start + timedelta(minutes=20),
            reason=reason, is_follow_up=True,
        )
        # Move it through the real state machine rather than setting status
        # directly — this also exercises the transition rules.
        for status in (VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.IN_CABIN,
                       VisitStatus.CONSULTED, VisitStatus.BILLED, VisitStatus.COMPLETED):
            visit.transition_to(status, by_user=doctor)
        return visit

    def _todays_visit(self, patient, doctor, status, reason):
        """A visit for today, left at the given point in the workflow."""
        now = timezone.localtime()
        start = now.replace(hour=random.randint(10, 16), minute=random.choice([0, 15, 30, 45]),
                            second=0, microsecond=0)

        visit = Visit.objects.create(
            patient=patient, doctor=doctor,
            scheduled_start=start, scheduled_end=start + timedelta(minutes=20),
            reason=reason, is_follow_up=True,
        )

        path = [VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.IN_CABIN]
        for step in path:
            if visit.status == status:
                break
            visit.transition_to(step, by_user=doctor)
        return visit
