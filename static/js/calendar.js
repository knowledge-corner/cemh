/*
 * The availability calendar's pop-up (KAN-22).
 *
 * Two jobs:
 *
 *   1. Open the add-event dialog on the day that was clicked, so reception does
 *      not have to re-type a date the calendar already knows.
 *   2. Show only the fields the chosen event type uses — and *disable* the
 *      rest, not merely hide them. A doctor half-chosen before switching to
 *      Holiday would otherwise still be submitted, and the server would be
 *      validating a field the user could no longer see.
 *
 * Nothing here is load-bearing for correctness: the server validates the
 * event type, the recurring range and the weekdays regardless of what this
 * script has hidden or disabled — uploading a rota CSV is the plain-HTML-form
 * way to do the same thing, just not in one step.
 */
(function () {
  "use strict";

  var modal = document.getElementById("event-modal");
  if (!modal) return;

  var typeField = document.getElementById("id_event_type");
  var recurringField = document.getElementById("id_is_recurring");
  var dateField = modal.querySelector('input[name="date"]');
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

  /* ── Opening and closing ────────────────────────────────────────────── */

  function open(on, opener) {
    lastOpener = opener || null;
    if (dateField && on) dateField.value = on;
    modal.hidden = false;
    applyType();
    var first = modal.querySelector("select, input");
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
    var opener = event.target.closest("[data-add-event]");
    if (opener) {
      event.preventDefault();
      open(opener.getAttribute("data-date"), opener);
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
