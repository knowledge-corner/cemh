/*
 * Periodic full-page reload for a read-mostly screen — the calendar, the
 * bookings list. Both are left open on a desk for long stretches, and staff
 * expect what they show to be current without a manual refresh.
 *
 * Skipped on any tick where reloading would lose something: a field has
 * focus, or a modal is open. A screen quietly wiping what somebody just typed
 * reads as data disappearing, not as freshness.
 *
 *   <script src="{% static 'js/auto_refresh.js' %}" data-refresh-ms="120000" defer></script>
 */
(function () {
  "use strict";

  var script = document.currentScript;
  var ms = parseInt(script.getAttribute("data-refresh-ms"), 10);
  if (!ms) return;

  function fieldHasFocus() {
    var active = document.activeElement;
    return !!(active && ["INPUT", "TEXTAREA", "SELECT"].indexOf(active.tagName) !== -1);
  }

  function modalIsOpen() {
    // The htmx-swapped modal pattern (bookings, the reception board) —
    // present only while something has been swapped into it.
    var host = document.getElementById("modal-host");
    if (host && host.textContent.trim() !== "") return true;

    // The calendar's own hidden/shown modal, toggled by calendar.js rather
    // than htmx.
    var eventModal = document.getElementById("event-modal");
    if (eventModal && !eventModal.hidden) return true;

    return false;
  }

  setInterval(function () {
    if (!fieldHasFocus() && !modalIsOpen()) {
      window.location.reload();
    }
  }, ms);
})();
