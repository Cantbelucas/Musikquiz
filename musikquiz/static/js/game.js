// Spilskærm: afspilning af klip i trin, gæt, skip og resultat.
(function () {
  "use strict";

  const root = document.getElementById("game");
  if (!root) return;

  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const urls = {
    state: root.dataset.stateUrl,
    guess: root.dataset.guessUrl,
    skip: root.dataset.skipUrl,
    next: root.dataset.nextUrl,
  };
  const $ = (id) => document.getElementById(id);
  const els = {
    index: $("hud-index"),
    total: $("hud-total"),
    score: $("hud-score"),
    round: $("round"),
    stages: $("stages"),
    playBtn: $("play-btn"),
    ring: $("ring-progress"),
    playSeconds: $("play-seconds"),
    hint: $("play-hint"),
    options: $("options"),
    skipBtn: $("skip-btn"),
    reveal: $("reveal"),
    verdict: $("reveal-verdict"),
    revealTitle: $("reveal-title"),
    revealArtist: $("reveal-artist"),
    listenBtn: $("listen-btn"),
    nextBtn: $("next-btn"),
    finished: $("finished"),
    finalScore: $("final-score"),
    finalMax: $("final-max"),
    finalSub: $("final-sub"),
    hsForm: $("highscore-form"),
    hsSaved: $("highscore-saved"),
    results: $("results"),
    error: $("game-error"),
    audio: $("audio"),
  };
  const RING_LENGTH = 2 * Math.PI * 54;
  els.ring.style.strokeDasharray = String(RING_LENGTH);

  let state = null;
  let busy = false;
  let clip = null; // aktivt klip: { start, end, secs, free, raf, timer }
  let renderedSong = -1;

  // ---------------------------------------------------------------- API
  async function api(url, body) {
    const opts = { credentials: "same-origin", headers: { Accept: "application/json" } };
    if (body !== undefined) {
      opts.method = "POST";
      opts.headers["Content-Type"] = "application/json";
      opts.headers["X-CSRF-Token"] = csrf;
      opts.body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(url, opts);
    } catch (_) {
      throw new Error("Ingen forbindelse til serveren. Prøv igen.");
    }
    let data = {};
    try {
      data = await res.json();
    } catch (_) {
      /* ikke-JSON svar */
    }
    if (!res.ok) throw new Error(data.error || "Noget gik galt. Genindlæs siden og prøv igen.");
    return data;
  }

  // ---------------------------------------------------------- afspilning
  function currentSeconds() {
    return state.stages[state.stage].seconds;
  }

  function setRing(progress) {
    els.ring.style.strokeDashoffset = String(RING_LENGTH * (1 - progress));
  }

  function setPlayingUi(playing) {
    els.playBtn.classList.toggle("is-playing", playing);
    els.playBtn.setAttribute("aria-label", playing ? "Stop" : "Afspil klip");
    if (!playing) els.playBtn.classList.remove("is-loading");
    updateHint();
  }

  function playClip() {
    if (!state || state.status === "finished") return;
    if (clip) {
      stopClip();
      return;
    }
    hideError();
    const free = state.status === "revealed";
    const start = state.start_at || 0;
    const secs = currentSeconds();
    clip = { start, secs, free, end: free ? Infinity : start + secs, raf: 0, timer: 0 };
    try {
      els.audio.currentTime = start;
    } catch (_) {
      /* sættes igen når metadata er indlæst */
    }
    setPlayingUi(true);
    const promise = els.audio.play();
    if (promise) {
      promise.catch((err) => {
        if (err && err.name === "AbortError") return;
        stopClip();
        showError("Lyden kunne ikke afspilles. Tryk på knappen igen.");
      });
    }
    scheduleStop();
    drawRing();
  }

  // Stop styres af timere ud fra lydens faktiske position (virker også når
  // browseren ikke tegner, fx i en baggrundsfane). rAF bruges kun til ringen.
  function scheduleStop() {
    if (!clip || clip.free) return;
    clearTimeout(clip.timer);
    const t = els.audio.currentTime;
    if (!els.audio.paused && t >= clip.end - 0.015) {
      stopClip();
      return;
    }
    const remaining = els.audio.paused || t < clip.start ? 0.05 : clip.end - t;
    clip.timer = setTimeout(scheduleStop, Math.max(5, Math.min(remaining * 1000, 250)));
  }

  function drawRing() {
    if (!clip) return;
    const t = els.audio.currentTime;
    if (clip.free) {
      const d = els.audio.duration;
      setRing(Number.isFinite(d) && d > clip.start ? (t - clip.start) / (d - clip.start) : 0);
    } else {
      setRing(Math.max(0, Math.min(1, (t - clip.start) / clip.secs)));
    }
    clip.raf = requestAnimationFrame(drawRing);
  }

  function stopClip() {
    if (!clip) return;
    cancelAnimationFrame(clip.raf);
    clearTimeout(clip.timer);
    clip = null;
    els.audio.pause();
    setRing(0);
    setPlayingUi(false);
  }

  function loadAudio(url) {
    stopClip();
    if (els.audio.dataset.src === url) return;
    els.audio.dataset.src = url;
    els.audio.src = url;
    els.audio.load();
  }

  els.audio.addEventListener("loadedmetadata", () => {
    if (clip && els.audio.currentTime < clip.start) els.audio.currentTime = clip.start;
  });
  els.audio.addEventListener("waiting", () => clip && els.playBtn.classList.add("is-loading"));
  els.audio.addEventListener("playing", () => {
    els.playBtn.classList.remove("is-loading");
    scheduleStop();
  });
  els.audio.addEventListener("ended", stopClip);
  els.audio.addEventListener("pause", () => {
    // Fx afbrudt af et opkald på mobilen
    if (clip && els.audio.currentTime < clip.end - 0.05) stopClip();
  });
  els.audio.addEventListener("error", () => {
    if (!els.audio.dataset.src) return;
    stopClip();
    showError("Lydfilen kunne ikke indlæses.");
  });

  // ------------------------------------------------------------ handlinger
  async function act(url, body) {
    if (busy) return null;
    busy = true;
    stopClip();
    hideError();
    updateControls();
    try {
      state = await api(url, body);
      return state;
    } catch (err) {
      showError(err.message);
      return null;
    } finally {
      busy = false;
      if (state) render();
    }
  }

  async function guess(optionId) {
    const data = await act(urls.guess, { option: optionId });
    if (!data) return;
    if (data.correct) {
      bump(els.score);
    } else if (data.status === "playing") {
      const wrong = els.options.querySelector(`[data-id="${optionId}"]`);
      if (wrong) {
        wrong.classList.remove("is-shake");
        void wrong.offsetWidth;
        wrong.classList.add("is-shake");
      }
      playClip();
    }
  }

  async function skip() {
    const data = await act(urls.skip, {});
    if (data && data.status === "playing") playClip();
  }

  async function next() {
    const data = await act(urls.next, {});
    if (data && data.status === "playing") playClip();
    if (data && data.status === "finished") window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // --------------------------------------------------------------- render
  function render() {
    els.index.textContent = String(Math.min(state.index + 1, state.total));
    els.total.textContent = String(state.total);
    els.score.textContent = String(state.score);

    if (state.status === "finished") {
      stopClip();
      els.audio.removeAttribute("src");
      delete els.audio.dataset.src;
      els.round.hidden = true;
      renderFinished();
      els.finished.hidden = false;
      return;
    }

    els.finished.hidden = true;
    els.round.hidden = false;
    els.round.classList.toggle("is-revealed", state.status === "revealed");
    loadAudio(state.audio_url);
    renderStages();
    renderOptions();
    renderReveal();
    updateControls();
    updateHint();
  }

  function renderStages() {
    if (!els.stages.children.length) {
      state.stages.forEach((s) => {
        const li = document.createElement("li");
        li.className = "stage";
        li.innerHTML = '<span class="stage-sec"></span><span class="stage-pts"></span>';
        li.querySelector(".stage-sec").textContent = `${s.seconds}s`;
        li.querySelector(".stage-pts").textContent = `${s.points}p`;
        els.stages.appendChild(li);
      });
    }
    const solvedAt = state.status === "revealed" && state.reveal && state.reveal.solved ? state.stage : -1;
    [...els.stages.children].forEach((li, i) => {
      const isCurrent = state.status === "playing" ? i === state.stage : i === solvedAt;
      li.classList.toggle("is-current", isCurrent);
      li.classList.toggle("is-done", i < state.stage || (state.status === "revealed" && solvedAt === -1));
      li.setAttribute("aria-current", isCurrent ? "step" : "false");
    });
    els.playSeconds.textContent = state.status === "playing" ? `${currentSeconds()}s` : "♪";
  }

  function renderOptions() {
    if (renderedSong !== state.index) {
      renderedSong = state.index;
      els.options.textContent = "";
      state.options.forEach((opt, i) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "option";
        btn.dataset.id = String(opt.id);
        btn.innerHTML =
          '<span class="option-key"></span><span class="option-text"><span class="option-title"></span><span class="option-artist"></span></span>';
        btn.querySelector(".option-key").textContent = String(i + 1);
        btn.querySelector(".option-title").textContent = opt.title;
        btn.querySelector(".option-artist").textContent = opt.artist;
        btn.addEventListener("click", () => guess(opt.id));
        els.options.appendChild(btn);
      });
    }
    state.options.forEach((opt) => {
      const btn = els.options.querySelector(`[data-id="${opt.id}"]`);
      if (!btn) return;
      btn.classList.toggle("is-wrong", opt.wrong);
      btn.classList.toggle("is-correct", state.status === "revealed" && opt.id === state.answer_id);
    });
  }

  function renderReveal() {
    const r = state.status === "revealed" ? state.reveal : null;
    els.reveal.hidden = !r;
    els.skipBtn.hidden = !!r;
    if (!r) return;
    els.reveal.classList.toggle("is-ok", r.solved);
    els.reveal.classList.toggle("is-miss", !r.solved);
    els.verdict.textContent = r.solved
      ? `Rigtigt! +${r.points} point på ${r.seconds} sek`
      : "Ikke denne gang — svaret var";
    els.revealTitle.textContent = r.title;
    els.revealArtist.textContent = r.artist;
    els.nextBtn.textContent = state.is_last ? "Se resultat" : "Næste sang";
  }

  function updateControls() {
    if (!state || state.status === "finished") return;
    const playing = state.status === "playing";
    els.options.querySelectorAll(".option").forEach((btn) => {
      const opt = state.options.find((o) => String(o.id) === btn.dataset.id);
      btn.disabled = busy || !playing || (opt && opt.wrong);
    });
    els.skipBtn.disabled = busy || !playing;
    els.nextBtn.disabled = busy;
    const lastStage = state.stage === state.stages.length - 1;
    els.skipBtn.textContent = lastStage ? "Giv op — vis svaret" : `Skip — hør ${state.stages[state.stage + 1].seconds} sekunder`;
  }

  function updateHint() {
    if (!state || state.status === "finished") return;
    if (clip) {
      els.hint.textContent = clip.free ? "Afspiller… tryk for at stoppe" : "Lyt godt efter…";
    } else if (state.status === "revealed") {
      els.hint.textContent = "Tryk for at høre mere af sangen";
    } else {
      const s = currentSeconds();
      els.hint.textContent = `Tryk for at høre ${s} ${s === 1 ? "sekund" : "sekunder"}`;
    }
  }

  function renderFinished() {
    const solved = state.results.filter((r) => r.solved).length;
    els.finalScore.textContent = String(state.score);
    els.finalMax.textContent = String(state.max_score);
    els.finalSub.textContent = `${solved} af ${state.total} rigtige`;
    els.hsForm.hidden = state.highscore_saved;
    els.hsSaved.hidden = !state.highscore_saved;

    els.results.textContent = "";
    state.results.forEach((r) => {
      const li = document.createElement("li");
      li.className = `result ${r.solved ? "is-ok" : "is-miss"}`;
      li.innerHTML =
        '<span class="result-song"><span class="result-title"></span><br><span class="result-artist"></span></span>' +
        '<span class="result-score"><span class="result-points"></span><span class="result-time"></span></span>';
      li.querySelector(".result-title").textContent = r.title;
      li.querySelector(".result-artist").textContent = r.artist;
      li.querySelector(".result-points").textContent = `${r.points}p`;
      li.querySelector(".result-time").textContent = r.solved ? `${r.seconds} sek` : "ikke gættet";
      els.results.appendChild(li);
    });
  }

  function bump(el) {
    el.classList.remove("bump");
    void el.offsetWidth;
    el.classList.add("bump");
  }

  function showError(message) {
    els.error.textContent = message;
    els.error.hidden = false;
  }

  function hideError() {
    els.error.hidden = true;
  }

  // ---------------------------------------------------------------- events
  els.playBtn.addEventListener("click", playClip);
  els.listenBtn.addEventListener("click", playClip);
  els.skipBtn.addEventListener("click", skip);
  els.nextBtn.addEventListener("click", next);

  document.addEventListener("keydown", (e) => {
    if (!state || e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (state.status === "playing" && /^[1-9]$/.test(e.key)) {
      const opt = state.options[Number(e.key) - 1];
      if (opt && !opt.wrong) guess(opt.id);
    } else if ((e.key === " " || e.key === "p") && tag !== "button") {
      e.preventDefault();
      playClip();
    } else if (e.key === "s" && state.status === "playing") {
      skip();
    } else if ((e.key === "Enter" || e.key === "n") && state.status === "revealed" && tag !== "button") {
      next();
    }
  });

  api(urls.state)
    .then((data) => {
      state = data;
      render();
    })
    .catch((err) => showError(err.message));
})();
