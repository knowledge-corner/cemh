# Requirements & delivery backlog

**Centre for Endocrine & Metabolic Health — patient management system**

Generated 31 July 2026 · repository `knowledge-corner/Cmeh`

This document is the agreed scope of the system, broken into stories, each sized and each
mapped to the automated tests that prove it works. It is meant to be used two ways: to agree
what is being built, and to see at a glance what is actually covered by tests and what is not.

Where a story has **no automated cover, it says so**. Those gaps are the most useful thing
here — they are where a defect would reach the clinic unnoticed.

## Where the project stands

| | Stories | Points |
|---|---:|---:|
| Delivered | 46 | **185** |
| Partially delivered | 1 | 5 |
| Blocked on a decision | 0 | 0 |
| Not started | 7 | 34 |
| **Total scoped** | **55** | **224** |

**212 automated tests** currently pass. 7 stories carry no automated cover;
each is flagged in place and listed again under *Testing* at the end.

## How to read this

**Story points** estimate relative effort and risk, not hours:

| Points | Meaning |
|---:|---|
| 1 | Trivial — a field, a label, a one-line rule. |
| 2 | Small — one form or view, no new concepts. |
| 3 | Ordinary — a screen or a model with real behaviour. |
| 5 | Substantial — several moving parts, or rules that must not be got wrong. |
| 8 | Large — a workflow spanning roles, or logic with clinical consequences. |
| 13 | Too large — split it before starting. |

**Status:**

- **Done** — built, and covered by tests unless noted otherwise.
- **Partial** — built but not finished; what remains is stated.
- **Blocked** — cannot proceed without a decision or information from the clinic.
- **Backlog** — agreed as wanted, not started.

---

## E1 · Accounts & access control

*Everyone who touches a patient record is identified, limited to their role, and recorded.*

**23 of 23 points delivered.**

### S-101 · One sign-in page for every role

`3 points` · **Done**

> As any clinic user, I want a single place to sign in, so that I do not have to know which part of the system I belong to.

**Acceptance criteria**

- Username and password, with clear errors on failure.
- Signing in while already signed in goes straight to my dashboard.
- The page states that record access is logged.

**Covered by**

- `TestLogin (5 tests)`

### S-102 · Land on the dashboard for my role

`2 points` · **Done**

> As a doctor, receptionist or patient, I want to arrive at my own screen after signing in, so that I never have to navigate to it.

**Acceptance criteria**

- Doctor → today's clinic. Receptionist → queue board. Patient → appointments.
- An administrator goes to the Django admin.
- A signed-out visitor is sent to sign in.

**Covered by**

- `TestRoleRouting (4 tests)`

### S-103 · Roles are enforced by the server, not by hidden links

`5 points` · **Done**

> As the clinic owner, I want role limits enforced in code, so that typing a URL cannot reveal something a user should not see.

**Acceptance criteria**

- A receptionist opening a clinical chart gets 403, not a redirect.
- A doctor cannot work the reception queue.
- A patient cannot reach any chart or billing screen.
- Restrictions hold on POST as well as GET.

**Covered by**

- `TestDoctorAccessControl (5 tests)`
- `TestEditingAccessControl (4 tests)`
- `TestReceptionQueue.test_a_doctor_cannot_work_the_queue`
- `TestBilling.test_a_patient_cannot_reach_billing`
- `TestPatientPortal.test_a_patient_cannot_reach_a_clinical_chart`

### S-104 · Staff accounts managed from the admin

`5 points` · **Done**

> As the clinic owner, I want to create doctor, receptionist and patient logins myself, so that I am not waiting on a developer to add staff.

**Acceptance criteria**

- Username, password, email, contact number and role, as specified.
- Doctors carry qualification, registration number and signature for prescriptions.
- Email addresses are unique.

> ⚠️ **Test gap.** No automated test — the admin screens are exercised by hand. Worth a smoke test before go-live.

### S-105 · Every look at a record is recorded, permanently

`5 points` · **Done**

> As the clinic owner, I want to answer “who opened this patient's file”, so that I can respond if a patient ever asks.

**Acceptance criteria**

- Opening a chart, creating or editing a record, printing, and signing in or out are all logged with user, time and IP.
- A failed sign-in is logged without the attempted password.
- Entries cannot be edited or deleted — including in bulk, and including from admin.
- No patient identifiers appear in application logs.

**Covered by**

- `TestAuditLogIsAppendOnly (2 tests)`
- `TestAuditLogCannotBeBulkErased (2 tests)`
- `TestEditingIsAudited (2 tests)`
- `TestPatientDashboard.test_opening_a_chart_is_written_to_the_audit_trail`
- `TestLogin.test_successful_login_is_recorded_in_the_audit_trail`
- `TestLogin.test_failed_login_is_recorded_without_the_password`
- `TestReceptionQueue.test_each_move_is_recorded_against_the_patient`

### S-106 · Sessions expire and cookies are secure

`3 points` · **Done**

> As the clinic owner, I want an unattended screen to lock itself, because patient records are on display in a room people walk through.

**Acceptance criteria**

- Thirty minutes of inactivity ends the session.
- HTTPS enforced in production, with HSTS and secure, HTTP-only cookies.
- The admin is mounted at a path set by environment variable; /admin/ is a decoy.

> ⚠️ **Test gap.** Configuration rather than behaviour. Verified by `manage.py check --deploy`, which passes clean.

---

## E2 · Patient records

*One unmistakable identity per patient, and the longitudinal history that hangs off it.*

**16 of 16 points delivered.**

### S-201 · Every patient gets a unique UHID

`5 points` · **Done**

> As a receptionist, I want each patient to have one permanent number, so that their whole history can be found from the number on their file.

**Acceptance criteria**

- Format CEMH-YY-NNNNN, issued automatically, never reused, never edited.
- Two receptionists registering at the same instant cannot produce a duplicate.
- The prefix is configurable per clinic.

**Covered by**

- `TestPatientIdentifier (4 tests)`
- `TestSaving.test_uhid_is_not_editable`

### S-202 · Patient demographics, including guardians

`3 points` · **Done**

> As a receptionist, I want a child's guardian recorded, so that I ring the right person when confirming an appointment.

**Acceptance criteria**

- Name, date of birth, sex, blood group, contact, address.
- Guardian name, relationship and phone for paediatric patients.
- Age shows in days, months or years as appropriate to how young they are.
- The contact number offered is the guardian's for a child, the patient's for an adult.

**Covered by**

- `TestPatientAge (5 tests)`
- `TestSaving.test_editing_patient_details_persists`

### S-203 · Standing background history

`3 points` · **Done**

> As a doctor, I want past history, family history and allergies on screen at every visit, so that I am not re-reading old notes to find them.

**Acceptance criteria**

- Presenting complaints, past medical, family, birth and development, allergies, medications, surgical, lifestyle.
- One record per patient — editing never creates a duplicate.
- Allergies are shown prominently on the sidebar and on printed prescriptions.

**Covered by**

- `TestSaving.test_editing_history_updates_the_existing_record`
- `TestPatientDashboard.test_allergies_are_surfaced_on_the_sidebar`

### S-204 · Register a new patient at the desk

`3 points` · **Done**

> As a receptionist, I want to register a caller without leaving the booking screen, because having to go elsewhere first is what stalls a phone booking.

**Acceptance criteria**

- Registration is reachable from the booking screen and returns to it.
- A UHID is issued on save.

**Covered by**

- `TestReceptionBooking.test_registering_a_patient_issues_a_uhid`

### S-205 · Find a patient by name, UHID or mobile

`2 points` · **Done**

> As clinic staff, I want to find a patient by whatever the caller gives me, so that I am not asking them to read out a number they may not have.

**Acceptance criteria**

- Search matches UHID, first or last name, patient mobile or guardian mobile.
- The doctor can type a UHID or mobile to open a chart directly.
- UHID matching ignores case.
- An unknown value reports plainly rather than failing.

**Covered by**

- `TestReceptionBooking.test_patient_search_finds_by_uhid_and_name`
- `TestDoctorHome (4 lookup tests)`

---

## E3 · Appointments & the clinic day

*The diary reflects reality, and a patient can only move forward through the day.*

**31 of 31 points delivered.**

### S-301 · A visit moves through a fixed sequence

`8 points` · **Done**

> As the clinic owner, I want the software to mirror how the clinic actually runs, so that the queue cannot get into a state that never happens in real life.

**Acceptance criteria**

- Booked → Confirmed → Arrived → In cabin → Consulted → Billed → Completed, with Cancelled and No-show as exits.
- An out-of-order move is refused, not silently accepted.
- Arrival, cabin, consultation and completion times are stamped as they happen.
- Every change records who made it.

**Covered by**

- `TestVisitLifecycle (9 tests)`
- `TestReceptionQueue.test_an_out_of_order_move_is_refused`

### S-302 · Two patients can never hold one doctor at once

`5 points` · **Done**

> As a receptionist, I want double booking to be impossible, so that I never have to apologise to a patient for it.

**Acceptance criteria**

- Enforced by the database, so it holds under simultaneous requests.
- Back-to-back appointments are fine; overlapping ones are not.
- Cancelling frees the slot again.
- Two different doctors may of course be busy at the same time.

**Covered by**

- `TestNoDoubleBooking (4 tests)`

### S-303 · Free slots are offered from consulting hours

`5 points` · **Done**

> As a receptionist, I want to see only times that are genuinely free, so that I can answer a caller straight away.

**Acceptance criteria**

- Hours, slot length, working days and booking horizon are configuration.
- Booked slots disappear; cancelled ones come back.
- Closed days offer nothing and say why.
- Patients are never offered a time that has already passed; reception may be.

**Covered by**

- `TestScheduling (5 tests)`

### S-304 · Take a booking at the desk or over the phone

`5 points` · **Done**

> As a receptionist, I want to book a patient in a few clicks, so that I am not keeping a caller waiting.

**Acceptance criteria**

- Search or register the patient, pick doctor and date, pick from free times.
- Every booking starts unconfirmed — the receptionist still has to ring the patient on the day, and confirming is that call.
- If someone takes the slot first, the error is readable, not a crash.
- A slot that is not on the chosen date is rejected.

**Covered by**

- `TestReceptionBooking (6 tests)`

### S-305 · Run the day from a queue board

`8 points` · **Done**

> As a receptionist, I want one screen showing where every patient is, so that I can call them in and send them through without a paper list.

**Acceptance criteria**

- A column per stage, with counts.
- "To confirm" is separate from "Confirmed", so the receptionist can see exactly who she still has to telephone.
- An unconfirmed card leads with the number to ring, as a tap-to-dial link, and names the guardian to ask for when the patient is a child.
- Each card offers only the moves legal from that patient's current state.
- Waiting time counts up and is highlighted past thirty minutes.
- The board refreshes itself, because a stale board sends the wrong patient in.
- Any day can be reviewed, not just today.

**Covered by**

- `TestReceptionQueue (5 tests)`
- `TestConfirmationCallIsVisibleWork (3 tests)`

---

## E4 · The doctor's chart

*Everything known about a patient, readable at a glance and editable in place.*

**21 of 21 points delivered.**

### S-401 · Open a chart by typing the UHID

`3 points` · **Done**

> As a doctor, I want to type the number from the patient's file, so that I reach their record without hunting through a list.

**Acceptance criteria**

- UHID or mobile number both work.
- Today's queue is listed below, with whoever is in the cabin first.
- Another doctor's patients do not appear in my queue.

**Covered by**

- `TestDoctorHome (6 tests)`

### S-402 · A patient panel that stays on screen

`3 points` · **Done**

> As a doctor, I want identity, allergies, active problems and the latest measurements always visible, so that they are never a tab away.

**Acceptance criteria**

- Allergies are prominent, and say so explicitly when none are recorded.
- Active problems, latest measurements, last vitals, attendance summary.
- The panel refreshes when any of it is edited, from whichever tab.

**Covered by**

- `TestPatientDashboard (6 tests)`

### S-403 · Five tabs, loaded without losing my place

`5 points` · **Done**

> As a doctor, I want to move between summary, notes, results, growth and prescriptions quickly, so that consultation time is not spent waiting.

**Acceptance criteria**

- Summary, Clinical Notes, Investigations, Growth Chart, Prescriptions.
- Tabs swap without a full page reload.
- Visits are grouped into the time bands clinicians read charts in.
- Investigations are grouped by test, so a value can be followed over years.

**Covered by**

- `TestDashboardTabs (6 tests)`

### S-404 · Edit every element from the chart

`8 points` · **Done**

> As a doctor, I want to correct or add to any part of the record where I am reading it, so that I never go looking for a separate editing screen.

**Acceptance criteria**

- Patient details, history, problems, investigations, notes, measurements, prescriptions and their drug lines.
- Saving refreshes the chart and panel in place.
- An invalid form redisplays with errors rather than losing the entry.
- A record cannot be edited through another patient's URL.
- The UHID is never editable.

**Covered by**

- `TestFormsOpen (4 tests)`
- `TestSaving (8 tests)`

### S-405 · The chart knows which visit is in progress

`2 points` · **Done**

> As a doctor, I want the consultation I am actually in to drive the screen, so that a follow-up already in the diary does not hide it.

**Acceptance criteria**

- The patient in the cabin takes precedence over any future booking.
- The Complete consultation action stays available.

*Added after this bug was found by driving the workflow in a browser.*

**Covered by**

- `TestChartShowsTheVisitInProgress (2 tests)`

---

## E5 · Growth & anthropometry

*Make a child's growth trajectory legible at a glance. Removable for clinics that do not need it.*

**37 of 37 points delivered.**

### S-501 · Record measurements at a visit

`3 points` · **Done**

> As a paediatric endocrinologist, I want height, weight and puberty staging recorded against the date they were taken, so that trends are truthful.

**Acceptance criteria**

- Height, weight, head circumference, waist, Tanner stage, parental heights.
- BMI is computed, never typed, so it cannot contradict height and weight.
- Age is taken at the measurement date, not today.

**Covered by**

- `TestMeasurement (5 tests)`
- `TestSaving.test_adding_a_measurement_persists_it`

### S-502 · Percentiles from published growth references

`8 points` · **Done**

> As a paediatric endocrinologist, I want a measurement placed on a centile, so that short stature is visible rather than inferred.

**Acceptance criteria**

- LMS method against published reference tables.
- Verified against the SD columns published alongside those tables.
- Returns nothing rather than guessing when no reference covers the patient.
- Sex-specific; declines to chart when sex is recorded as neither.
- The standard in use is chosen by GROWTH_REFERENCE, and a standard whose tables are absent falls back to another — visibly, never silently.

**Covered by**

- `TestAgainstPublishedValues (2 tests)`
- `TestZScoreAndPercentile (5 tests)`
- `TestAssess (7 tests)`
- `TestStandardSelection (8 tests)`

### S-503 · Plot the child against the centile curves

`5 points` · **Done**

> As a paediatric endocrinologist, I want the child's line drawn over the reference family, so that crossing centiles is obvious.

**Acceptance criteria**

- Height, weight and BMI for age; head circumference under three.
- 3rd to 97th centile curves behind the patient's own line.
- The centile is stated in words as well as drawn.
- Curves stop where the published data stops — never extrapolated.

**Covered by**

- `TestReferenceCurves (3 tests)`
- `TestDashboardTabs.test_growth_tab_plots_a_recorded_measurement`
- `TestDashboardTabs.test_growth_tab_without_measurements_shows_an_empty_state`

### S-504 · Mid-parental target height

`2 points` · **Done**

> As a paediatric endocrinologist, I want the target height from the parents' heights, so that I can judge whether the child is on their own track.

**Acceptance criteria**

- Boys (father + mother + 13) / 2; girls (father + mother − 13) / 2.
- Shown only when both parental heights are known.

**Covered by**

- `TestMeasurement.test_mid_parental_height_adds_thirteen_for_a_boy`
- `TestMeasurement.test_mid_parental_height_subtracts_thirteen_for_a_girl`

### S-506 · Import supplied growth tables safely

`3 points` · **Done**

> As the clinic owner, I want new reference tables installed without a developer hand-editing clinical data.

**Acceptance criteria**

- The command reads the IAP 2015 paper's own PDF, so no value is transcribed by hand; a single table may also come from a CSV.
- It refuses anything it cannot vouch for, naming the offending rows: values must rise across each row and smoothly with age.
- For BMI it also checks P50 < 23-Eq < 27-Eq throughout, and that the two cut-off lines have converged on the adult 23 and 27 by eighteen years.
- The tables installed in the repository are re-validated by the test suite, so a hand edit to clinical data fails the build.

**Covered by**

- `TestASoundTableIsAccepted, TestTransposedColumnsAreRefused (3 tests)`
- `TestADroppedDigitIsRefused (2 tests)`
- `TestTheShapeOfTheTableIsChecked (2 tests)`
- `TestTheBmiCutoffCheck (3 tests)`
- `TestTheInstalledTablesStillPassTheirOwnChecks`

### S-505 · Chart against IAP 2015 Indian references

`5 points` · **Done**

> As a paediatric endocrinologist, I want Indian children charted against Indian references, so that centiles reflect the population I treat.

**Acceptance criteria**

- WHO below five years, IAP 2015 for 5–18 — IAP's own recommendation, and the standard the clinic has chosen.
- All six tables installed from the paper: height, weight and BMI, both sexes, 5.0–18.0 years at half-year steps.
- A child of exactly 5.0 years is charted against IAP, where those charts begin; above 18 the chart falls back to CDC and says so.
- Every chart names the reference that actually produced it.

*IAP Growth Chart Committee, Indian Pediatrics 2015;52:47–55, Tables II–VII.*

**Covered by**

- `TestTablesAreInstalled (2 tests)`
- `TestAgainstThePublishedCentiles (4 tests)`
- `TestCurvesComeFromThePaper (4 tests)`
- `TestStandardSelection.test_the_five_year_boundary_belongs_to_iap`

### S-507 · Say which kind of number the reference can give

`5 points` · **Done**

> As a paediatric endocrinologist, I want to know whether a centile was computed or read off a printed curve, so that I know how much to trust the decimal places.

**Acceptance criteria**

- WHO and CDC publish LMS parameters, so an exact centile and z-score are shown, as before.
- IAP publishes the curves only, so the child is placed in the band between two printed centiles, with an SDS interpolated between them.
- No LMS is back-fitted from the seven printed points — that would report a z-score reading as exact while carrying invisible fitting error.
- The paper's SD column is stored but never scored against: it is the sample SD, and using it would put a child on the printed 97th centile at +2.2 to +3.4 SDS instead of +1.88.
- Asking a centile reference for an exact z-score raises rather than returning an approximation.

**Covered by**

- `TestNoExactZScoreIsInvented (4 tests)`
- `TestGrowthTabShowsWhichReferenceAnswered (5 tests)`

### S-508 · Refuse to guess below the 3rd centile

`3 points` · **Done**

> As a paediatric endocrinologist, I want no invented number for a child off the printed scale, but I still want an SDS I can act on.

**Acceptance criteria**

- Outside the outermost printed curve the band is still stated — 'below the 3rd centile' — but no centile or SDS is interpolated.
- A z-score from an LMS reference is supplied alongside and labelled with the source it came from, so growth-hormone decisions still have a figure.
- A child on the scale gets no companion figure, so the fallback appears only where it is needed.

*WHO's 2007 5–19y reference would be a better companion than CDC for Indian children; it publishes LMS and is a drop-in when sourced.*

**Covered by**

- `TestOffTheScale (5 tests)`
- `TestGrowthTabShowsWhichReferenceAnswered.test_a_child_below_the_third_centile_gets_a_labelled_companion`

### S-509 · Overweight and obesity from the adult-equivalent cut-offs

`3 points` · **Done**

> As a paediatric endocrinologist, I want overweight and obesity judged by the Asian cut-offs, because Indian children carry more risk at a lower BMI.

**Acceptance criteria**

- Four bands: thinness below the 3rd centile, then normal, overweight at the 23-equivalent line and obesity at the 27-equivalent line.
- The cut-offs are age- and sex-specific — 15.7 kg/m² at five years, 23.2 at eighteen — never the adult 25 and 30.
- The 23-Eq and 27-Eq columns are excluded from centile placement, because they are cut-offs and not percentiles of anything.
- Both lines are drawn on the BMI chart, labelled, in warning colours.
- A clinic charting against WHO or CDC gets no verdict at all rather than one derived from the wrong lines.

**Covered by**

- `TestTheFourBands (7 tests)`
- `TestTheCutoffsAreNotTheAdultOnes (4 tests)`
- `TestNoVerdictWhenTheCutoffsAreUnavailable (6 tests)`
- `TestTheBmiEqColumnsAreNotCentiles (3 tests)`

---

## E6 · Prescriptions

*The doctor's instructions reach the patient on paper, and reach reception at the right moment.*

**13 of 13 points delivered.**

### S-601 · Compose a prescription with medication lines

`5 points` · **Done**

> As a doctor, I want to write drug, strength, dose, frequency and duration, so that the patient leaves with unambiguous instructions.

**Acceptance criteria**

- Multiple medication lines, plus advice, investigations advised and follow-up date.
- Existing lines can be removed.
- A new prescription is a draft — saving never issues it.

**Covered by**

- `TestPrescriptionWorkflow.test_new_prescription_starts_as_a_draft`

### S-602 · Issuing is a deliberate handover

`3 points` · **Done**

> As a doctor, I want to decide when a prescription is final, so that a half-written one never reaches the front desk.

**Acceptance criteria**

- Reception can only print an issued prescription.
- The draft/issued state is visible on the chart.

**Covered by**

- `TestPrescriptionWorkflow.test_sending_to_reception_marks_it_generated`

### S-603 · One action ends the consultation

`5 points` · **Done**

> As a doctor, I want the fee and the prescription to go to reception together, so that the receptionist is told what to collect rather than asking.

**Acceptance criteria**

- Records the fee, issues the prescription and moves the visit, in one step.
- Refused when the patient is not in the cabin.
- Warns when no medication has been added, but permits advice-only.
- A receptionist cannot set the fee.

**Covered by**

- `TestConsultationHandover (4 tests)`

---

## E7 · Billing & receipts

*Money is collected against what the doctor charged, and evidenced.*

**15 of 15 points delivered.**

### S-701 · See what is owed before taking payment

`3 points` · **Done**

> As a receptionist, I want the charge already on screen, so that I do not have to interrupt the doctor to ask.

**Acceptance criteria**

- Consultation fee, procedure charges and discount, with a total.
- The patient, doctor and visit date are shown alongside.

**Covered by**

- `TestBilling.test_billing_page_shows_what_is_owed`
- `TestConsultationHandover.test_a_discount_reduces_what_reception_collects`

### S-702 · Record payment and issue a receipt

`5 points` · **Done**

> As a receptionist, I want a numbered receipt produced automatically, so that the patient always leaves with proof of payment.

**Acceptance criteria**

- Cash, UPI, card or bank transfer, with a reference field.
- A receipt is created on payment and records who took the money.
- Payment in full settles the visit.

**Covered by**

- `TestBilling.test_payment_in_full_issues_a_receipt_and_settles_the_visit`

### S-703 · Part payments stay outstanding

`3 points` · **Done**

> As a receptionist, I want a partly-paid visit to stay on my list, so that an unpaid balance is not quietly forgotten.

**Acceptance criteria**

- The balance is shown.
- The visit is not settled until nothing is outstanding.

**Covered by**

- `TestBilling.test_a_part_payment_leaves_the_visit_unsettled`

### S-704 · Receipt numbers never collide

`2 points` · **Done**

> As the clinic owner, I want receipt numbers to be unique, because they are financial records.

**Acceptance criteria**

- Format R-YY-NNNNN from a database sequence.
- Never reused, never edited.

**Covered by**

- `TestBilling.test_receipt_numbers_do_not_collide`

### S-705 · Check the patient out

`2 points` · **Done**

> As a receptionist, I want to close the visit when the patient leaves, so that the board reflects who is still in the building.

**Acceptance criteria**

- Available once the visit is settled.

**Covered by**

- `TestBilling.test_checkout_completes_the_visit`

---

## E8 · Printing

*Paper that looks like it came from this clinic.*

**5 of 5 points delivered.**

### S-801 · Print the prescription on letterhead

`3 points` · **Done**

> As a receptionist, I want to hand the patient a proper prescription, so that it is accepted at any pharmacy.

**Acceptance criteria**

- Clinic name, address and telephone; doctor's name, qualification and registration number.
- Allergies printed prominently when recorded.
- A scanned signature appears when uploaded.
- Laid out for A4; printing is recorded.

**Covered by**

- `TestBilling.test_printing_a_prescription_records_it`

### S-802 · Print the receipt

`2 points` · **Done**

> As a receptionist, I want a printable receipt, so that the patient can claim reimbursement.

**Acceptance criteria**

- Receipt number, date, itemised charges, amount received, method.
- Balance shown if any remains.

**Covered by**

- `TestBilling.test_printing_a_receipt_shows_the_number`

---

## E9 · The public page

*One page that tells people what the clinic does and gets them to telephone or send a WhatsApp message. Patients never sign in.*

**8 of 8 points delivered.**

### S-901 · A single public page for the clinic

`5 points` · **Done**

> As somebody looking for an endocrinologist, I want to see what this clinic treats and who its doctors are, so that I know it is the right place to ring.

**Acceptance criteria**

- Clinic name, address, consulting hours and both consultants with their qualifications.
- What the clinic treats, in adult and paediatric columns, matching the printed brochure.
- Readable on a phone, which is how most people will find it.
- The only page in the system that search engines may index; everything behind the login stays hidden from them.
- No patient information appears anywhere on it.

**Covered by**

- `TestPublicPage (9 tests)`
- `TestPublicPageLeaksNothing (2 tests)`
- `TestEverythingElseStillNeedsALogin (2 tests)`

### S-902 · Call and WhatsApp buttons that actually book

`3 points` · **Done**

> As somebody wanting an appointment, I want to reach the clinic in one tap, so that I do not have to fill in a form and wait.

**Acceptance criteria**

- Tap-to-dial and WhatsApp buttons, at the top of the page and again at the end.
- The WhatsApp link carries the country code and a message already typed.
- The page says plainly that bookings are by telephone, not online.
- Which number receives them is configurable per clinic.

**Covered by**

- `TestPublicPage.test_offers_a_telephone_link`
- `TestPublicPage.test_offers_a_whatsapp_link_in_international_format`

### S-903 · Patient portal — logins, appointment list, online booking

`0 points` · **Withdrawn**

> As a patient, I want to see my appointments and book online.

**Acceptance criteria**

- Built and working, then removed at the clinic's request.
- Reception takes every booking; patients call or send a WhatsApp message.

*Withdrawn, not forgotten. Recorded here so the decision is visible: the clinic decided patients should not book online, so a working portal was deleted rather than left behind an unused login.*

**Covered by**

- `TestNoPatientPortal (2 tests) — asserts the routes are gone`

---

## E10 · Platform, reuse & deployment

*Runs reliably for this clinic, and can be re-skinned for the next one without a fork.*

**16 of 21 points delivered.**

### S-1001 · 12-factor project on PostgreSQL, in Docker

`5 points` · **Done**

> As a developer, I want configuration in the environment, so that the same image runs any clinic.

**Acceptance criteria**

- Separate dev, production and test settings.
- Database, secrets, hosts and branding from environment variables.
- docker compose up gives a working stack from nothing.
- Production refuses to start with a default secret key or empty hosts.

> ⚠️ **Test gap.** No automated test. Verified by rebuilding the database from empty and running `check --deploy`, which passes.

### S-1002 · Realistic demo data on demand

`3 points` · **Done**

> As a developer or reviewer, I want lifelike data in one command, so that screens can be judged against something resembling a real practice.

**Acceptance criteria**

- Five patients with years of visits, results and measurements.
- Re-runnable; refuses to run with DEBUG off unless forced.
- Identifiers restart so demo output is reproducible.

> ⚠️ **Test gap.** No automated test — exercised manually on every rebuild.

### S-1003 · Re-skin for another clinic without forking

`3 points` · **Done**

> As the clinic owner, I want a second clinic to differ only in branding and features, so that core fixes still merge down.

**Acceptance criteria**

- Colours in one CSS variables file.
- Name, address, UHID prefix and hours in one settings file.
- Logo and letterhead in one templates directory.

> ⚠️ **Test gap.** No automated test. Verified by editing theme.css and confirming the whole interface re-skins.

### S-1004 · Speciality features are removable modules

`5 points` · **Done**

> As the clinic owner, I want to switch off features another clinic does not need, so that an orthopaedic practice never sees growth charts.

**Acceptance criteria**

- Removing 'growth' from OPTIONAL_APPS removes its models, admin and tab together.
- No core app imports from a speciality app.
- The application still boots with it removed.

> ⚠️ **Test gap.** No automated test. Verified by hand. **Worth automating** — this is the property the whole multi-clinic strategy rests on.

### S-1005 · Deploy to the clinic's own server

`5 points` · **Partial**

> As the clinic owner, I want the system running on our own domain, so that staff can start using it.

**Acceptance criteria**

- DigitalOcean droplet in Bangalore with managed PostgreSQL.
- Caddy for automatic HTTPS.
- Automated backups with point-in-time recovery, and a tested restore.
- Domain pointed and certificate issued.

> ⚠️ **Test gap.** Compose files, Caddyfile and production settings are written and checked. Nothing has been deployed — no server, domain or database exists yet.

---

## Backlog — agreed but not started

7 stories, 34 points.

### S-1102 · Appointment reminders by SMS or WhatsApp

`8 points` · **Backlog**

> As a receptionist, I want patients reminded automatically, so that fewer fail to attend.

Needs a provider decision and a background job runner — neither exists yet.

### S-1103 · Clinical form builder

`8 points` · **Backlog**

> As a doctor, I want to add my own questions to the consultation form without a developer.

The FormDefinition model and the JSON field it drives are built; there is no screen to edit definitions, so new fields still need a developer.

### S-1104 · Daily collection and attendance report

`5 points` · **Backlog**

> As the clinic owner, I want a daily summary of patients seen and money taken.

All the underlying data is recorded; nothing reports on it yet.

### S-1105 · Doctor availability and leave

`5 points` · **Backlog**

> As a receptionist, I want to block out leave and vary consulting hours per doctor.

Hours are currently one setting for the whole clinic.

### S-1106 · Upload investigation reports from the chart

`3 points` · **Backlog**

> As a doctor, I want to attach the lab's PDF to a result.

The file field exists on the model and works in admin, but is not on the chart's add-result form.

### S-1108 · Lock accounts after repeated failed sign-ins

`2 points` · **Backlog**

> As the clinic owner, I want brute-force attempts blocked.

Failed attempts are already logged. Recommended before go-live.

### S-1109 · Backup and restore runbook

`3 points` · **Backlog**

> As the clinic owner, I want a tested procedure for restoring patient data.

Managed PostgreSQL provides the backups; the restore has never been rehearsed. This is the highest-risk gap once real patients are in the system.

---

## Testing

**Automated.** The suite runs in seconds against a real PostgreSQL database — `pytest` from the project root. The figure above is counted from the suite itself when this document is generated, not written down.

**Browser-driven.** The full booking-to-receipt path, and the public page, have been driven through a real browser. This found four bugs the unit tests had not — including one where a booked follow-up hid the doctor's Complete consultation action.

**Not yet covered.** Django admin screens, the deployment configuration, the removable-module guarantee, and the demo-data command. Each is noted against its story.

**Clinical validation.** The growth-chart maths is checked against published reference tables, but no clinician has yet reviewed the system against real cases.

### Stories with no automated cover

These are the places a regression would not be caught:

| Story | What is missing |
|---|---|
| S-104 · Staff accounts managed from the admin | No automated test — the admin screens are exercised by hand. Worth a smoke test before go-live. |
| S-106 · Sessions expire and cookies are secure | Configuration rather than behaviour. Verified by `manage.py check --deploy`, which passes clean. |
| S-1001 · 12-factor project on PostgreSQL, in Docker | No automated test. Verified by rebuilding the database from empty and running `check --deploy`, which passes. |
| S-1002 · Realistic demo data on demand | No automated test — exercised manually on every rebuild. |
| S-1003 · Re-skin for another clinic without forking | No automated test. Verified by editing theme.css and confirming the whole interface re-skins. |
| S-1004 · Speciality features are removable modules | No automated test. Verified by hand. Worth automating — this is the property the whole multi-clinic strategy rests on. |
| S-1005 · Deploy to the clinic's own server | Compose files, Caddyfile and production settings are written and checked. Nothing has been deployed — no server, domain or database exists yet. |

### Running the tests

```bash
pytest                      # all 212
pytest tests/test_workflow.py   # the clinic day, booking to receipt
pytest tests/test_growth_reference.py  # percentile maths vs published tables
```

---

## Open decisions for the clinic

1. **Where is it hosted, and when do we go live?** Recommended: DigitalOcean Bangalore with managed PostgreSQL, for India data residency and automated backups (S-1005).
2. **What else does the receptionist capture at check-in?** The mechanism for clinic-specific fields exists, but no fields have been agreed (S-1103).
3. **Do patients get portal logins, and who issues them?** Today they are created one at a time in the admin (S-1107).
4. **Should WHO's 2007 5–19 year reference be added alongside IAP?** It publishes LMS, so it would give a continuous SDS for a child below the 3rd centile, where the IAP tables stop. CDC fills that role today and is labelled as doing so (S-508). A decision for Dr. Vrushali.

