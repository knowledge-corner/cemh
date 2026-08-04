/*
 * The "add a new specialisation" option on the Add Doctor form.
 *
 * The text box is hidden until the dropdown says it is needed, and disabled
 * while hidden — hidden stops it being seen, only disabled stops it being
 * submitted, and a name typed and then abandoned should not travel with the
 * form.
 *
 * Whether a typed name is required is settled on the server. This decides only
 * what is on screen, and this file can be blocked, fail to load, or be edited
 * by anyone with the page open.
 */
(function () {
  "use strict";

  var ADD_NEW = "__new__";

  var select = document.getElementById("id_specialisation");
  var field = document.getElementById("new-specialisation-field");
  if (!select || !field) {
    return;
  }

  var input = document.getElementById("id_new_specialisation");

  function apply() {
    var adding = select.value === ADD_NEW;

    field.hidden = !adding;
    if (input) {
      input.disabled = !adding;
      if (adding) {
        input.focus();
      } else {
        input.value = "";
      }
    }
  }

  select.addEventListener("change", apply);

  // On load, so a form that came back carrying validation errors still shows
  // the box the user was typing in.
  apply();
})();
