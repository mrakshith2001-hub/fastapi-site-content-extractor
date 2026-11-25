// Trail directory: renders every route into the scrollable list.
(function () {
  var list = document.getElementById("trail-list");
  var trails = [];

  function render(filter) {
    list.innerHTML = "";
    trails
      .filter(function (t) { return filter === "all" || t.difficulty === filter; })
      .forEach(function (trail) {
        var item = document.createElement("div");
        item.className = "trail";
        item.innerHTML =
          "<h3>" + trail.name + "</h3>" +
          "<p class=\"difficulty\">" + trail.difficulty + " &middot; " + trail.season + "</p>" +
          "<p>" + trail.distance_km + " km with " + trail.elevation_m + " m of elevation gain. " + trail.summary + "</p>";
        list.appendChild(item);
      });
  }

  fetch("data/trails.json")
    .then(function (response) { return response.json(); })
    .then(function (data) {
      trails = data.trails;
      render("all");
    });

  document.querySelectorAll("[data-filter]").forEach(function (button) {
    button.addEventListener("click", function () {
      render(button.getAttribute("data-filter"));
    });
  });
})();
