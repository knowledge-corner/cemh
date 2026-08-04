/*
 * The guardian section on the patient registration form.
 *
 * Shown only when the date of birth makes the patient a minor, and hidden again
 * the moment it does not. Whether the fields are *required* is settled on the
 * server; this decides only what is on screen. A section that is merely hidden
 * is not a rule, and this file can be blocked, fail to load, or be edited by
 * anyone with the page open.
 *
 * The fields are disabled while hidden as well as hidden. `hidden` stops them
 * being seen; only `disabled` stops them being submitted, and a guardian name
 * typed for a child and then left behind when the date of birth is corrected to
 * an adult's is exactly the stale value that should not reach the server.
 */
(function () {
  "use strict";

  var GUARDIAN_AGE = 18;

  var dob = document.getElementById("id_date_of_birth");
  var section = document.getElementById("guardian-section");
  if (!dob || !section) {
    return;
  }

  var fields = section.querySelectorAll("input, select, textarea");

  function completedYears(born, on) {
    var years = on.getFullYear() - born.getFullYear();
    var monthDiff = on.getMonth() - born.getMonth();
    if (monthDiff < 0 || (monthDiff === 0 && on.getDate() < born.getDate())) {
      years -= 1;
    }
    return years;
  }

  function isMinor(value) {
    if (!value) {
      return false;
    }
    // Split rather than `new Date(value)`: the string form is parsed as UTC,
    // which shifts the date by a day for anyone west of Greenwich and would put
    // a birthday on the wrong side of the boundary.
    var parts = value.split("-");
    if (parts.length !== 3) {
      return false;
    }
    var born = new Date(+parts[0], +parts[1] - 1, +parts[2]);
    if (isNaN(born.getTime())) {
      return false;
    }
    var today = new Date();
    // Somebody whose eighteenth birthday is today is 18, and needs no guardian.
    return completedYears(born, today) < GUARDIAN_AGE;
  }

  function apply() {
    var needed = isMinor(dob.value);

    section.hidden = !needed;
    Array.prototype.forEach.call(fields, function (input) {
      input.disabled = !needed;
      if (!needed) {
        input.value = "";
      }
    });
  }

  // `change` covers the picker and a completed typed date; `blur` is the case
  // the acceptance criteria name. `input` keeps it responsive while typing.
  dob.addEventListener("change", apply);
  dob.addEventListener("blur", apply);
  dob.addEventListener("input", apply);

  // On load, so a form that came back carrying validation errors still shows
  // the section the patient's date of birth calls for.
  apply();
})();
