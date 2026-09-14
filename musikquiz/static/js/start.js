// Startskærm: vis artist/genre-menu og antal sange for det aktuelle valg.
(function () {
  "use strict";

  const form = document.getElementById("start-form");
  const dataEl = document.getElementById("availability-data");
  if (!form || !dataEl) return;

  document.documentElement.classList.add("js");

  const { availability, round_length: roundLength } = JSON.parse(dataEl.textContent);
  const info = document.getElementById("round-info");
  const startBtn = document.getElementById("start-btn");
  const levels = ["easy", "medium", "hard"];

  function selected(name) {
    const el = form.querySelector(`input[name="${name}"]:checked`);
    return el ? el.value : null;
  }

  function countsFor(category) {
    if (category === "all") return availability.all;
    const select = form.querySelector(`select[name="value_${category}"]`);
    return (select && availability[category][select.value]) || {};
  }

  function update() {
    const category = selected("category");
    form.querySelectorAll(".category-value").forEach((el) => {
      el.classList.toggle("is-visible", el.dataset.category === category);
    });

    const counts = countsFor(category);
    levels.forEach((level) => {
      const n = counts[level] || 0;
      const label = form.querySelector(`[data-count-for="${level}"]`);
      label.textContent = n === 1 ? "1 sang" : `${n} sange`;
      label.closest(".difficulty").classList.toggle("is-empty", n === 0);
    });

    const n = counts[selected("difficulty")] || 0;
    info.classList.toggle("is-warning", n > 0 && n < roundLength);
    if (n === 0) {
      info.textContent = "Ingen sange matcher dette valg — prøv en anden sværhedsgrad.";
      info.classList.add("is-warning");
    } else if (n < roundLength) {
      info.textContent = `Kun ${n} ${n === 1 ? "sang" : "sange"} matcher — runden bliver kortere.`;
    } else {
      info.textContent = `En runde er ${roundLength} sange.`;
    }
    startBtn.disabled = n === 0;
  }

  // Vælg automatisk en sværhedsgrad der har sange, hvis den valgte er tom.
  function ensureDifficultyWithSongs() {
    const counts = countsFor(selected("category"));
    if ((counts[selected("difficulty")] || 0) > 0) return;
    const first = levels.find((l) => (counts[l] || 0) > 0);
    if (first) form.querySelector(`input[name="difficulty"][value="${first}"]`).checked = true;
  }

  form.addEventListener("change", (event) => {
    if (event.target.name !== "difficulty") ensureDifficultyWithSongs();
    update();
  });

  ensureDifficultyWithSongs();
  update();
})();
