"""
The requirements backlog, as data.

Single source of truth for both docs/REQUIREMENTS.md and the review artifact, so
the two can never drift apart.

Every story records the tests that actually prove it. Where a story has no
automated cover, it says so plainly — that gap is the most useful thing this
document reports.
"""

DONE = "done"
PARTIAL = "partial"
BLOCKED = "blocked"
BACKLOG = "backlog"
WITHDRAWN = "withdrawn"

STATUS_LABEL = {
    DONE: "Done",
    PARTIAL: "Partial",
    BLOCKED: "Blocked",
    BACKLOG: "Backlog",
    WITHDRAWN: "Withdrawn",
}

POINT_SCALE = [
    ("1", "Trivial — a field, a label, a one-line rule."),
    ("2", "Small — one form or view, no new concepts."),
    ("3", "Ordinary — a screen or a model with real behaviour."),
    ("5", "Substantial — several moving parts, or rules that must not be got wrong."),
    ("8", "Large — a workflow spanning roles, or logic with clinical consequences."),
    ("13", "Too large — split it before starting."),
]

EPICS = [
    dict(
        id="E1", name="Accounts & access control",
        goal="Everyone who touches a patient record is identified, limited to their role, "
             "and recorded.",
        stories=[
            dict(
                id="S-101", points=3, status=DONE,
                title="One sign-in page for every role",
                story="As any clinic user, I want a single place to sign in, so that I do not "
                      "have to know which part of the system I belong to.",
                criteria=[
                    "Username and password, with clear errors on failure.",
                    "Signing in while already signed in goes straight to my dashboard.",
                    "The page states that record access is logged.",
                ],
                tests=["TestLogin (5 tests)"],
            ),
            dict(
                id="S-102", points=2, status=DONE,
                title="Land on the dashboard for my role",
                story="As a doctor, receptionist or patient, I want to arrive at my own screen "
                      "after signing in, so that I never have to navigate to it.",
                criteria=[
                    "Doctor → today's clinic. Receptionist → queue board. Patient → appointments.",
                    "An administrator goes to the Django admin.",
                    "A signed-out visitor is sent to sign in.",
                ],
                tests=["TestRoleRouting (4 tests)"],
            ),
            dict(
                id="S-103", points=5, status=DONE,
                title="Roles are enforced by the server, not by hidden links",
                story="As the clinic owner, I want role limits enforced in code, so that typing "
                      "a URL cannot reveal something a user should not see.",
                criteria=[
                    "A receptionist opening a clinical chart gets 403, not a redirect.",
                    "A doctor cannot work the reception queue.",
                    "A patient cannot reach any chart or billing screen.",
                    "Restrictions hold on POST as well as GET.",
                ],
                tests=[
                    "TestDoctorAccessControl (5 tests)",
                    "TestEditingAccessControl (4 tests)",
                    "TestReceptionQueue.test_a_doctor_cannot_work_the_queue",
                    "TestBilling.test_a_patient_cannot_reach_billing",
                    "TestPatientPortal.test_a_patient_cannot_reach_a_clinical_chart",
                ],
            ),
            dict(
                id="S-104", points=5, status=DONE,
                title="Staff accounts managed from the admin",
                story="As the clinic owner, I want to create doctor, receptionist and patient "
                      "logins myself, so that I am not waiting on a developer to add staff.",
                criteria=[
                    "Username, password, email, contact number and role, as specified.",
                    "Doctors carry qualification, registration number and signature for prescriptions.",
                    "Email addresses are unique.",
                ],
                tests=[],
                gap="No automated test — the admin screens are exercised by hand. "
                    "Worth a smoke test before go-live.",
            ),
            dict(
                id="S-105", points=5, status=DONE,
                title="Every look at a record is recorded, permanently",
                story="As the clinic owner, I want to answer “who opened this patient's file”, "
                      "so that I can respond if a patient ever asks.",
                criteria=[
                    "Opening a chart, creating or editing a record, printing, and signing in or "
                    "out are all logged with user, time and IP.",
                    "A failed sign-in is logged without the attempted password.",
                    "Entries cannot be edited or deleted — including in bulk, and including from admin.",
                    "No patient identifiers appear in application logs.",
                ],
                tests=[
                    "TestAuditLogIsAppendOnly (2 tests)",
                    "TestAuditLogCannotBeBulkErased (2 tests)",
                    "TestEditingIsAudited (2 tests)",
                    "TestPatientDashboard.test_opening_a_chart_is_written_to_the_audit_trail",
                    "TestLogin.test_successful_login_is_recorded_in_the_audit_trail",
                    "TestLogin.test_failed_login_is_recorded_without_the_password",
                    "TestReceptionQueue.test_each_move_is_recorded_against_the_patient",
                ],
            ),
            dict(
                id="S-106", points=3, status=DONE,
                title="Sessions expire and cookies are secure",
                story="As the clinic owner, I want an unattended screen to lock itself, because "
                      "patient records are on display in a room people walk through.",
                criteria=[
                    "Thirty minutes of inactivity ends the session.",
                    "HTTPS enforced in production, with HSTS and secure, HTTP-only cookies.",
                    "The admin is mounted at a path set by environment variable; /admin/ is a decoy.",
                ],
                tests=[],
                gap="Configuration rather than behaviour. Verified by "
                    "`manage.py check --deploy`, which passes clean.",
            ),
        ],
    ),
    dict(
        id="E2", name="Patient records",
        goal="One unmistakable identity per patient, and the longitudinal history that hangs off it.",
        stories=[
            dict(
                id="S-201", points=5, status=DONE,
                title="Every patient gets a unique UHID",
                story="As a receptionist, I want each patient to have one permanent number, so "
                      "that their whole history can be found from the number on their file.",
                criteria=[
                    "Format CEMH-YY-NNNNN, issued automatically, never reused, never edited.",
                    "Two receptionists registering at the same instant cannot produce a duplicate.",
                    "The prefix is configurable per clinic.",
                ],
                tests=["TestPatientIdentifier (4 tests)", "TestSaving.test_uhid_is_not_editable"],
            ),
            dict(
                id="S-202", points=3, status=DONE,
                title="Patient demographics, including guardians",
                story="As a receptionist, I want a child's guardian recorded, so that I ring the "
                      "right person when confirming an appointment.",
                criteria=[
                    "Name, date of birth, sex, blood group, contact, address.",
                    "Guardian name, relationship and phone for paediatric patients.",
                    "Age shows in days, months or years as appropriate to how young they are.",
                    "The contact number offered is the guardian's for a child, the patient's for an adult.",
                ],
                tests=["TestPatientAge (5 tests)", "TestSaving.test_editing_patient_details_persists"],
            ),
            dict(
                id="S-203", points=3, status=DONE,
                title="Standing background history",
                story="As a doctor, I want past history, family history and allergies on screen "
                      "at every visit, so that I am not re-reading old notes to find them.",
                criteria=[
                    "Presenting complaints, past medical, family, birth and development, "
                    "allergies, medications, surgical, lifestyle.",
                    "One record per patient — editing never creates a duplicate.",
                    "Allergies are shown prominently on the sidebar and on printed prescriptions.",
                ],
                tests=[
                    "TestSaving.test_editing_history_updates_the_existing_record",
                    "TestPatientDashboard.test_allergies_are_surfaced_on_the_sidebar",
                ],
            ),
            dict(
                id="S-204", points=3, status=DONE,
                title="Register a new patient at the desk",
                story="As a receptionist, I want to register a caller without leaving the booking "
                      "screen, because having to go elsewhere first is what stalls a phone booking.",
                criteria=[
                    "Registration is reachable from the booking screen and returns to it.",
                    "A UHID is issued on save.",
                ],
                tests=["TestReceptionBooking.test_registering_a_patient_issues_a_uhid"],
            ),
            dict(
                id="S-205", points=2, status=DONE,
                title="Find a patient by name, UHID or mobile",
                story="As clinic staff, I want to find a patient by whatever the caller gives me, "
                      "so that I am not asking them to read out a number they may not have.",
                criteria=[
                    "Search matches UHID, first or last name, patient mobile or guardian mobile.",
                    "The doctor can type a UHID or mobile to open a chart directly.",
                    "UHID matching ignores case.",
                    "An unknown value reports plainly rather than failing.",
                ],
                tests=[
                    "TestReceptionBooking.test_patient_search_finds_by_uhid_and_name",
                    "TestDoctorHome (4 lookup tests)",
                ],
            ),
        ],
    ),
    dict(
        id="E3", name="Appointments & the clinic day",
        goal="The diary reflects reality, and a patient can only move forward through the day.",
        stories=[
            dict(
                id="S-301", points=8, status=DONE,
                title="A visit moves through a fixed sequence",
                story="As the clinic owner, I want the software to mirror how the clinic actually "
                      "runs, so that the queue cannot get into a state that never happens in real life.",
                criteria=[
                    "Booked → Confirmed → Arrived → In cabin → Consulted → Billed → Completed, "
                    "with Cancelled and No-show as exits.",
                    "An out-of-order move is refused, not silently accepted.",
                    "Arrival, cabin, consultation and completion times are stamped as they happen.",
                    "Every change records who made it.",
                ],
                tests=["TestVisitLifecycle (9 tests)", "TestReceptionQueue.test_an_out_of_order_move_is_refused"],
            ),
            dict(
                id="S-302", points=5, status=DONE,
                title="Two patients can never hold one doctor at once",
                story="As a receptionist, I want double booking to be impossible, so that I never "
                      "have to apologise to a patient for it.",
                criteria=[
                    "Enforced by the database, so it holds under simultaneous requests.",
                    "Back-to-back appointments are fine; overlapping ones are not.",
                    "Cancelling frees the slot again.",
                    "Two different doctors may of course be busy at the same time.",
                ],
                tests=["TestNoDoubleBooking (4 tests)"],
            ),
            dict(
                id="S-303", points=5, status=DONE,
                title="Free slots are offered from consulting hours",
                story="As a receptionist, I want to see only times that are genuinely free, so "
                      "that I can answer a caller straight away.",
                criteria=[
                    "Hours, slot length, working days and booking horizon are configuration.",
                    "Booked slots disappear; cancelled ones come back.",
                    "Closed days offer nothing and say why.",
                    "Patients are never offered a time that has already passed; reception may be.",
                ],
                tests=["TestScheduling (5 tests)"],
            ),
            dict(
                id="S-304", points=5, status=DONE,
                title="Take a booking at the desk or over the phone",
                story="As a receptionist, I want to book a patient in a few clicks, so that I am "
                      "not keeping a caller waiting.",
                criteria=[
                    "Search or register the patient, pick doctor and date, pick from free times.",
                    "Every booking starts unconfirmed — the receptionist still has to ring "
                    "the patient on the day, and confirming is that call.",
                    "If someone takes the slot first, the error is readable, not a crash.",
                    "A slot that is not on the chosen date is rejected.",
                ],
                tests=["TestReceptionBooking (6 tests)"],
            ),
            dict(
                id="S-305", points=8, status=DONE,
                title="Run the day from a queue board",
                story="As a receptionist, I want one screen showing where every patient is, so "
                      "that I can call them in and send them through without a paper list.",
                criteria=[
                    "A column per stage, with counts.",
                    "\"To confirm\" is separate from \"Confirmed\", so the receptionist can "
                    "see exactly who she still has to telephone.",
                    "An unconfirmed card leads with the number to ring, as a tap-to-dial link, "
                    "and names the guardian to ask for when the patient is a child.",
                    "Each card offers only the moves legal from that patient's current state.",
                    "Waiting time counts up and is highlighted past thirty minutes.",
                    "The board refreshes itself, because a stale board sends the wrong patient in.",
                    "Any day can be reviewed, not just today.",
                ],
                tests=["TestReceptionQueue (5 tests)",
                       "TestConfirmationCallIsVisibleWork (3 tests)"],
            ),
        ],
    ),
    dict(
        id="E4", name="The doctor's chart",
        goal="Everything known about a patient, readable at a glance and editable in place.",
        stories=[
            dict(
                id="S-401", points=3, status=DONE,
                title="Open a chart by typing the UHID",
                story="As a doctor, I want to type the number from the patient's file, so that I "
                      "reach their record without hunting through a list.",
                criteria=[
                    "UHID or mobile number both work.",
                    "Today's queue is listed below, with whoever is in the cabin first.",
                    "Another doctor's patients do not appear in my queue.",
                ],
                tests=["TestDoctorHome (6 tests)"],
            ),
            dict(
                id="S-402", points=3, status=DONE,
                title="A patient panel that stays on screen",
                story="As a doctor, I want identity, allergies, active problems and the latest "
                      "measurements always visible, so that they are never a tab away.",
                criteria=[
                    "Allergies are prominent, and say so explicitly when none are recorded.",
                    "Active problems, latest measurements, last vitals, attendance summary.",
                    "The panel refreshes when any of it is edited, from whichever tab.",
                ],
                tests=["TestPatientDashboard (6 tests)"],
            ),
            dict(
                id="S-403", points=5, status=DONE,
                title="Five tabs, loaded without losing my place",
                story="As a doctor, I want to move between summary, notes, results, growth and "
                      "prescriptions quickly, so that consultation time is not spent waiting.",
                criteria=[
                    "Summary, Clinical Notes, Investigations, Growth Chart, Prescriptions.",
                    "Tabs swap without a full page reload.",
                    "Visits are grouped into the time bands clinicians read charts in.",
                    "Investigations are grouped by test, so a value can be followed over years.",
                ],
                tests=["TestDashboardTabs (6 tests)"],
            ),
            dict(
                id="S-404", points=8, status=DONE,
                title="Edit every element from the chart",
                story="As a doctor, I want to correct or add to any part of the record where I am "
                      "reading it, so that I never go looking for a separate editing screen.",
                criteria=[
                    "Patient details, history, problems, investigations, notes, measurements, "
                    "prescriptions and their drug lines.",
                    "Saving refreshes the chart and panel in place.",
                    "An invalid form redisplays with errors rather than losing the entry.",
                    "A record cannot be edited through another patient's URL.",
                    "The UHID is never editable.",
                ],
                tests=["TestFormsOpen (4 tests)", "TestSaving (8 tests)"],
            ),
            dict(
                id="S-405", points=2, status=DONE,
                title="The chart knows which visit is in progress",
                story="As a doctor, I want the consultation I am actually in to drive the screen, "
                      "so that a follow-up already in the diary does not hide it.",
                criteria=[
                    "The patient in the cabin takes precedence over any future booking.",
                    "The Complete consultation action stays available.",
                ],
                tests=["TestChartShowsTheVisitInProgress (2 tests)"],
                note="Added after this bug was found by driving the workflow in a browser.",
            ),
        ],
    ),
    dict(
        id="E5", name="Growth & anthropometry",
        goal="Make a child's growth trajectory legible at a glance. Removable for clinics that "
             "do not need it.",
        stories=[
            dict(
                id="S-501", points=3, status=DONE,
                title="Record measurements at a visit",
                story="As a paediatric endocrinologist, I want height, weight and puberty staging "
                      "recorded against the date they were taken, so that trends are truthful.",
                criteria=[
                    "Height, weight, head circumference, waist, Tanner stage, parental heights.",
                    "BMI is computed, never typed, so it cannot contradict height and weight.",
                    "Age is taken at the measurement date, not today.",
                ],
                tests=["TestMeasurement (5 tests)", "TestSaving.test_adding_a_measurement_persists_it"],
            ),
            dict(
                id="S-502", points=8, status=DONE,
                title="Percentiles from published growth references",
                story="As a paediatric endocrinologist, I want a measurement placed on a centile, "
                      "so that short stature is visible rather than inferred.",
                criteria=[
                    "LMS method against published reference tables.",
                    "Verified against the SD columns published alongside those tables.",
                    "Returns nothing rather than guessing when no reference covers the patient.",
                    "Sex-specific; declines to chart when sex is recorded as neither.",
                    "The standard in use is chosen by GROWTH_REFERENCE, and a standard whose "
                    "tables are absent falls back to another — visibly, never silently.",
                ],
                tests=[
                    "TestAgainstPublishedValues (2 tests)",
                    "TestZScoreAndPercentile (5 tests)",
                    "TestAssess (7 tests)",
                    "TestStandardSelection (8 tests)",
                ],
            ),
            dict(
                id="S-503", points=5, status=DONE,
                title="Plot the child against the centile curves",
                story="As a paediatric endocrinologist, I want the child's line drawn over the "
                      "reference family, so that crossing centiles is obvious.",
                criteria=[
                    "Height, weight and BMI for age; head circumference under three.",
                    "3rd to 97th centile curves behind the patient's own line.",
                    "The centile is stated in words as well as drawn.",
                    "Curves stop where the published data stops — never extrapolated.",
                ],
                tests=[
                    "TestReferenceCurves (3 tests)",
                    "TestDashboardTabs.test_growth_tab_plots_a_recorded_measurement",
                    "TestDashboardTabs.test_growth_tab_without_measurements_shows_an_empty_state",
                ],
            ),
            dict(
                id="S-504", points=2, status=DONE,
                title="Mid-parental target height",
                story="As a paediatric endocrinologist, I want the target height from the parents' "
                      "heights, so that I can judge whether the child is on their own track.",
                criteria=["Boys (father + mother + 13) / 2; girls (father + mother − 13) / 2.",
                          "Shown only when both parental heights are known."],
                tests=["TestMeasurement.test_mid_parental_height_adds_thirteen_for_a_boy",
                       "TestMeasurement.test_mid_parental_height_subtracts_thirteen_for_a_girl"],
            ),
            dict(
                id="S-506", points=3, status=DONE,
                title="Import supplied growth tables safely",
                story="As the clinic owner, I want new reference tables installed without a "
                      "developer hand-editing clinical data.",
                criteria=[
                    "The command reads the IAP 2015 paper's own PDF, so no value is "
                    "transcribed by hand; a single table may also come from a CSV.",
                    "It refuses anything it cannot vouch for, naming the offending rows: "
                    "values must rise across each row and smoothly with age.",
                    "For BMI it also checks P50 < 23-Eq < 27-Eq throughout, and that the two "
                    "cut-off lines have converged on the adult 23 and 27 by eighteen years.",
                    "The tables installed in the repository are re-validated by the test suite, "
                    "so a hand edit to clinical data fails the build.",
                ],
                tests=[
                    "TestASoundTableIsAccepted, TestTransposedColumnsAreRefused (3 tests)",
                    "TestADroppedDigitIsRefused (2 tests)",
                    "TestTheShapeOfTheTableIsChecked (2 tests)",
                    "TestTheBmiCutoffCheck (3 tests)",
                    "TestTheInstalledTablesStillPassTheirOwnChecks",
                ],
            ),
            dict(
                id="S-505", points=5, status=DONE,
                title="Chart against IAP 2015 Indian references",
                story="As a paediatric endocrinologist, I want Indian children charted against "
                      "Indian references, so that centiles reflect the population I treat.",
                criteria=[
                    "WHO below five years, IAP 2015 for 5–18 — IAP's own recommendation, and "
                    "the standard the clinic has chosen.",
                    "All six tables installed from the paper: height, weight and BMI, both "
                    "sexes, 5.0–18.0 years at half-year steps.",
                    "A child of exactly 5.0 years is charted against IAP, where those charts "
                    "begin; above 18 the chart falls back to CDC and says so.",
                    "Every chart names the reference that actually produced it.",
                ],
                tests=["TestTablesAreInstalled (2 tests)",
                       "TestAgainstThePublishedCentiles (4 tests)",
                       "TestCurvesComeFromThePaper (4 tests)",
                       "TestStandardSelection.test_the_five_year_boundary_belongs_to_iap"],
                note="IAP Growth Chart Committee, Indian Pediatrics 2015;52:47–55, Tables II–VII.",
            ),
            dict(
                id="S-507", points=5, status=DONE,
                title="Say which kind of number the reference can give",
                story="As a paediatric endocrinologist, I want to know whether a centile was "
                      "computed or read off a printed curve, so that I know how much to trust "
                      "the decimal places.",
                criteria=[
                    "WHO and CDC publish LMS parameters, so an exact centile and z-score are "
                    "shown, as before.",
                    "IAP publishes the curves only, so the child is placed in the band between "
                    "two printed centiles, with an SDS interpolated between them.",
                    "No LMS is back-fitted from the seven printed points — that would report a "
                    "z-score reading as exact while carrying invisible fitting error.",
                    "The paper's SD column is stored but never scored against: it is the sample "
                    "SD, and using it would put a child on the printed 97th centile at +2.2 to "
                    "+3.4 SDS instead of +1.88.",
                    "Asking a centile reference for an exact z-score raises rather than "
                    "returning an approximation.",
                ],
                tests=["TestNoExactZScoreIsInvented (4 tests)",
                       "TestGrowthTabShowsWhichReferenceAnswered (5 tests)"],
            ),
            dict(
                id="S-508", points=3, status=DONE,
                title="Refuse to guess below the 3rd centile",
                story="As a paediatric endocrinologist, I want no invented number for a child "
                      "off the printed scale, but I still want an SDS I can act on.",
                criteria=[
                    "Outside the outermost printed curve the band is still stated — 'below the "
                    "3rd centile' — but no centile or SDS is interpolated.",
                    "A z-score from an LMS reference is supplied alongside and labelled with "
                    "the source it came from, so growth-hormone decisions still have a figure.",
                    "A child on the scale gets no companion figure, so the fallback appears "
                    "only where it is needed.",
                ],
                tests=["TestOffTheScale (5 tests)",
                       "TestGrowthTabShowsWhichReferenceAnswered."
                       "test_a_child_below_the_third_centile_gets_a_labelled_companion"],
                note="WHO's 2007 5–19y reference would be a better companion than CDC for "
                     "Indian children; it publishes LMS and is a drop-in when sourced.",
            ),
            dict(
                id="S-509", points=3, status=DONE,
                title="Overweight and obesity from the adult-equivalent cut-offs",
                story="As a paediatric endocrinologist, I want overweight and obesity judged by "
                      "the Asian cut-offs, because Indian children carry more risk at a lower BMI.",
                criteria=[
                    "Four bands: thinness below the 3rd centile, then normal, overweight at the "
                    "23-equivalent line and obesity at the 27-equivalent line.",
                    "The cut-offs are age- and sex-specific — 15.7 kg/m² at five years, 23.2 at "
                    "eighteen — never the adult 25 and 30.",
                    "The 23-Eq and 27-Eq columns are excluded from centile placement, because "
                    "they are cut-offs and not percentiles of anything.",
                    "Both lines are drawn on the BMI chart, labelled, in warning colours.",
                    "A clinic charting against WHO or CDC gets no verdict at all rather than "
                    "one derived from the wrong lines.",
                ],
                tests=["TestTheFourBands (7 tests)",
                       "TestTheCutoffsAreNotTheAdultOnes (4 tests)",
                       "TestNoVerdictWhenTheCutoffsAreUnavailable (6 tests)",
                       "TestTheBmiEqColumnsAreNotCentiles (3 tests)"],
            ),
        ],
    ),
    dict(
        id="E6", name="Prescriptions",
        goal="The doctor's instructions reach the patient on paper, and reach reception at the "
             "right moment.",
        stories=[
            dict(
                id="S-601", points=5, status=DONE,
                title="Compose a prescription with medication lines",
                story="As a doctor, I want to write drug, strength, dose, frequency and duration, "
                      "so that the patient leaves with unambiguous instructions.",
                criteria=[
                    "Multiple medication lines, plus advice, investigations advised and follow-up date.",
                    "Existing lines can be removed.",
                    "A new prescription is a draft — saving never issues it.",
                ],
                tests=["TestPrescriptionWorkflow.test_new_prescription_starts_as_a_draft"],
            ),
            dict(
                id="S-602", points=3, status=DONE,
                title="Issuing is a deliberate handover",
                story="As a doctor, I want to decide when a prescription is final, so that a "
                      "half-written one never reaches the front desk.",
                criteria=[
                    "Reception can only print an issued prescription.",
                    "The draft/issued state is visible on the chart.",
                ],
                tests=["TestPrescriptionWorkflow.test_sending_to_reception_marks_it_generated"],
            ),
            dict(
                id="S-603", points=5, status=DONE,
                title="One action ends the consultation",
                story="As a doctor, I want the fee and the prescription to go to reception "
                      "together, so that the receptionist is told what to collect rather than asking.",
                criteria=[
                    "Records the fee, issues the prescription and moves the visit, in one step.",
                    "Refused when the patient is not in the cabin.",
                    "Warns when no medication has been added, but permits advice-only.",
                    "A receptionist cannot set the fee.",
                ],
                tests=["TestConsultationHandover (4 tests)"],
            ),
        ],
    ),
    dict(
        id="E7", name="Billing & receipts",
        goal="Money is collected against what the doctor charged, and evidenced.",
        stories=[
            dict(
                id="S-701", points=3, status=DONE,
                title="See what is owed before taking payment",
                story="As a receptionist, I want the charge already on screen, so that I do not "
                      "have to interrupt the doctor to ask.",
                criteria=["Consultation fee, procedure charges and discount, with a total.",
                          "The patient, doctor and visit date are shown alongside."],
                tests=["TestBilling.test_billing_page_shows_what_is_owed",
                       "TestConsultationHandover.test_a_discount_reduces_what_reception_collects"],
            ),
            dict(
                id="S-702", points=5, status=DONE,
                title="Record payment and issue a receipt",
                story="As a receptionist, I want a numbered receipt produced automatically, so "
                      "that the patient always leaves with proof of payment.",
                criteria=["Cash, UPI, card or bank transfer, with a reference field.",
                          "A receipt is created on payment and records who took the money.",
                          "Payment in full settles the visit."],
                tests=["TestBilling.test_payment_in_full_issues_a_receipt_and_settles_the_visit"],
            ),
            dict(
                id="S-703", points=3, status=DONE,
                title="Part payments stay outstanding",
                story="As a receptionist, I want a partly-paid visit to stay on my list, so that "
                      "an unpaid balance is not quietly forgotten.",
                criteria=["The balance is shown.",
                          "The visit is not settled until nothing is outstanding."],
                tests=["TestBilling.test_a_part_payment_leaves_the_visit_unsettled"],
            ),
            dict(
                id="S-704", points=2, status=DONE,
                title="Receipt numbers never collide",
                story="As the clinic owner, I want receipt numbers to be unique, because they are "
                      "financial records.",
                criteria=["Format R-YY-NNNNN from a database sequence.",
                          "Never reused, never edited."],
                tests=["TestBilling.test_receipt_numbers_do_not_collide"],
            ),
            dict(
                id="S-705", points=2, status=DONE,
                title="Check the patient out",
                story="As a receptionist, I want to close the visit when the patient leaves, so "
                      "that the board reflects who is still in the building.",
                criteria=["Available once the visit is settled."],
                tests=["TestBilling.test_checkout_completes_the_visit"],
            ),
        ],
    ),
    dict(
        id="E8", name="Printing",
        goal="Paper that looks like it came from this clinic.",
        stories=[
            dict(
                id="S-801", points=3, status=DONE,
                title="Print the prescription on letterhead",
                story="As a receptionist, I want to hand the patient a proper prescription, so "
                      "that it is accepted at any pharmacy.",
                criteria=[
                    "Clinic name, address and telephone; doctor's name, qualification and "
                    "registration number.",
                    "Allergies printed prominently when recorded.",
                    "A scanned signature appears when uploaded.",
                    "Laid out for A4; printing is recorded.",
                ],
                tests=["TestBilling.test_printing_a_prescription_records_it"],
            ),
            dict(
                id="S-802", points=2, status=DONE,
                title="Print the receipt",
                story="As a receptionist, I want a printable receipt, so that the patient can "
                      "claim reimbursement.",
                criteria=["Receipt number, date, itemised charges, amount received, method.",
                          "Balance shown if any remains."],
                tests=["TestBilling.test_printing_a_receipt_shows_the_number"],
            ),
        ],
    ),
    dict(
        id="E9", name="The public page",
        goal="One page that tells people what the clinic does and gets them to telephone or "
             "send a WhatsApp message. Patients never sign in.",
        stories=[
            dict(
                id="S-901", points=5, status=DONE,
                title="A single public page for the clinic",
                story="As somebody looking for an endocrinologist, I want to see what this clinic "
                      "treats and who its doctors are, so that I know it is the right place to ring.",
                criteria=[
                    "Clinic name, address, consulting hours and both consultants with their "
                    "qualifications.",
                    "What the clinic treats, in adult and paediatric columns, matching the "
                    "printed brochure.",
                    "Readable on a phone, which is how most people will find it.",
                    "The only page in the system that search engines may index; everything "
                    "behind the login stays hidden from them.",
                    "No patient information appears anywhere on it.",
                ],
                tests=["TestPublicPage (9 tests)",
                       "TestPublicPageLeaksNothing (2 tests)",
                       "TestEverythingElseStillNeedsALogin (2 tests)"],
            ),
            dict(
                id="S-902", points=3, status=DONE,
                title="Call and WhatsApp buttons that actually book",
                story="As somebody wanting an appointment, I want to reach the clinic in one tap, "
                      "so that I do not have to fill in a form and wait.",
                criteria=[
                    "Tap-to-dial and WhatsApp buttons, at the top of the page and again at the end.",
                    "The WhatsApp link carries the country code and a message already typed.",
                    "The page says plainly that bookings are by telephone, not online.",
                    "Which number receives them is configurable per clinic.",
                ],
                tests=["TestPublicPage.test_offers_a_telephone_link",
                       "TestPublicPage.test_offers_a_whatsapp_link_in_international_format"],
            ),
            dict(
                id="S-903", points=0, status=WITHDRAWN,
                title="Patient portal — logins, appointment list, online booking",
                story="As a patient, I want to see my appointments and book online.",
                criteria=[
                    "Built and working, then removed at the clinic's request.",
                    "Reception takes every booking; patients call or send a WhatsApp message.",
                ],
                tests=["TestNoPatientPortal (2 tests) — asserts the routes are gone"],
                note="Withdrawn, not forgotten. Recorded here so the decision is visible: the "
                     "clinic decided patients should not book online, so a working portal was "
                     "deleted rather than left behind an unused login.",
            ),
        ],
    ),
    dict(
        id="E10", name="Platform, reuse & deployment",
        goal="Runs reliably for this clinic, and can be re-skinned for the next one without a fork.",
        stories=[
            dict(
                id="S-1001", points=5, status=DONE,
                title="12-factor project on PostgreSQL, in Docker",
                story="As a developer, I want configuration in the environment, so that the same "
                      "image runs any clinic.",
                criteria=["Separate dev, production and test settings.",
                          "Database, secrets, hosts and branding from environment variables.",
                          "docker compose up gives a working stack from nothing.",
                          "Production refuses to start with a default secret key or empty hosts."],
                tests=[],
                gap="No automated test. Verified by rebuilding the database from empty and "
                    "running `check --deploy`, which passes.",
            ),
            dict(
                id="S-1002", points=3, status=DONE,
                title="Realistic demo data on demand",
                story="As a developer or reviewer, I want lifelike data in one command, so that "
                      "screens can be judged against something resembling a real practice.",
                criteria=["Five patients with years of visits, results and measurements.",
                          "Re-runnable; refuses to run with DEBUG off unless forced.",
                          "Identifiers restart so demo output is reproducible."],
                tests=[],
                gap="No automated test — exercised manually on every rebuild.",
            ),
            dict(
                id="S-1003", points=3, status=DONE,
                title="Re-skin for another clinic without forking",
                story="As the clinic owner, I want a second clinic to differ only in branding and "
                      "features, so that core fixes still merge down.",
                criteria=["Colours in one CSS variables file.",
                          "Name, address, UHID prefix and hours in one settings file.",
                          "Logo and letterhead in one templates directory."],
                tests=[],
                gap="No automated test. Verified by editing theme.css and confirming the whole "
                    "interface re-skins.",
            ),
            dict(
                id="S-1004", points=5, status=DONE,
                title="Speciality features are removable modules",
                story="As the clinic owner, I want to switch off features another clinic does not "
                      "need, so that an orthopaedic practice never sees growth charts.",
                criteria=["Removing 'growth' from OPTIONAL_APPS removes its models, admin and tab together.",
                          "No core app imports from a speciality app.",
                          "The application still boots with it removed."],
                tests=[],
                gap="No automated test. Verified by hand. **Worth automating** — this is the "
                    "property the whole multi-clinic strategy rests on.",
            ),
            dict(
                id="S-1005", points=5, status=PARTIAL,
                title="Deploy to the clinic's own server",
                story="As the clinic owner, I want the system running on our own domain, so that "
                      "staff can start using it.",
                criteria=["DigitalOcean droplet in Bangalore with managed PostgreSQL.",
                          "Caddy for automatic HTTPS.",
                          "Automated backups with point-in-time recovery, and a tested restore.",
                          "Domain pointed and certificate issued."],
                tests=[],
                gap="Compose files, Caddyfile and production settings are written and checked. "
                    "Nothing has been deployed — no server, domain or database exists yet.",
            ),
        ],
    ),
]

BACKLOG_ITEMS = [
    dict(id="S-1102", points=8, title="Appointment reminders by SMS or WhatsApp",
         story="As a receptionist, I want patients reminded automatically, so that fewer fail to "
               "attend.",
         note="Needs a provider decision and a background job runner — neither exists yet."),
    dict(id="S-1103", points=8, title="Clinical form builder",
         story="As a doctor, I want to add my own questions to the consultation form without a "
               "developer.",
         note="The FormDefinition model and the JSON field it drives are built; there is no "
              "screen to edit definitions, so new fields still need a developer."),
    dict(id="S-1104", points=5, title="Daily collection and attendance report",
         story="As the clinic owner, I want a daily summary of patients seen and money taken.",
         note="All the underlying data is recorded; nothing reports on it yet."),
    dict(id="S-1105", points=5, title="Doctor availability and leave",
         story="As a receptionist, I want to block out leave and vary consulting hours per doctor.",
         note="Hours are currently one setting for the whole clinic."),
    dict(id="S-1106", points=3, title="Upload investigation reports from the chart",
         story="As a doctor, I want to attach the lab's PDF to a result.",
         note="The file field exists on the model and works in admin, but is not on the chart's "
              "add-result form."),
    dict(id="S-1108", points=2, title="Lock accounts after repeated failed sign-ins",
         story="As the clinic owner, I want brute-force attempts blocked.",
         note="Failed attempts are already logged. Recommended before go-live."),
    dict(id="S-1109", points=3, title="Backup and restore runbook",
         story="As the clinic owner, I want a tested procedure for restoring patient data.",
         note="Managed PostgreSQL provides the backups; the restore has never been rehearsed. "
              "This is the highest-risk gap once real patients are in the system."),
]

TESTING_NOTES = [
    ("Automated", "The suite runs in seconds against a real PostgreSQL database — `pytest` "
                  "from the project root. The figure above is counted from the suite itself "
                  "when this document is generated, not written down."),
    ("Browser-driven", "The full booking-to-receipt path, and the public page, have been driven "
                       "through a real browser. This found four bugs the unit tests had not — "
                       "including one where a booked follow-up hid the doctor's Complete "
                       "consultation action."),
    ("Not yet covered", "Django admin screens, the deployment configuration, the removable-module "
                        "guarantee, and the demo-data command. Each is noted against its story."),
    ("Clinical validation", "The growth-chart maths is checked against published reference tables, "
                            "but no clinician has yet reviewed the system against real cases."),
]

#: Questions only the clinic can answer. Kept here rather than in each builder,
#: so the markdown, PDF and Word versions cannot drift apart.
OPEN_DECISIONS = [
    ("Where is it hosted, and when do we go live?",
     "Recommended: DigitalOcean Bangalore with managed PostgreSQL, for India data residency "
     "and automated backups (S-1005)."),
    ("What else does the receptionist capture at check-in?",
     "The mechanism for clinic-specific fields exists, but no fields have been agreed (S-1103)."),
    ("Do patients get portal logins, and who issues them?",
     "Today they are created one at a time in the admin (S-1107)."),
    ("Should WHO's 2007 5–19 year reference be added alongside IAP?",
     "It publishes LMS, so it would give a continuous SDS for a child below the 3rd centile, "
     "where the IAP tables stop. CDC fills that role today and is labelled as doing so "
     "(S-508). A decision for Dr. Vrushali."),
]
