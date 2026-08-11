/*
 * The availability calendar's pop-up (KAN-22).
 *
 * Three jobs:
 *
 *   1. Open the dialog on the day that was clicked, pre-filled to add — so
 *      reception does not have to re-type a date the calendar already knows.
 *   2. Open the same dialog pre-filled to edit one existing row, repointed at
 *      reception_edit_calendar_event and with the Recurring section hidden —
 *      editing changes the occurrence that was clicked, never the pattern
 *      that generated it.
 *   3. Show only the fields the chosen event type uses — and *disable* the
 *      rest, not merely hide them. A doctor half-chosen before switching to
 *      Holiday would otherwise still be submitted, and the server would be
 *      validating a field the user could no longer see.
 *
 * Nothing here is load-bearing for correctness: the server validates the
 * event type, the recurring range, the weekdays and the row being edited
 * regardless of what this script has hidden, disabled or pre-filled —
 * uploading a rota CSV is the plain-HTML-form way to do the same thing for
 * an add, just not in one step.
 */
(function () {
  "use strict";

  var modal = document.getElementById("event-modal");
  if (!modal) return;

  var form = document.getElementById("event-modal-form");
  var title = document.getElementById("event-modal-title");
  var typeField = document.getElementById("id_event_type");
  var typeRow = document.getElementById("event-type-field");
  var recurringRow = document.getElementById("recurring-checkbox-field");
  var recurringField = document.getElementById("id_is_recurring");
  var dateField = modal.querySelector('input[name="date"]');
  var doctorField = modal.querySelector('select[name="doctor"]');
  var startField = modal.querySelector('input[name="start_time"]');
  var endField = modal.querySelector('input[name="end_time"]');
  var addUrl = form.getAttribute("data-add-url");
  var editUrlTemplate = form.getAttribute("data-edit-url-template");
  var lastOpener = null;

  /* ── Which fields belong to which event type ────────────────────────── */

  function setEnabled(section, on) {
    section.hidden = !on;
    var controls = section.querySelectorAll("input, select, textarea");
    for (var i = 0; i < controls.length; i++) {
      controls[i].disabled = !on;
    }
  }

  function belongsTo(section, chosen) {
    var owners = (section.getAttribute("data-event-fields") || "").split(/\s+/);
    for (var i = 0; i < owners.length; i++) {
      if (owners[i] === chosen) return true;
    }
    return false;
  }

  function applyType() {
    var chosen = typeField ? typeField.value : "hours";
    var sections = modal.querySelectorAll("[data-event-fields]");
    for (var i = 0; i < sections.length; i++) {
      setEnabled(sections[i], belongsTo(sections[i], chosen));
    }
    applyRecurring();
  }

  /* The end date and the weekday circles only mean anything once Recurring is
   * ticked — unticking it clears them as well as hiding them, so the server
   * never rejects a field the user can no longer see (a dead end, not a
   * correction they can act on). Left alone if the "hours" section is itself
   * disabled — applyType() has already cleared everything in that case. */
  function applyRecurring() {
    var section = modal.querySelector('[data-when="recurring"]');
    if (!section) return;

    var hoursOn = recurringField && !recurringField.disabled;
    var on = hoursOn && recurringField.checked;
    section.hidden = !on;

    var controls = section.querySelectorAll("input");
    for (var i = 0; i < controls.length; i++) {
      controls[i].disabled = !on;
      if (!on && controls[i].type === "checkbox") controls[i].checked = false;
      if (!on && controls[i].type === "date") controls[i].value = "";
    }
  }

  /* Editing never recurs — it changes the one row that was clicked. Hides
   * and disables the Recurring checkbox itself (not just the section it
   * reveals), and pins the event type to "hours" since a schedule row is
   * never a holiday. Reversed by resetRecurring() when the dialog is
   * reopened to add something instead. */
  function lockToSingleHoursRow() {
    if (typeField) {
      typeField.value = "hours";
      typeField.disabled = true;
    }
    if (typeRow) typeRow.hidden = true;
    if (recurringField) {
      recurringField.checked = false;
      recurringField.disabled = true;
    }
    if (recurringRow) recurringRow.hidden = true;
    applyType();
  }

  function unlockForAdding() {
    if (typeField) typeField.disabled = false;
    if (typeRow) typeRow.hidden = false;
    if (recurringField) recurringField.disabled = false;
    if (recurringRow) recurringRow.hidden = false;
    applyType();
  }

  /* ── Opening and closing ────────────────────────────────────────────── */

  function openToAdd(on, opener) {
    lastOpener = opener || null;
    form.action = addUrl;
    if (title) title.textContent = "Add to the calendar";
    unlockForAdding();
    if (dateField) dateField.value = on || "";
    if (doctorField) doctorField.value = "";
    if (startField) startField.value = "";
    if (endField) endField.value = "";
    show();
  }

  function openToEdit(opener) {
    lastOpener = opener || null;
    /* editUrlTemplate is reception_edit_calendar_event built with pk=0 — the
     * one part of the URL that ever contains a zero, so a literal swap of
     * that exact segment is enough. */
    form.action = editUrlTemplate.replace("/0/edit/", "/" + opener.getAttribute("data-pk") + "/edit/");
    if (title) title.textContent = "Edit working hours";
    lockToSingleHoursRow();
    if (dateField) dateField.value = opener.getAttribute("data-date") || "";
    if (doctorField) doctorField.value = opener.getAttribute("data-doctor") || "";
    if (startField) startField.value = opener.getAttribute("data-start") || "";
    if (endField) endField.value = opener.getAttribute("data-end") || "";
    show();
  }

  function show() {
    modal.hidden = false;
    var first = modal.querySelector("select:not([disabled]), input:not([disabled])");
    if (first) first.focus();
    document.addEventListener("keydown", onKey);
  }

  function close() {
    modal.hidden = true;
    document.removeEventListener("keydown", onKey);
    /* Back where they were, rather than at the top of the document. */
    if (lastOpener && document.contains(lastOpener)) lastOpener.focus();
  }

  function onKey(event) {
    if (event.key === "Escape") {
      close();
      return;
    }
    if (event.key !== "Tab") return;

    /* Keep Tab inside the dialog while it is open, or focus walks off into the
     * calendar behind it and the user is typing into a page they cannot see. */
    var focusable = modal.querySelectorAll(
      'button, [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])'
    );
    var visible = [];
    for (var i = 0; i < focusable.length; i++) {
      if (focusable[i].offsetParent !== null) visible.push(focusable[i]);
    }
    if (!visible.length) return;

    var first = visible[0];
    var last = visible[visible.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      last.focus();
      event.preventDefault();
    } else if (!event.shiftKey && document.activeElement === last) {
      first.focus();
      event.preventDefault();
    }
  }

  document.addEventListener("click", function (event) {
    var addOpener = event.target.closest("[data-add-event]");
    if (addOpener) {
      event.preventDefault();
      openToAdd(addOpener.getAttribute("data-date"), addOpener);
      return;
    }
    var editOpener = event.target.closest("[data-edit-event]");
    if (editOpener) {
      event.preventDefault();
      openToEdit(editOpener);
      return;
    }
    if (event.target.closest("[data-close-modal]")) {
      event.preventDefault();
      close();
      return;
    }
    if (event.target === modal && modal.hasAttribute("data-close-on-backdrop")) {
      close();
    }
  });

  modal.addEventListener("change", function (event) {
    if (event.target === typeField) applyType();
    if (event.target === recurringField) applyRecurring();
  });

  applyType();
})();
