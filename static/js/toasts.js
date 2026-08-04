/*
 * Toast messages.
 *
 * A success message gets out of the way by itself after three seconds. An error
 * or a warning does not: it waits to be dismissed. The difference matters in a
 * clinic — "Patient added" is a courtesy, but "this bill was already settled"
 * is something the receptionist has to have read before the next patient walks
 * up, and three seconds is not long enough to guarantee she looked.
 *
 * Toasts arrive two ways: rendered into #toast-host on a page load, and swapped
 * into it out of band after an HTMX action. Both end up as .toast elements in
 * the host, so arming is driven by what is in the DOM rather than by which
 * route put it there.
 */
(function () {
  "use strict";

  var LIFETIME_MS = 3000;
  var FADE_MS = 200;

  function dismiss(toast) {
    if (toast.dataset.going === "1") {
      return;
    }
    toast.dataset.going = "1";
    toast.classList.add("toast--going");
    window.setTimeout(function () {
      if (toast.parentNode) {
        toast.parentNode.removeChild(toast);
      }
    }, FADE_MS);
  }

  function arm() {
    var toasts = document.querySelectorAll(".toast:not([data-armed])");

    Array.prototype.forEach.call(toasts, function (toast) {
      toast.dataset.armed = "1";

      var close = toast.querySelector(".toast__close");
      if (close) {
        close.addEventListener("click", function () {
          dismiss(toast);
        });
      }

      // Errors and warnings stay until somebody closes them.
      if (toast.dataset.persist === "1") {
        return;
      }

      var timer = window.setTimeout(function () {
        dismiss(toast);
      }, LIFETIME_MS);

      // Reading it should not be a race. Hovering holds it open; the clock
      // starts again on the way out.
      toast.addEventListener("mouseenter", function () {
        window.clearTimeout(timer);
      });
      toast.addEventListener("mouseleave", function () {
        timer = window.setTimeout(function () {
          dismiss(toast);
        }, LIFETIME_MS);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", arm);
  } else {
    arm();
  }

  // HTMX events bubble to the document, so one listener covers every swap,
  // including the out-of-band ones that deliver toasts from a board action.
  document.addEventListener("htmx:afterSettle", arm);
  document.addEventListener("htmx:oobAfterSwap", arm);
})();
