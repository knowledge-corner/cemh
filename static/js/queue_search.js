/*
 * Folding the Stage 1 search away until it is wanted (KAN-33).
 *
 * Enhancement only. The form is a plain GET form that filters on the server, so
 * with this file blocked or broken the search still works — it simply sits open
 * all the time. That is why the class is added here rather than being written
 * into the template: the collapsed state must not exist unless something is
 * able to open it again.
 *
 * The term also lives in the URL, which is what lets the board's thirty-second
 * poll carry it. Filtering the cards from here instead would undo itself on the
 * next refresh, between one glance at the screen and the next.
 */
(function () {
  "use strict";

  var box = document.getElementById("queue-search");
  if (!box) return;

  var toggle = box.querySelector(".qsearch__toggle");
  var form = box.querySelector(".qsearch__form");
  var field = form && form.querySelector('input[name="q"]');
  if (!toggle || !form || !field) return;

  box.classList.add("qsearch--enhanced");

  function open() {
    box.classList.add("is-open");
    toggle.setAttribute("aria-expanded", "true");
    field.focus();
    field.select();
  }

  function close() {
    box.classList.remove("is-open");
    toggle.setAttribute("aria-expanded", "false");
    toggle.focus();
  }

  toggle.addEventListener("click", open);

  form.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    /* Escape closes it, but only closes it. Clearing the box here would look
     * like the same key, while quietly leaving the board still filtered by a
     * term no longer on screen — the search term lives in the URL, not in this
     * input, so emptying the input does not widen the board. Use Clear. */
    close();
  });
})();
