// Home page: featured trails come from JSON, season notes load lazily on scroll.
(function () {
  var featured = document.getElementById("featured-trails");

  fetch("data/trails.json")
    .then(function (response) { return response.json(); })
    .then(function (data) {
      featured.innerHTML = "";
      data.trails.filter(function (t) { return t.featured; }).forEach(function (trail) {
        var card = document.createElement("article");
        card.className = "card";
        card.innerHTML =
          "<h3>" + trail.name + "</h3>" +
          "<p>" + trail.distance_km + " km, " + trail.elevation_m + " m of climbing. " + trail.summary + "</p>";
        featured.appendChild(card);
      });
    });

  var notes = document.getElementById("season-notes");
  var observer = new IntersectionObserver(function (entries) {
    if (!entries[0].isIntersecting) return;
    observer.disconnect();
    fetch(notes.getAttribute("data-lazy-src"))
      .then(function (response) { return response.json(); })
      .then(function (data) {
        notes.querySelector(".loading").remove();
        data.notes.forEach(function (text) {
          var p = document.createElement("p");
          p.textContent = text;
          notes.appendChild(p);
        });
      });
  });
  observer.observe(notes);
})();
