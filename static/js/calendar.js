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
 * Nothing here is load-bearing for correctness: the server validates by event
 * type regardless, and the Doctor schedule screen is plain HTML forms that can
 * do all of this without JavaScript, just not in one step.
 */
(function () {
  "use strict";

  var modal = document.getElementById("event-modal");
  if (!modal) return;

  var typeField = document.getElementById("id_event_type");
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

  /* Space-separated, because a couple of fields belong to more than one type:
   * "who is away" and "whose hours are these" are the same question and the
   * same picker, and duplicating it would let the two drift apart. */
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
    applyRepeat();
  }

  /* Three repeats, and each wants a different pair of fields:
   *
   *   "Does not repeat"     end date, no weekdays
   *   "On chosen weekdays"  end date *and* weekdays  (KAN-22)
   *   "Every week"          neither
   *
   * A weekly pattern runs until it is removed, so an end date would be a
   * promise the model cannot keep; the server refuses the combination, and
   * this stops it being offered. Days ticked and then abandoned by changing
   * the repeat are cleared as well as hidden — the server rejects them rather
   * than ignoring them, and a rejection naming a field the user can no longer
   * see is a dead end.
   */
  function applyRepeat() {
    var repeat = modal.querySelector('select[name="repeat"]');
    var value = repeat && !repeat.disabled ? repeat.value : "once";

    var range = modal.querySelector('[data-when="once"]');
    if (range) {
      var weekly = value === "weekly";
      range.hidden = weekly;
      var input = range.querySelector("input");
      if (input) {
        input.disabled = weekly;
        if (weekly) input.value = "";
      }
    }

    var picker = modal.querySelector('[data-when="selected"]');
    if (picker) {
      var selected = value === "selected";
      picker.hidden = !selected;
      var boxes = picker.querySelectorAll('input[type="checkbox"]');
      for (var i = 0; i < boxes.length; i++) {
        boxes[i].disabled = !selected;
        if (!selected) boxes[i].checked = false;
      }
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
    if (event.target.name === "repeat") applyRepeat();
  });

  applyType();
})();
