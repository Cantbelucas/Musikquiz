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

  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const csrf = csrfMeta ? csrfMeta.content : "";

  async function postJson(url, body) {
    const res = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(body),
    });
    let data = {};
    try {
      data = await res.json();
    } catch (_) {
      /* ikke-JSON */
    }
    if (!res.ok) throw new Error(data.error || `Serverfejl (${res.status})`);
    return data;
  }

  // Genre-formular: vis kun felter for den valgte kilde
  document.querySelectorAll("[data-source-form]").forEach((wrap) => {
    const form = wrap.closest("form");
    function sync() {
      const checked = form.querySelector('input[name="source"]:checked');
      const source = checked ? checked.value : "";
      wrap.querySelectorAll("[data-source-only]").forEach((el) => {
        el.hidden = el.dataset.sourceOnly !== source;
      });
    }
    form.addEventListener("change", (e) => {
      if (e.target.name === "source") sync();
    });
    sync();
  });

  // Forudfyld cache: én søgeterm pr. kald, så lange hentninger ikke rammer timeouts
  const providerLabel = { itunes: "iTunes", deezer: "Deezer" };
  document.querySelectorAll("[data-cache-url]").forEach((btn) => {
    btn.addEventListener("click", async (event) => {
      event.preventDefault();
      const total = parseInt(btn.dataset.cacheTerms, 10) || 0;
      const statusEl = document.getElementById(btn.dataset.cacheStatus);
      const buttons = document.querySelectorAll("[data-cache-url]");
      if (!total || !statusEl) return;

      buttons.forEach((b) => (b.disabled = true));
      statusEl.hidden = false;
      const isList = statusEl.tagName === "OL";
      const line = statusEl.querySelector(".cache-status");
      if (isList) statusEl.textContent = "";

      function log(text, cls) {
        if (isList) {
          const li = document.createElement("li");
          li.textContent = text;
          if (cls) li.className = cls;
          statusEl.appendChild(li);
        } else if (line) {
          line.textContent = text;
        }
      }

      let failed = 0;
      let last = null;
      for (let i = 0; i < total; i++) {
        if (!isList) log(`Henter søgeterm ${i + 1} af ${total}…`);
        try {
          last = await postJson(btn.dataset.cacheUrl, { term_index: i, refresh: btn.dataset.cacheRefresh === "1" });
          const t = last.terms[0];
          if (t.error) {
            failed++;
            if (isList) log(`“${t.term}”: fejl — ${t.error}`, "is-error");
          } else if (isList) {
            const from = t.provider ? providerLabel[t.provider] : "ingen resultater";
            const how = t.fetched ? "hentet" : "fra cache";
            log(`“${t.term}”: ${t.found} sange (${from}, ${how})`);
          }
        } catch (err) {
          failed++;
          log(`Søgeterm ${i + 1}: ${err.message}`, "is-error");
        }
      }
      const summary = last ? `Færdig: ${last.playable} spor i quizzen.` : "Færdig.";
      log(failed ? `${summary} ${failed} søgetermer fejlede — prøv igen om lidt.` : `${summary} Genindlæser…`, failed ? "is-error" : "is-ok");
      buttons.forEach((b) => (b.disabled = false));
      if (!failed) setTimeout(() => window.location.reload(), 900);
    });
  });

  // Sværhedsgrad pr. cachet spor gemmes med det samme
  document.querySelectorAll("select[data-track-url]").forEach((select) => {
    select.addEventListener("change", async () => {
      select.disabled = true;
      try {
        await postJson(select.dataset.trackUrl, { difficulty: select.value });
        select.classList.add("is-saved");
        setTimeout(() => select.classList.remove("is-saved"), 1200);
      } catch (err) {
        window.alert(`Kunne ikke gemme: ${err.message}`);
      } finally {
        select.disabled = false;
      }
    });
  });
})();
