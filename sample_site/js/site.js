// Shared behaviour for every page: active nav link and modal dialogs.
(function () {
  var page = document.body.getAttribute("data-page");
  document.querySelectorAll("nav a").forEach(function (link) {
    if (link.getAttribute("href").indexOf(page === "home" ? "index" : page) !== -1) {
      link.classList.add("active");
    }
  });

  document.querySelectorAll("[data-modal-open]").forEach(function (trigger) {
    trigger.addEventListener("click", function () {
      var modal = document.getElementById(trigger.getAttribute("data-modal-open"));
      if (modal) modal.hidden = false;
    });
  });

  document.querySelectorAll("[data-modal-close]").forEach(function (button) {
    button.addEventListener("click", function () {
      button.closest(".modal").hidden = true;
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      document.querySelectorAll(".modal").forEach(function (modal) {
        modal.hidden = true;
      });
    }
  });
})();
