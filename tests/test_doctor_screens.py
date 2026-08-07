"""
The doctor's screens, after a round of use.

Five things the doctor asked for, each because the screen got in the way of
something they were actually trying to do.
"""

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from appointments.models import VisitStatus
from clinical.models import Diagnosis
from patients.models import Patient

from .factories import (
    later_today, make_adult_patient, make_doctor, make_patient,
    make_receptionist, make_visit,
)


def _born_years_and_months_ago(years, months):
    """A date of birth giving exactly this age today, safe near month ends."""
    today = date.today()
    month = today.month - months
    year = today.year - years
    while month <= 0:
        month += 12
        year -= 1
    day = min(today.day, 28)
    return date(year, month, day)


class TestAgeIsShownAtTheRightPrecision(TestCase):
    """
    Two months is a visible distance on a growth chart and changes what a
    centile means, so a child's age needs the months. For an adult the months
    are noise on every screen that carries an age.
    """

    def _patient(self, years, months, **kwargs):
        return make_patient(
            date_of_birth=_born_years_and_months_ago(years, months), **kwargs
        )

    def test_a_child_is_shown_in_years_and_months(self):
        self.assertEqual(self._patient(9, 2).age_display, "9 yrs 2 months")

    def test_one_month_is_not_pluralised(self):
        self.assertEqual(self._patient(9, 1).age_display, "9 yrs 1 month")

    def test_a_child_exactly_on_their_birthday_drops_the_months(self):
        self.assertEqual(self._patient(9, 0).age_display, "9 yrs")

    def test_an_adult_is_shown_in_whole_years_only(self):
        adult = self._patient(45, 7, phone="9820044444")
        self.assertEqual(adult.age_display, "45 yrs")

    def test_the_boundary_is_the_paediatric_age_limit(self):
        # 18 is an adult, 17 is not.
        self.assertEqual(self._patient(18, 5, phone="9820055555").age_display, "18 yrs")
        self.assertEqual(self._patient(17, 5, phone="9820066666").age_display,
                         "17 yrs 5 months")

    def test_an_infant_drops_the_years_entirely(self):
        # "0 yrs 3 months" reads worse than "3 months".
        self.assertEqual(self._patient(0, 3, phone="9820077777").age_display, "3 months")

    def test_a_newborn_is_shown_in_days(self):
        newborn = make_patient(
            date_of_birth=date.today() - timedelta(days=9), phone="9820088888"
        )
        self.assertEqual(newborn.age_display, "9 days")

    def test_the_chart_shows_the_months_for_a_child(self):
        child = self._patient(9, 2)
        self.client.force_login(make_doctor())
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[child.patient_id])
        )
        self.assertContains(response, "9 yrs 2 months")


class TestOpeningARecordBySearching(TestCase):
    def setUp(self):
        self.client.force_login(make_doctor())
        self.patient = make_patient(first_name="Aarav", last_name="Deshpande")

    def test_typing_part_of_a_name_suggests_the_patient(self):
        response = self.client.get(reverse("doctor_patient_search"), {"q": "Aar"})
        self.assertContains(response, "Aarav Deshpande")
        self.assertContains(response, self.patient.patient_id)

    def test_a_suggestion_links_straight_to_the_chart(self):
        response = self.client.get(reverse("doctor_patient_search"), {"q": "Aar"})
        self.assertContains(
            response, reverse("doctor_patient_dashboard", args=[self.patient.patient_id])
        )

    def test_searching_by_surname_works_too(self):
        response = self.client.get(reverse("doctor_patient_search"), {"q": "Deshp"})
        self.assertContains(response, self.patient.patient_id)

    def test_searching_by_uhid_still_works(self):
        response = self.client.get(
            reverse("doctor_patient_search"), {"q": self.patient.patient_id[:9]}
        )
        self.assertContains(response, self.patient.patient_id)

    def test_a_single_character_returns_nothing(self):
        # Two characters minimum, or the first keystroke lists the whole clinic.
        response = self.client.get(reverse("doctor_patient_search"), {"q": "A"})
        self.assertNotContains(response, self.patient.patient_id)

    def test_no_match_says_so(self):
        response = self.client.get(reverse("doctor_patient_search"), {"q": "Zzzzz"})
        self.assertContains(response, "No patient matches")

    def test_the_home_page_offers_the_type_ahead_box(self):
        response = self.client.get(reverse("doctor_home"))
        self.assertContains(response, reverse("doctor_patient_search"))

    def test_a_receptionist_cannot_use_the_doctors_search(self):
        self.client.force_login(make_receptionist())
        self.assertEqual(
            self.client.get(reverse("doctor_patient_search"), {"q": "Aar"}).status_code,
            403,
        )


class TestTheQueueRefreshesItself(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.client.force_login(self.doctor)

    def test_the_home_page_polls_the_queue(self):
        response = self.client.get(reverse("doctor_home"))
        self.assertContains(response, reverse("doctor_queue"))
        self.assertContains(response, "every 15s")

    def test_the_queue_fragment_is_a_partial_not_a_page(self):
        response = self.client.get(reverse("doctor_queue"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<html")

    def test_a_patient_reception_sends_through_appears_without_a_refresh(self):
        # The whole point: nothing about this request tells the page to reload.
        self.assertNotContains(self.client.get(reverse("doctor_queue")), "CEMH-")

        patient = make_patient()
        visit = make_visit(patient, self.doctor, start=later_today())
        visit.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
        visit.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)

        self.assertContains(self.client.get(reverse("doctor_queue")), patient.patient_id)

    def test_another_doctors_patient_is_not_polled_into_this_queue(self):
        other = make_doctor(username="dr2", email="dr2@example.in")
        patient = make_patient()
        visit = make_visit(patient, other, start=later_today())
        visit.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
        visit.transition_to(VisitStatus.ARRIVED, by_user=self.receptionist)

        self.assertNotContains(self.client.get(reverse("doctor_queue")), patient.patient_id)


class TestBookedNotConfirmedFromTodaysClinic(TestCase):
    """
    Booked and Confirmed share a column on the board, and the queue below only
    ever shows today, so a doctor had no way to see a booking on their own
    diary that reception never got round to confirming. This pop-up, off
    Today's clinic, is that list — scoped to this doctor's own patients only.
    """

    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.client.force_login(self.doctor)

    def test_the_link_is_on_the_home_page(self):
        response = self.client.get(reverse("doctor_home"))
        self.assertContains(response, reverse("doctor_unconfirmed_appointments"))

    def test_the_link_sits_beside_the_waiting_count(self):
        # Both badges live in the same queue-card header, next to each other.
        response = self.client.get(reverse("doctor_home"))
        body = response.content.decode()
        self.assertLess(
            body.index("waiting"),
            body.index(reverse("doctor_unconfirmed_appointments")),
        )

    def test_a_booked_visit_is_listed(self):
        patient = make_patient()
        visit = make_visit(patient, self.doctor, start=later_today())
        response = self.client.get(reverse("doctor_unconfirmed_appointments"))
        self.assertContains(response, patient.patient_id)
        self.assertEqual(list(response.context["visits"]), [visit])

    def test_a_confirmed_visit_is_not_listed(self):
        patient = make_patient()
        visit = make_visit(patient, self.doctor, start=later_today())
        visit.transition_to(VisitStatus.CONFIRMED, by_user=self.receptionist)
        response = self.client.get(reverse("doctor_unconfirmed_appointments"))
        self.assertNotContains(response, patient.patient_id)

    def test_another_doctors_unconfirmed_booking_does_not_show_here(self):
        other = make_doctor(username="dr2", email="dr2@example.in")
        patient = make_patient()
        make_visit(patient, other, start=later_today())

        response = self.client.get(reverse("doctor_unconfirmed_appointments"))
        self.assertNotContains(response, patient.patient_id)

    def test_nothing_booked_says_so(self):
        response = self.client.get(reverse("doctor_unconfirmed_appointments"))
        self.assertContains(response, "Nothing of yours is waiting on a confirmation call")

    def test_the_count_on_the_link_matches_this_doctors_own(self):
        other = make_doctor(username="dr3", email="dr3@example.in")
        make_visit(make_patient(), self.doctor, start=later_today())
        make_visit(make_patient(phone="9820011133"), other, start=later_today())

        response = self.client.get(reverse("doctor_home"))
        self.assertEqual(response.context["unconfirmed_count"], 1)
        self.assertContains(response, "1 unconfirmed")

    def test_the_polled_queue_fragment_carries_the_same_count(self):
        # _queue.html is swapped in wholesale every 15s, so the count has to be
        # refreshed by that same view or it goes stale the moment reception
        # confirms or adds a booking.
        make_visit(make_patient(), self.doctor, start=later_today())
        response = self.client.get(reverse("doctor_queue"))
        self.assertEqual(response.context["unconfirmed_count"], 1)
        self.assertContains(response, "1 unconfirmed")

    def test_a_receptionist_cannot_open_this_doctors_pop_up(self):
        self.client.force_login(self.receptionist)
        response = self.client.get(reverse("doctor_unconfirmed_appointments"))
        self.assertEqual(response.status_code, 403)


class TestVisitHistoryShowsOnlyWhatHappened(TestCase):
    def setUp(self):
        self.doctor = make_doctor()
        self.receptionist = make_receptionist()
        self.client.force_login(self.doctor)
        self.patient = make_patient()

    def _visit(self, status, days_ago=1):
        visit = make_visit(
            self.patient, self.doctor,
            start=timezone.now() - timedelta(days=days_ago),
        )
        path = [VisitStatus.CONFIRMED, VisitStatus.ARRIVED, VisitStatus.IN_CABIN,
                VisitStatus.CONSULTED, VisitStatus.BILLED, VisitStatus.COMPLETED]
        for step in path:
            if visit.status == status:
                break
            visit.transition_to(step, by_user=self.receptionist)
        return visit

    def _summary(self):
        return self.client.get(
            reverse("doctor_patient_tab", args=[self.patient.patient_id, "summary"])
        )

    def test_a_completed_visit_is_listed(self):
        self._visit(VisitStatus.COMPLETED, days_ago=3)
        self.assertEqual(self._summary().context["visit_count"], 1)

    def test_the_consultation_happening_now_is_listed(self):
        self._visit(VisitStatus.IN_CABIN, days_ago=0)
        self.assertEqual(self._summary().context["visit_count"], 1)

    def test_a_future_booking_is_not_history(self):
        make_visit(self.patient, self.doctor, start=later_today())
        self.assertEqual(self._summary().context["visit_count"], 0)

    def test_a_cancelled_visit_is_not_history(self):
        visit = self._visit(VisitStatus.CONFIRMED, days_ago=2)
        visit.transition_to(VisitStatus.CANCELLED, by_user=self.receptionist)
        self.assertEqual(self._summary().context["visit_count"], 0)

    def test_a_no_show_is_not_history(self):
        visit = self._visit(VisitStatus.CONFIRMED, days_ago=2)
        visit.transition_to(VisitStatus.NO_SHOW, by_user=self.receptionist)
        self.assertEqual(self._summary().context["visit_count"], 0)

    def test_a_visit_still_awaiting_payment_counts(self):
        # The consultation happened; the money is a separate matter.
        self._visit(VisitStatus.CONSULTED, days_ago=1)
        self.assertEqual(self._summary().context["visit_count"], 1)


class TestPastProblemsCanBeSeen(TestCase):
    def setUp(self):
        self.client.force_login(make_doctor())
        self.patient = make_patient()
        Diagnosis.objects.create(
            patient=self.patient, description="Vitamin D deficiency",
            status=Diagnosis.Status.RESOLVED, resolved_on=date(2024, 6, 1),
        )
        Diagnosis.objects.create(
            patient=self.patient, description="Type 1 diabetes",
            status=Diagnosis.Status.ACTIVE,
        )

    def _summary(self):
        return self.client.get(
            reverse("doctor_patient_tab", args=[self.patient.patient_id, "summary"])
        )

    def test_active_and_past_problems_are_counted_separately(self):
        context = self._summary().context
        self.assertEqual(len(context["active_diagnoses"]), 1)
        self.assertEqual(context["past_diagnosis_count"], 1)

    def test_a_resolved_problem_is_offered_on_the_summary(self):
        response = self._summary()
        self.assertContains(response, "Past problems (1)")
        self.assertContains(response, "Vitamin D deficiency")

    def test_a_resolved_problem_is_not_in_the_active_list(self):
        active = [d.description for d in self._summary().context["active_diagnoses"]]
        self.assertNotIn("Vitamin D deficiency", active)

    def test_the_sidebar_offers_them_too(self):
        response = self.client.get(
            reverse("doctor_patient_dashboard", args=[self.patient.patient_id])
        )
        self.assertContains(response, "Past problems (1)")

    def test_a_patient_with_no_past_problems_gets_no_section(self):
        clean = make_patient(first_name="Nobody", phone="9820012399")
        response = self.client.get(
            reverse("doctor_patient_tab", args=[clean.patient_id, "summary"])
        )
        self.assertNotContains(response, "Past problems")
