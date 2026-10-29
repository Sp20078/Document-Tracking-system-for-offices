/* Small progressive-enhancement helpers. The app works without JavaScript;
   these only add convenience feedback. */
document.addEventListener("DOMContentLoaded", function () {
  // --- Mobile sidebar -----------------------------------------------------
  var sidebar = document.getElementById("sidebar");
  var toggle = document.getElementById("sidebarToggle");
  var backdrop = document.getElementById("sidebarBackdrop");

  function closeSidebar() {
    if (sidebar) sidebar.classList.remove("open");
    if (backdrop) backdrop.classList.remove("show");
  }

  if (toggle && sidebar) {
    toggle.addEventListener("click", function () {
      sidebar.classList.toggle("open");
      if (backdrop) backdrop.classList.toggle("show");
    });
  }
  if (backdrop) backdrop.addEventListener("click", closeSidebar);
  document.querySelectorAll(".sidebar .side-link").forEach(function (link) {
    link.addEventListener("click", closeSidebar);
  });

  // --- Confirmation dialogs ----------------------------------------------
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.dataset.confirm)) {
        event.preventDefault();
      }
    });
  });

  // --- Loading feedback on form submission -------------------------------
  document.querySelectorAll("form[data-loading]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector("button[type='submit']");
      if (!button || button.disabled) return;
      button.dataset.originalHtml = button.innerHTML;
      button.disabled = true;
      button.innerHTML =
        '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Working…';
      // If the browser stays on the page (e.g. validation failed), recover.
      window.setTimeout(function () {
        button.disabled = false;
        if (button.dataset.originalHtml) button.innerHTML = button.dataset.originalHtml;
      }, 7000);
    });
  });

  // --- Filters auto-submit ------------------------------------------------
  document.querySelectorAll("[data-autosubmit]").forEach(function (field) {
    field.addEventListener("change", function () {
      if (field.form) field.form.submit();
    });
  });

  // --- Filter employees by the chosen department --------------------------
  var departmentSelect = document.querySelector("[data-department-select]");
  var assigneeSelect = document.querySelector("[data-assignee-select]");
  if (departmentSelect && assigneeSelect) {
    var options = Array.prototype.slice.call(
      assigneeSelect.querySelectorAll("option[data-department]")
    );
    var filterEmployees = function () {
      var department = departmentSelect.value;
      options.forEach(function (option) {
        var matches = !department || option.dataset.department === department;
        option.hidden = !matches;
        option.disabled = !matches;
        if (!matches && option.selected) assigneeSelect.value = "";
      });
    };
    departmentSelect.addEventListener("change", filterEmployees);
    filterEmployees();
  }

  // --- Auto-dismiss success alerts ---------------------------------------
  window.setTimeout(function () {
    document.querySelectorAll(".alert-success").forEach(function (alert) {
      var instance = bootstrap.Alert.getOrCreateInstance(alert);
      if (instance) instance.close();
    });
  }, 6000);
});
