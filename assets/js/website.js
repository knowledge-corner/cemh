/*
 * The public page's two pieces of behaviour: the mobile menu, and the
 * Adult / Paediatric tabs.
 *
 * Both are enhancements. The page is complete without this file — the menu
 * links are in the markup, and both service panels are open under their own
 * headings, which shows a visitor more of what the clinic treats rather than
 * less. The `wf-js` class this script adds is what tells the stylesheet it is
 * safe to hide one panel and show the tabs; nothing is hidden before that,
 * so a script that fails to load cannot take half the page with it.
 *
 * The FAQ accordion is deliberately absent: it is <details>, which opens,
 * closes, announces itself and answers find-in-page with no help from here.
 */
(function () {
  "use strict";

  document.documentElement.classList.add("wf-js");

  /* ── Mobile menu ────────────────────────────────────────────────────── */

  var burger = document.querySelector(".wf-burger");
  var mobileNav = document.getElementById("wf-mobile-nav");

  if (burger && mobileNav) {
    var setOpen = function (open) {
      mobileNav.hidden = !open;
      burger.setAttribute("aria-expanded", open ? "true" : "false");
      burger.setAttribute("aria-label", open ? "Close the menu" : "Open the menu");
    };

    burger.addEventListener("click", function () {
      setOpen(mobileNav.hidden);
    });

    /* Tapping a link navigates within the page, so the menu has to get out of
     * the way itself — nothing else will close it. */
    mobileNav.addEventListener("click", function (event) {
      if (event.target.closest("a")) setOpen(false);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !mobileNav.hidden) {
        setOpen(false);
        burger.focus();
      }
    });
  }

  /* ── Analytics events ──────────────────────────────────────────────────
   * Which button got pressed, not just that someone left the page. GA4's
   * own enhanced measurement already counts outbound clicks (the WhatsApp
   * links) and page views on its own, but it cannot tell a phone number in
   * the header from one in a doctor's card, and it does not tag tel: links
   * at all — so those need naming here. One delegated listener rather than
   * one per link, since the set of tracked links spans the whole page.
   *
   * Every call is guarded: an ad blocker or an unset Measurement ID must
   * never touch how the page behaves, matching this file's own rule that
   * the page is complete without it. */

  document.addEventListener("click", function (event) {
    if (typeof gtag !== "function") return;

    var link = event.target.closest("a[data-ga-label]");
    if (!link) return;

    var label = link.getAttribute("data-ga-label");
    var eventName = link.getAttribute("data-ga-event");
    if (!eventName) {
      if (link.href.indexOf("tel:") === 0) eventName = "call_click";
      else if (link.href.indexOf("https://wa.me/") === 0) eventName = "whatsapp_click";
    }
    if (!eventName) return;

    gtag("event", eventName, { location: label });
  });

  /* ── Service tabs ───────────────────────────────────────────────────── */

  var tabs = Array.prototype.slice.call(document.querySelectorAll(".wf-tab"));
  if (!tabs.length) return;

  var panelFor = function (tab) {
    return document.getElementById(tab.getAttribute("aria-controls"));
  };

  var select = function (tab, moveFocus) {
    tabs.forEach(function (other) {
      var isChosen = other === tab;
      var panel = panelFor(other);

      other.classList.toggle("is-active", isChosen);
      other.setAttribute("aria-selected", isChosen ? "true" : "false");
      /* One stop in the tab order for the whole set: Tab moves past the tabs
       * to the panel, and the arrow keys move between them. That is what a
       * screen-reader user is told a tablist does. */
      other.setAttribute("tabindex", isChosen ? "0" : "-1");
      if (panel) panel.hidden = !isChosen;
    });
    if (moveFocus) tab.focus();
  };

  tabs.forEach(function (tab, index) {
    tab.addEventListener("click", function () { select(tab, false); });

    tab.addEventListener("keydown", function (event) {
      var step = 0;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") step = 1;
      else if (event.key === "ArrowLeft" || event.key === "ArrowUp") step = -1;
      else if (event.key === "Home") { select(tabs[0], true); event.preventDefault(); return; }
      else if (event.key === "End") { select(tabs[tabs.length - 1], true); event.preventDefault(); return; }
      if (!step) return;

      event.preventDefault();
      select(tabs[(index + step + tabs.length) % tabs.length], true);
    });
  });

  /* Collapse to the first tab now that the tabs themselves are visible. */
  select(tabs[0], false);
})();
