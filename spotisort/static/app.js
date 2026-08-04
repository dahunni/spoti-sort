(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const state = {
    status: window.__STATUS__ || {},
    playlists: [],
    selected: new Set((window.__STATUS__ || {}).selected || []),
    saved: new Set((window.__STATUS__ || {}).selected || []),
    // Anchor the countdown to a local clock so it ticks smoothly between polls
    // instead of jumping around with request latency.
    skew: 0,
  };

  const toast = (msg, bad) => {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.toggle("bad", !!bad);
    el.hidden = false;
    clearTimeout(el._t);
    el._t = setTimeout(() => { el.hidden = true; }, 3200);
  };

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    let data = {};
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  }

  /* ── countdown ──────────────────────────────────────────────── */

  function humanise(seconds) {
    if (seconds <= 0) return "any moment";
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (d) return `${d}d ${h}h`;
    if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
    if (m) return `${m}m ${String(s).padStart(2, "0")}s`;
    return `${s}s`;
  }

  function tick() {
    const st = state.status;
    if (!st.next_run_at) return;
    const now = Date.now() / 1000 + state.skew;
    const left = st.next_run_at - now;
    $("#countdown").textContent = st.running ? "running…" : humanise(left);
    const when = new Date(st.next_run_at * 1000);
    $("#next-abs").textContent = st.running
      ? "A sort is in progress."
      : `at ${when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · ${when.toLocaleDateString()}`;
  }

  /* ── rendering ──────────────────────────────────────────────── */

  function renderLastRun() {
    const run = state.status.last_run;
    const box = $("#lastrun-body");
    if (!run) { box.textContent = "No run recorded yet."; return; }
    if (run.error) { box.innerHTML = `<span class="dot error"></span>${escape(run.error)}`; return; }

    const when = new Date((run.finished_at || 0) * 1000);
    const head = document.createElement("div");
    const moves = run.moves === 0 ? "everything was already in order"
                : `${run.moves} track${run.moves === 1 ? "" : "s"} moved`;
    head.innerHTML = `<span class="dot ${run.ok ? "ok" : "error"}"></span>` +
      `${when.toLocaleString()} — ${escape(moves)} in ${run.duration || 0}s`;
    box.innerHTML = "";
    box.appendChild(head);
    (run.playlists || []).forEach((p) => {
      const line = document.createElement("div");
      line.className = "runline";
      line.innerHTML = `<span class="dot ${p.status}"></span>` +
        `<span class="name">${escape(p.name || p.playlist_id)}</span>` +
        `<span class="muted small">${escape(p.detail || p.status)}</span>`;
      box.appendChild(line);
    });
    if (run.note) {
      const note = document.createElement("div");
      note.className = "muted small";
      note.textContent = run.note;
      box.appendChild(note);
    }
  }

  const escape = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function renderPlaylists() {
    const host = $("#playlists");
    const needle = ($("#filter").value || "").toLowerCase();
    const list = state.playlists.filter((p) => p.name.toLowerCase().includes(needle));
    host.innerHTML = "";

    if (!list.length) {
      host.innerHTML = `<div class="empty">${state.playlists.length ? "Nothing matches that filter." : "No playlists found on this account."}</div>`;
    }

    list.forEach((p) => {
      const row = document.createElement("label");
      row.className = "pl" + (p.editable ? "" : " locked");
      const art = p.image
        ? `<img class="art" src="${escape(p.image)}" alt="" loading="lazy">`
        : `<span class="art"></span>`;
      const sub = p.editable
        ? `${p.total} track${p.total === 1 ? "" : "s"} · ${escape(p.owner)}`
        : `${p.total} tracks · owned by ${escape(p.owner)} — can't be reordered`;
      row.innerHTML =
        `<input type="checkbox" ${state.selected.has(p.id) ? "checked" : ""} ${p.editable ? "" : "disabled"}>` +
        art +
        `<span class="meta"><span class="name">${escape(p.name)}</span><span class="sub">${sub}</span></span>`;
      row.querySelector("input").addEventListener("change", (ev) => {
        if (ev.target.checked) state.selected.add(p.id); else state.selected.delete(p.id);
        refreshSaveState();
      });
      host.appendChild(row);
    });
    refreshSaveState();
  }

  function sameSet(a, b) {
    return a.size === b.size && [...a].every((x) => b.has(x));
  }

  function refreshSaveState() {
    const dirty = !sameSet(state.selected, state.saved);
    $("#save-selection").disabled = !dirty;
    $("#save-hint").textContent = dirty ? "Unsaved changes" : "";
    $("#selcount").textContent = `${state.selected.size} selected`;
  }

  function renderAccount() {
    const st = state.status;
    const box = $("#account");
    box.hidden = !st.connected || !st.user;
    if (st.user) {
      $("#account-name").textContent = st.user.name || "Connected";
      const img = $("#account-img");
      img.hidden = !st.user.image;
      if (st.user.image) img.src = st.user.image;
    }
  }

  function applyStatus(st, initial) {
    const wasRunning = state.status.running;
    state.status = st;
    state.skew = st.now - Date.now() / 1000;
    if (initial) {
      state.saved = new Set(st.selected || []);
      state.selected = new Set(st.selected || []);
    }
    $("#dashboard").hidden = !st.connected;
    $("#step-connect").hidden = !st.configured || st.connected;
    $("#step-credentials").hidden = !!st.configured;
    $("#running-pill").hidden = !st.running;
    $("#run-now").disabled = st.running;
    $("#interval").value = String(st.interval_minutes);
    $("#order").value = st.order;
    $("#run-on-start").checked = !!st.run_on_start;
    renderAccount();
    renderLastRun();
    tick();
    if (wasRunning && !st.running) toast("Sort finished");
  }

  async function loadPlaylists(force) {
    try {
      const data = await api("/api/playlists" + (force ? "?refresh=1" : ""));
      state.playlists = data.playlists || [];
      // Keep the order stable and put the editable ones on top.
      state.playlists.sort((a, b) => (b.editable - a.editable) || a.name.localeCompare(b.name));
      renderPlaylists();
    } catch (err) {
      $("#playlists").innerHTML = `<div class="empty">${escape(err.message)}</div>`;
    }
  }

  /* ── wiring ─────────────────────────────────────────────────── */

  document.addEventListener("click", async (ev) => {
    const copyBtn = ev.target.closest("[data-copy]");
    if (copyBtn) {
      const text = $(copyBtn.dataset.copy).textContent.trim();
      try {
        await navigator.clipboard.writeText(text);
        toast("Copied");
      } catch (_) {
        toast("Copy failed — select it manually", true);
      }
    }
  });

  const credForm = $("#credentials-form");
  if (credForm) {
    credForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fd = new FormData(credForm);
      try {
        await api("/api/credentials", {
          method: "POST",
          body: { client_id: fd.get("client_id"), client_secret: fd.get("client_secret") },
        });
        location.reload();
      } catch (err) { toast(err.message, true); }
    });
  }

  const dash = $("#dashboard");
  if (dash) {
    $("#refresh").addEventListener("click", () => loadPlaylists(true));
    $("#filter").addEventListener("input", renderPlaylists);

    $("#save-selection").addEventListener("click", async (ev) => {
      ev.target.disabled = true;
      try {
        await api("/api/selection", { method: "POST", body: { playlists: [...state.selected] } });
        state.saved = new Set(state.selected);
        toast("Selection saved");
      } catch (err) { toast(err.message, true); }
      refreshSaveState();
    });

    const saveSetting = async (body, message) => {
      try {
        await api("/api/settings", { method: "POST", body });
        toast(message);
        await poll();
      } catch (err) { toast(err.message, true); }
    };
    $("#interval").addEventListener("change", (ev) =>
      saveSetting({ interval_minutes: Number(ev.target.value) }, "Schedule updated"));
    $("#order").addEventListener("change", (ev) =>
      saveSetting({ order: ev.target.value }, "Sort order updated"));
    $("#run-on-start").addEventListener("change", (ev) =>
      saveSetting({ run_on_start: ev.target.checked }, "Saved"));

    $("#run-now").addEventListener("click", async (ev) => {
      ev.target.disabled = true;
      try {
        await api("/api/run", { method: "POST" });
        toast("Sorting started");
      } catch (err) {
        toast(err.message, true);
        ev.target.disabled = false;
      }
      setTimeout(poll, 400);
    });

    $("#disconnect").addEventListener("click", async () => {
      if (!confirm("Forget the stored Spotify token? You'll need to authorise again.")) return;
      try {
        await api("/api/disconnect", { method: "POST" });
        location.reload();
      } catch (err) { toast(err.message, true); }
    });
  }

  async function poll() {
    try {
      applyStatus(await api("/api/status"), false);
    } catch (_) { /* transient; the next tick retries */ }
  }

  applyStatus(state.status, true);
  if (state.status.connected) loadPlaylists(false);
  setInterval(tick, 1000);
  // Poll faster while a sort is in flight so the result appears promptly.
  setInterval(() => poll(), 5000);
})();
