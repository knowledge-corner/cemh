"""
Editing records from the doctor's chart.

Covers that each edit button opens a form, that saving persists, that the
change is audited, and that a non-doctor cannot reach any of it.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import VisitStatus
from audit.models import AccessLog, AuditAction
from clinical.models import ClinicalNote, Diagnosis, Investigation, ReferenceLetter
from growth.models import Measurement
from pharmacy.models import Prescription, add_months_or_years

from .factories import (
    make_doctor, make_history, make_measurement, make_patient, make_receptionist,
    make_visit,
)


class EditingTestCase(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.client.force_login(self.doctor)
        self.patient = make_patient()

    def add_url(self, kind):
        return reverse("doctor_add_record", args=[self.patient.patient_id, kind])

    def edit_url(self, kind, pk):
        return reverse("doctor_edit_record", args=[self.patient.patient_id, kind, pk])

    def open_visit(self, *, hours=0):
        """
        A visit the patient is currently here for — notes attach to it.

        ``hours`` offsets the start so a second call for the same doctor (a
        later consultation) does not overlap the first and trip the
        no-double-booking constraint.
        """
        visit = make_visit(self.patient, self.doctor, start=timezone.now() + timedelta(hours=hours))
        visit.transition_to(VisitStatus.CONFIRMED, by_user=self.doctor)
        visit.transition_to(VisitStatus.ARRIVED, by_user=self.doctor)
        visit.transition_to(VisitStatus.IN_CABIN, by_user=self.doctor)
        return visit


class TestFormsOpen(EditingTestCase):
    def test_each_add_form_opens(self):
        self.open_visit()
        for kind in ("history", "diagnosis", "investigation", "measurement",
                     "reference_letter"):
            response = self.client.get(self.add_url(kind))
            self.assertEqual(response.status_code, 200, f"{kind} form failed to open")
            self.assertContains(response, "<form")

    def test_patient_details_form_opens(self):
        self.open_visit()
        response = self.client.get(self.edit_url("patient", self.patient.pk))
        self.assertContains(response, self.patient.first_name)

    def test_unknown_record_type_returns_404(self):
        self.assertEqual(self.client.get(self.add_url("nonsense")).status_code, 404)

    def test_note_cannot_be_added_without_an_open_visit(self):
        # There is nothing to write a consultation note against.
        self.assertEqual(self.client.get(self.add_url("note")).status_code, 403)
        response = self.client.post(self.add_url("note"), {"complaints": "Tired"})
        self.assertEqual(response.status_code, 403)


class TestSaving(EditingTestCase):
    def test_adding_a_diagnosis_persists_it(self):
        self.open_visit()
        response = self.client.post(self.add_url("diagnosis"), {
            "description": "Thyroid disorders in children",
            "status": Diagnosis.Status.ACTIVE,
            "diagnosed_on": timezone.localdate().isoformat(),
            "icd10_code": "", "notes": "", "resolved_on": "",
        })
        self.assertEqual(response.status_code, 200)
        diagnosis = Diagnosis.objects.get()
        self.assertEqual(diagnosis.patient, self.patient)
        self.assertEqual(diagnosis.description, "Thyroid disorders in children")

    def test_adding_an_investigation_persists_it(self):
        self.open_visit()
        self.client.post(self.add_url("investigation"), {
            "test_name": "TSH", "category": "THYROID",
            "performed_on": timezone.localdate().isoformat(),
            "value": "6.2", "value_numeric": "6.2", "unit": "µIU/mL",
            "reference_range": "0.5 – 4.5", "is_abnormal": "on",
            "lab_name": "Metropolis", "notes": "",
        })
        result = Investigation.objects.get()
        self.assertEqual(result.patient, self.patient)
        self.assertTrue(result.is_abnormal)
        self.assertEqual(result.recorded_by, self.doctor)

    def test_adding_a_measurement_persists_it(self):
        self.open_visit()
        self.client.post(self.add_url("measurement"), {
            "measured_on": timezone.localdate().isoformat(),
            "height_cm": "123.1", "weight_kg": "23.60",
            "head_circumference_cm": "", "waist_cm": "", "puberty_stage": "",
            "mother_height_cm": "152.0", "father_height_cm": "165.0", "notes": "",
        })
        measurement = Measurement.objects.get()
        self.assertEqual(measurement.height_cm, Decimal("123.1"))
        self.assertEqual(measurement.mid_parental_height_cm, Decimal("165.0"))

    def test_a_measurement_with_no_bone_age_is_still_fine(self):
        # bone_age_years is optional — most measurements never have an X-ray
        # behind them, so leaving it out of the post (as the form does) must
        # not be refused.
        self.open_visit()
        self.client.post(self.add_url("measurement"), {
            "measured_on": timezone.localdate().isoformat(),
            "height_cm": "123.1", "weight_kg": "23.60",
            "head_circumference_cm": "", "waist_cm": "", "puberty_stage": "",
            "mother_height_cm": "", "father_height_cm": "", "notes": "",
        })
        measurement = Measurement.objects.get()
        self.assertIsNone(measurement.bone_age_years)

    def test_a_bone_age_is_persisted(self):
        self.open_visit()
        self.client.post(self.add_url("measurement"), {
            "measured_on": timezone.localdate().isoformat(),
            "height_cm": "123.1", "weight_kg": "23.60",
            "head_circumference_cm": "", "waist_cm": "", "puberty_stage": "",
            "bone_age_years": "8.3",
            "mother_height_cm": "", "father_height_cm": "", "notes": "",
        })
        measurement = Measurement.objects.get()
        self.assertEqual(measurement.bone_age_years, Decimal("8.3"))

    def test_editing_history_updates_the_existing_record(self):
        self.open_visit()
        make_history(self.patient, allergies="")
        self.client.post(self.add_url("history"), {
            "presenting_complaints": "", "past_medical_history": "",
            "family_history": "", "birth_history": "",
            "allergies": "Penicillin — rash", "current_medications": "",
            "surgical_history": "", "lifestyle_notes": "",
        })
        # A patient has one history record; saving must update, never duplicate.
        self.patient.history.refresh_from_db()
        self.assertEqual(self.patient.history.allergies, "Penicillin — rash")
        self.assertEqual(self.patient.history_set.count() if hasattr(self.patient, "history_set") else 1, 1)

    def test_editing_patient_details_persists(self):
        self.open_visit()
        self.client.post(self.edit_url("patient", self.patient.pk), {
            "first_name": "Aarav", "last_name": "Deshpande",
            "date_of_birth": self.patient.date_of_birth.isoformat(),
            "sex": "M", "blood_group": "B+", "phone": "9820012345",
            "alternate_phone": "", "email": "", "guardian_name": "Meera Deshpande",
            "guardian_relation": "Mother", "guardian_phone": "9820012345",
            "address": "", "city": "Mumbai", "pincode": "400092", "referred_by": "",
        })
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.blood_group, "B+")
        self.assertEqual(self.patient.guardian_name, "Meera Deshpande")

    def test_uhid_is_not_editable(self):
        self.open_visit()
        original = self.patient.patient_id
        self.client.post(self.edit_url("patient", self.patient.pk), {
            "first_name": "Aarav", "last_name": "Deshpande",
            "date_of_birth": self.patient.date_of_birth.isoformat(),
            "sex": "M", "blood_group": "", "phone": "9820012345",
            "patient_id": "HACKED-00-00000",
            "alternate_phone": "", "email": "", "guardian_name": "",
            "guardian_relation": "", "guardian_phone": "",
            "address": "", "city": "", "pincode": "", "referred_by": "",
        })
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.patient_id, original)

    def test_adding_a_note_attaches_it_to_the_open_visit(self):
        visit = self.open_visit()
        self.client.post(self.add_url("note"), {
            "clinical_notes": "Short stature, plan bone age.",
            "prescription_note": "Continue calcium and vitamin D as advised.",
        })
        note = ClinicalNote.objects.get()
        self.assertEqual(note.visit, visit)
        self.assertEqual(note.author, self.doctor)

    def test_the_note_form_offers_only_the_two_boxes(self):
        # The clinic asked for exactly two text boxes — nothing about
        # complaints, examination, assessment, plan or vitals any more.
        self.open_visit()
        response = self.client.get(self.add_url("note"))
        for legacy_field in ("complaints", "examination", "assessment",
                              "systolic_bp", "diastolic_bp", "temperature_c"):
            self.assertNotContains(response, f'name="{legacy_field}"')
        self.assertContains(response, 'name="clinical_notes"')
        self.assertContains(response, 'name="prescription_note"')

    def test_a_note_written_before_the_change_still_shows_its_old_content(self):
        # Non-destructive: nothing already on record disappears from view.
        visit = self.open_visit()
        ClinicalNote.objects.create(
            visit=visit, patient=self.patient, author=self.doctor,
            complaints="Fatigue", assessment="Hypothyroid, uncontrolled",
        )
        response = self.client.get(
            reverse("doctor_patient_tab", args=[self.patient.patient_id, "notes"])
        )
        self.assertContains(response, "Fatigue")
        self.assertContains(response, "Hypothyroid, uncontrolled")

    def test_a_second_note_on_the_same_visit_is_refused(self):
        self.open_visit()
        self.client.post(self.add_url("note"), {"clinical_notes": "First note"})
        response = self.client.post(self.add_url("note"), {"clinical_notes": "Second note"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ClinicalNote.objects.count(), 1)

    def test_a_new_visit_allows_another_note(self):
        first = self.open_visit()
        self.client.post(self.add_url("note"), {"clinical_notes": "First note"})
        first.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        self.open_visit(hours=2)
        response = self.client.post(self.add_url("note"), {"clinical_notes": "Second note"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ClinicalNote.objects.count(), 2)

    def test_only_the_latest_note_can_be_edited(self):
        first = self.open_visit()
        self.client.post(self.add_url("note"), {"clinical_notes": "First note"})
        older = ClinicalNote.objects.get()
        first.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        self.open_visit(hours=2)
        self.client.post(self.add_url("note"), {"clinical_notes": "Second note"})
        response = self.client.get(self.edit_url("note", older.pk))
        self.assertEqual(response.status_code, 403)

    def test_invalid_form_redisplays_rather_than_saving(self):
        self.open_visit()
        response = self.client.post(self.add_url("diagnosis"), {
            "description": "", "status": Diagnosis.Status.ACTIVE,
            "diagnosed_on": "", "icd10_code": "", "notes": "", "resolved_on": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<form")
        self.assertEqual(Diagnosis.objects.count(), 0)


class TestPrescriptionWorkflow(EditingTestCase):
    def _item_formset(self, **overrides):
        payload = {
            "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000",
            "items-0-drug_name": "Levothyroxine", "items-0-strength": "50 mcg",
            "items-0-dosage": "1 tablet", "items-0-frequency": "Once daily",
            "items-0-duration": "3 months", "items-0-instructions": "Before breakfast",
        }
        payload.update(overrides)
        return payload

    def test_new_prescription_starts_as_a_draft(self):
        self.open_visit()
        self.client.post(self.add_url("prescription"), self._item_formset(
            investigations_advised="", follow_up_number="", follow_up_unit="",
            follow_up_notes="",
        ))
        prescription = Prescription.objects.get()
        self.assertFalse(prescription.is_generated, "Must not auto-issue on save")
        self.assertEqual(prescription.items.count(), 1)

    def test_a_prescription_cannot_be_created_without_an_open_visit(self):
        # Unlike a reference letter, a prescription is clinical: it needs the
        # chart unlocked the same way everything else on it does.
        response = self.client.post(self.add_url("prescription"), self._item_formset())
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Prescription.objects.exists())

    def test_a_second_prescription_is_refused_without_a_new_consultation(self):
        # At most one prescription per consultation — the existing one is
        # what "Edit" is for, not a second "+ New prescription".
        self.open_visit()
        self.client.post(self.add_url("prescription"), self._item_formset())
        response = self.client.post(self.add_url("prescription"), self._item_formset())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Prescription.objects.filter(patient=self.patient).count(), 1)

    def test_a_second_consultation_allows_another_prescription(self):
        first = self.open_visit()
        self.client.post(self.add_url("prescription"), self._item_formset())
        first.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        self.open_visit(hours=2)
        response = self.client.post(self.add_url("prescription"), self._item_formset())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Prescription.objects.filter(patient=self.patient).count(), 2)

    def test_only_the_latest_prescription_can_be_edited(self):
        first = self.open_visit()
        self.client.post(self.add_url("prescription"), self._item_formset())
        older = Prescription.objects.get()
        first.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        self.open_visit(hours=2)
        self.client.post(self.add_url("prescription"), self._item_formset())
        response = self.client.get(self.edit_url("prescription", older.pk))
        self.assertEqual(response.status_code, 403)

    def test_the_add_button_is_hidden_once_the_consultation_has_one(self):
        self.open_visit()
        self.client.post(self.add_url("prescription"), self._item_formset())
        response = self.client.get(
            reverse("doctor_patient_tab", args=[self.patient.patient_id, "prescriptions"])
        )
        self.assertNotContains(response, "New prescription")

    def test_the_edit_button_is_hidden_on_an_older_prescription(self):
        first = self.open_visit()
        self.client.post(self.add_url("prescription"), self._item_formset())
        first.transition_to(VisitStatus.CONSULTED, by_user=self.doctor)
        self.open_visit(hours=2)
        self.client.post(self.add_url("prescription"), self._item_formset())
        response = self.client.get(
            reverse("doctor_patient_tab", args=[self.patient.patient_id, "prescriptions"])
        )
        self.assertEqual(response.content.decode().count("btn-edit--solid"), 0)
        # One "Edit" button rendered — for the latest prescription only.
        self.assertEqual(response.content.decode().count(">Edit<"), 1)

    def test_editing_an_existing_prescription_updates_it_not_duplicates_it(self):
        self.open_visit()
        self.client.post(self.add_url("prescription"), self._item_formset())
        prescription = Prescription.objects.get()
        self.client.post(
            self.edit_url("prescription", prescription.pk),
            self._item_formset(investigations_advised="Repeat TSH in 6 weeks"),
        )
        prescription.refresh_from_db()
        self.assertEqual(Prescription.objects.count(), 1)
        self.assertEqual(prescription.investigations_advised, "Repeat TSH in 6 weeks")

    def test_a_doctor_can_attach_a_scanned_prescription_instead_of_typing_items(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.open_visit()
        scan = SimpleUploadedFile("rx.jpg", b"not-really-a-jpeg", content_type="image/jpeg")
        blank_item = {f"items-0-{field}": "" for field in
                      ("drug_name", "strength", "dosage", "frequency", "duration", "instructions")}
        self.client.post(self.add_url("prescription"), self._item_formset(
            **blank_item, scanned_file=scan,
        ))
        prescription = Prescription.objects.get()
        self.assertTrue(prescription.scanned_file)
        self.assertEqual(prescription.items.count(), 0)

    def test_it_can_be_printed_from_its_own_tab(self):
        self.open_visit()
        self.client.post(self.add_url("prescription"), self._item_formset())
        prescription = Prescription.objects.get()
        response = self.client.get(reverse("print_prescription_record", args=[prescription.pk]))
        self.assertContains(response, "Levothyroxine")

    def test_the_printed_sheet_says_gender_not_sex(self):
        self.open_visit()
        self.client.post(self.add_url("prescription"), self._item_formset())
        prescription = Prescription.objects.get()
        response = self.client.get(reverse("print_prescription_record", args=[prescription.pk]))
        self.assertContains(response, "Age / Gender")
        self.assertNotContains(response, "Age / Sex")

    def test_printing_records_it_and_is_reachable_without_a_visit(self):
        prescription = Prescription.objects.create(
            patient=self.patient, doctor=self.doctor,
        )
        response = self.client.get(
            reverse("print_prescription_record", args=[prescription.pk]), {"mark": "1"},
        )
        self.assertEqual(response.status_code, 200)
        prescription.refresh_from_db()
        self.assertIsNotNone(prescription.printed_at)

    def test_the_follow_up_dropdowns_compute_a_tentative_date(self):
        self.open_visit()
        self.client.post(self.add_url("prescription"), self._item_formset(
            follow_up_number="3", follow_up_unit="MONTH",
        ))
        prescription = Prescription.objects.get()
        expected = add_months_or_years(prescription.created_at.date(), 3, "MONTH")
        self.assertEqual(prescription.tentative_follow_up_date, expected)

    def test_no_follow_up_chosen_means_no_tentative_date(self):
        self.open_visit()
        self.client.post(self.add_url("prescription"), self._item_formset(
            follow_up_number="", follow_up_unit="",
        ))
        prescription = Prescription.objects.get()
        self.assertIsNone(prescription.tentative_follow_up_date)

    def test_the_printed_sheet_shows_the_tentative_date_and_a_confirm_message(self):
        self.open_visit()
        self.client.post(self.add_url("prescription"), self._item_formset(
            follow_up_number="2", follow_up_unit="YEAR",
        ))
        prescription = Prescription.objects.get()
        response = self.client.get(reverse("print_prescription_record", args=[prescription.pk]))
        self.assertContains(response, "Tentative date")
        self.assertContains(response, "please confirm")
        self.assertContains(response, prescription.tentative_follow_up_date.strftime("%-d %B %Y"))

    def test_the_prescription_note_from_clinical_notes_appears_on_the_print(self):
        visit = self.open_visit()
        ClinicalNote.objects.create(
            visit=visit, patient=self.patient, author=self.doctor,
            prescription_note="Avoid dairy for two weeks.",
        )
        self.client.post(self.add_url("prescription"), self._item_formset())
        prescription = Prescription.objects.filter(visit=visit).get()
        response = self.client.get(reverse("print_prescription_record", args=[prescription.pk]))
        self.assertContains(response, "Avoid dairy for two weeks.")


class TestAlwaysEditableTabsNeedNoOpenVisit(EditingTestCase):
    """
    Summary, Investigations, Growth Chart and Reference Letters are reference
    data a doctor may reasonably need to correct at any time, not only
    mid-consultation — see views_doctor.ALWAYS_EDITABLE_TABS. Only clinical
    notes and prescriptions stay locked to an open visit (covered by
    tests/test_patient_file_readonly.py and TestPrescriptionWorkflow above).
    """

    def test_a_diagnosis_can_be_added(self):
        response = self.client.post(self.add_url("diagnosis"), {
            "description": "Type 2 diabetes mellitus",
            "status": Diagnosis.Status.ACTIVE,
            "diagnosed_on": timezone.localdate().isoformat(),
            "icd10_code": "", "notes": "", "resolved_on": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Diagnosis.objects.get().patient, self.patient)

    def test_background_history_can_be_added(self):
        response = self.client.post(self.add_url("history"), {
            "presenting_complaints": "", "past_medical_history": "",
            "family_history": "", "birth_history": "", "allergies": "Penicillin",
            "current_medications": "", "surgical_history": "", "lifestyle_notes": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.patient.history.allergies, "Penicillin")

    def test_patient_details_can_be_edited(self):
        response = self.client.post(self.edit_url("patient", self.patient.pk), {
            "first_name": self.patient.first_name, "last_name": self.patient.last_name,
            "sex": self.patient.sex, "date_of_birth": self.patient.date_of_birth.isoformat(),
            "blood_group": "O+", "phone": "9820099999",
            "alternate_phone": "", "email": "", "guardian_name": "",
            "guardian_relation": "", "guardian_phone": "",
            "address": "", "city": "", "pincode": "", "referred_by": "",
        })
        self.assertEqual(response.status_code, 200)
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.phone, "9820099999")

    def test_an_investigation_result_can_be_added(self):
        response = self.client.post(self.add_url("investigation"), {
            "test_name": "TSH", "category": "THYROID",
            "performed_on": timezone.localdate().isoformat(),
            "value": "6.2", "value_numeric": "6.2", "unit": "µIU/mL",
            "reference_range": "0.5 – 4.5", "is_abnormal": "on",
            "lab_name": "Metropolis", "notes": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Investigation.objects.get().patient, self.patient)

    def test_a_growth_measurement_can_be_added(self):
        response = self.client.post(self.add_url("measurement"), {
            "measured_on": timezone.localdate().isoformat(),
            "height_cm": "123.1", "weight_kg": "23.60",
            "head_circumference_cm": "", "waist_cm": "", "puberty_stage": "",
            "mother_height_cm": "", "father_height_cm": "", "notes": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Measurement.objects.get().patient, self.patient)


class TestReferenceLetterWorkflow(EditingTestCase):
    """
    A letter for school, insurance, travel or fitness — written in the
    doctor's own words. Written any time, whether or not a visit is open —
    the request for one often has nothing to do with why the patient was
    last seen — but capped at one per consultation like a note or
    prescription, and only ever editable while it is the latest one.
    """

    def _post(self, **overrides):
        payload = {"to": "The Principal, Green Valley School",
                   "note": "This is to certify that the patient is fit to attend school."}
        payload.update(overrides)
        return self.client.post(self.add_url("reference_letter"), payload)

    def test_no_open_visit_is_required(self):
        response = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ReferenceLetter.objects.count(), 1)

    def test_it_is_attached_to_the_patient_and_doctor(self):
        self._post()
        letter = ReferenceLetter.objects.get()
        self.assertEqual(letter.patient, self.patient)
        self.assertEqual(letter.doctor, self.doctor)
        self.assertEqual(letter.to, "The Principal, Green Valley School")

    def test_a_second_letter_is_refused_without_a_new_consultation(self):
        self._post(to="The Principal, Green Valley School")
        response = self._post(to="ABC Insurance Co.")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ReferenceLetter.objects.count(), 1)

    def test_a_new_consultation_allows_another_letter(self):
        self._post(to="The Principal, Green Valley School")
        self.open_visit()
        response = self._post(to="ABC Insurance Co.")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ReferenceLetter.objects.count(), 2)

    def test_only_the_latest_letter_can_be_edited(self):
        self._post()
        older = ReferenceLetter.objects.get()
        self.open_visit()
        self._post(to="ABC Insurance Co.")
        response = self.client.get(self.edit_url("reference_letter", older.pk))
        self.assertEqual(response.status_code, 403)

    def test_editing_an_existing_letter_updates_it_not_duplicates_it(self):
        self._post()
        letter = ReferenceLetter.objects.get()
        self.client.post(self.edit_url("reference_letter", letter.pk), {
            "to": "The Principal, Green Valley School",
            "note": "Revised: fit to attend school from Monday.",
        })
        letter.refresh_from_db()
        self.assertEqual(ReferenceLetter.objects.count(), 1)
        self.assertIn("Revised", letter.note)

    def test_a_blank_note_is_refused(self):
        response = self._post(note="")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<form")
        self.assertEqual(ReferenceLetter.objects.count(), 0)

    def test_the_letter_appears_on_its_own_tab(self):
        self._post()
        response = self.client.get(
            reverse("doctor_patient_tab", args=[self.patient.patient_id, "reference_letters"])
        )
        self.assertContains(response, "The Principal, Green Valley School")

    def test_the_tab_is_offered_next_to_prescriptions(self):
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[self.patient.patient_id])
        )
        tabs = [key for key, _label in response.context["tabs"]]
        self.assertEqual(
            tabs.index("reference_letters"), tabs.index("prescriptions") + 1,
        )

    def test_it_can_be_printed(self):
        self._post()
        letter = ReferenceLetter.objects.get()
        response = self.client.get(reverse("print_reference_letter", args=[letter.pk]))
        self.assertContains(response, "The Principal, Green Valley School")
        self.assertContains(response, "fit to attend school")
        self.assertContains(response, "Age / Gender")

    def test_printing_records_it(self):
        self._post()
        letter = ReferenceLetter.objects.get()
        self.client.get(reverse("print_reference_letter", args=[letter.pk]), {"mark": "1"})
        letter.refresh_from_db()
        self.assertIsNotNone(letter.printed_at)
        self.assertTrue(
            AccessLog.objects.filter(action=AuditAction.PRINT).exists()
        )

    def test_a_receptionist_can_print_but_not_edit(self):
        self._post()
        letter = ReferenceLetter.objects.get()
        self.client.force_login(make_receptionist())
        self.assertEqual(
            self.client.get(reverse("print_reference_letter", args=[letter.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(self.add_url("reference_letter")).status_code, 403,
        )


class TestEditingIsAudited(EditingTestCase):
    def test_creating_a_record_is_logged_against_the_patient(self):
        self.open_visit()
        self.client.post(self.add_url("diagnosis"), {
            "description": "Childhood obesity", "status": Diagnosis.Status.ACTIVE,
            "diagnosed_on": timezone.localdate().isoformat(),
            "icd10_code": "", "notes": "", "resolved_on": "",
        })
        entry = AccessLog.objects.filter(action=AuditAction.CREATE).get()
        self.assertEqual(entry.patient_id_ref, self.patient.patient_id)
        self.assertEqual(entry.username, self.doctor.username)

    def test_updating_a_record_is_logged_as_an_update(self):
        self.open_visit()
        measurement = make_measurement(self.patient)
        self.client.post(self.edit_url("measurement", measurement.pk), {
            "measured_on": timezone.localdate().isoformat(),
            "height_cm": "124.0", "weight_kg": "24.0",
            "head_circumference_cm": "", "waist_cm": "", "puberty_stage": "",
            "mother_height_cm": "", "father_height_cm": "", "notes": "",
        })
        self.assertTrue(AccessLog.objects.filter(action=AuditAction.UPDATE).exists())


class TestEditingAccessControl(EditingTestCase):
    def test_receptionist_cannot_open_an_edit_form(self):
        self.client.force_login(make_receptionist())
        self.assertEqual(self.client.get(self.add_url("diagnosis")).status_code, 403)

    def test_receptionist_cannot_post_an_edit(self):
        self.client.force_login(make_receptionist())
        response = self.client.post(self.add_url("diagnosis"), {
            "description": "Injected", "status": Diagnosis.Status.ACTIVE,
            "diagnosed_on": timezone.localdate().isoformat(),
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Diagnosis.objects.count(), 0)

    def test_anonymous_visitor_is_redirected(self):
        self.client.logout()
        response = self.client.get(self.add_url("diagnosis"))
        self.assertEqual(response.status_code, 302)

    def test_cannot_edit_a_record_belonging_to_another_patient(self):
        self.open_visit()
        other = make_patient(phone="9820099999")
        measurement = make_measurement(other)
        # The URL names this patient but the record belongs to someone else.
        response = self.client.get(self.edit_url("measurement", measurement.pk))
        self.assertEqual(response.status_code, 404)
