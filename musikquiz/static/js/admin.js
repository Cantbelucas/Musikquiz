// Admin: bekræft sletning, forhåndsvisning af lyd og filvalg i upload-feltet.
(function () {
  "use strict";

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  // Afspil/stop et spor direkte fra listen
  const audio = document.getElementById("preview-audio");
  let activeBtn = null;
  function stopPreview() {
    if (!audio) return;
    audio.pause();
    if (activeBtn) {
      activeBtn.classList.remove("is-playing");
      activeBtn.textContent = "▶";
    }
    activeBtn = null;
  }
  document.querySelectorAll(".preview-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (activeBtn === btn) return stopPreview();
      stopPreview();
      audio.src = btn.dataset.src;
      audio.currentTime = parseFloat(btn.dataset.start) || 0;
      audio.play().catch(() => stopPreview());
      activeBtn = btn;
      btn.classList.add("is-playing");
      btn.textContent = "■";
    });
  });
  if (audio) audio.addEventListener("ended", stopPreview);

  // Upload: vis filnavn, udfyld artist/titel fra "Artist - Titel.mp3" hvis felterne er tomme
  const drop = document.getElementById("dropzone");
  const input = document.getElementById("file-input");
  const text = document.getElementById("dropzone-text");
  const form = document.getElementById("upload-form");
  if (drop && input) {
    ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, () => drop.classList.add("is-over")));
    ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.classList.remove("is-over")));
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      drop.classList.toggle("has-file", !!file);
      if (!file) return;
      text.textContent = `${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
      const stem = file.name.replace(/\.[^.]+$/, "");
      const parts = stem.split(" - ");
      if (parts.length >= 2) {
        const artist = form.querySelector('input[name="artist"]');
        const title = form.querySelector('input[name="title"]');
        if (!artist.value) artist.value = parts[0].trim();
        if (!title.value) title.value = parts.slice(1).join(" - ").trim();
      }
    });
    form.addEventListener("submit", () => {
      const btn = document.getElementById("upload-btn");
      btn.disabled = true;
      btn.textContent = "Uploader…";
    });
  }
})();
