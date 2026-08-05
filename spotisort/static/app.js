(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const ORDER_LABELS = { newest_first: "newest first", oldest_first: "oldest first" };

  // Handed over in a JSON data block rather than an inline script, because the CSP
  // is `script-src 'self'`.
  let boot = { status: {}, csrf: "" };
  try {
    boot = JSON.parse(document.getElementById("bootstrap").textContent);
  } catch (_) { /* fall through to the empty default; /api/status refills it */ }
  let csrfToken = boot.csrf || "";

  // Selection is a Map of playlist id -> sort order, so each playlist carries its own.
  // id -> {sort, add, order}. The two roles are independent: a playlist can be a
  // Tesla quick-add target without being sorted, and vice versa.
  const toMap = (entries) => new Map((entries || []).map((e) =>
    [e.id, { sort: e.sort !== false, add: e.add !== false, order: e.order }]));

  const toFavorites = (entries) =>
    new Set((entries || []).filter((e) => e.favorite).map((e) => e.id));

  const state = {
    status: boot.status || {},
    playlists: [],
    selected: toMap((boot.status || {}).entries),
    saved: toMap((boot.status || {}).entries),
    favorites: toFavorites((boot.status || {}).entries),
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
      ...opts,
      headers: {
        "Content-Type": "application/json",
        // Session-cookie auth needs this on every state-changing request.
        "X-CSRF-Token": csrfToken,
        ...(opts.headers || {}),
      },
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
      const order = p.status === "ok" && ORDER_LABELS[p.order] ? ` · ${ORDER_LABELS[p.order]}` : "";
      line.innerHTML = `<span class="dot ${p.status}"></span>` +
        `<span class="name">${escape(p.name || p.playlist_id)}</span>` +
        `<span class="muted small">${escape((p.detail || p.status) + order)}</span>`;
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
      const chosen = state.selected.get(p.id) || null;
      const sortOn = !!(chosen && chosen.sort);
      const addOn = !!(chosen && chosen.add);
      // Plain <div> with explicit toggle buttons. An earlier version wrapped the
      // whole row in a <label>, which made every click on the dropdown or star
      // toggle selection instead.
      const row = document.createElement("div");
      row.className = "pl" + (p.editable ? "" : " locked") +
                      (sortOn || addOn ? " on" : "");
      const art = p.image
        ? `<img class="art" src="${escape(p.image)}" alt="" loading="lazy">`
        : `<span class="art"></span>`;
      const sub = p.editable
        ? `${p.total} track${p.total === 1 ? "" : "s"} · ${escape(p.owner)}`
        : `${p.total} tracks · owned by ${escape(p.owner)} — can't be reordered`;
      const order = (chosen && chosen.order) || state.status.default_order || "newest_first";
      const fav = state.favorites.has(p.id);
      row.innerHTML =
        art +
        `<span class="meta"><span class="name">${escape(p.name)}</span><span class="sub">${sub}</span></span>` +
        `<span class="roles">` +
        `<button type="button" class="chip sort${sortOn ? " on" : ""}" role="switch" ` +
        `aria-checked="${sortOn}" ${p.editable ? "" : "disabled"} ` +
        `title="Keep this playlist in date order">Sort</button>` +
        `<select class="order" ${sortOn ? "" : "hidden"} aria-label="Sort order for ${escape(p.name)}">` +
        `<option value="newest_first"${order === "newest_first" ? " selected" : ""}>Newest first</option>` +
        `<option value="oldest_first"${order === "oldest_first" ? " selected" : ""}>Oldest first</option>` +
        `</select>` +
        `<button type="button" class="chip add${addOn ? " on" : ""}" role="switch" ` +
        `aria-checked="${addOn}" ${p.editable ? "" : "disabled"} ` +
        `title="Offer this playlist on the Tesla page">Car</button>` +
        `<button type="button" class="star${fav ? " on" : ""}" ${addOn ? "" : "hidden"} ` +
        `title="Show first on the Tesla page" aria-label="Favourite ${escape(p.name)}">${fav ? "\u2605" : "\u2606"}</button>` +
        `</span>`;

      const sortChip = row.querySelector(".chip.sort");
      const addChip = row.querySelector(".chip.add");
      const select = row.querySelector("select");
      const star = row.querySelector(".star");

      const write = () => {
        const on = sortChip.classList.contains("on");
        const car = addChip.classList.contains("on");
        if (!on && !car) state.selected.delete(p.id);
        else state.selected.set(p.id, { sort: on, add: car, order: select.value });
        select.hidden = !on;
        star.hidden = !car;
        row.classList.toggle("on", on || car);
        refreshSaveState();
      };

      const toggleChip = (chip) => {
        const next = !chip.classList.contains("on");
        chip.classList.toggle("on", next);
        chip.setAttribute("aria-checked", String(next));
        write();
      };
      sortChip.addEventListener("click", () => toggleChip(sortChip));
      addChip.addEventListener("click", () => toggleChip(addChip));
      select.addEventListener("change", write);

      star.addEventListener("click", async () => {
        const next = !state.favorites.has(p.id);
        if (next) state.favorites.add(p.id); else state.favorites.delete(p.id);
        star.classList.toggle("on", next);
        star.textContent = next ? "\u2605" : "\u2606";
        try {
          await api("/api/favorite", { method: "POST", body: { playlist_id: p.id, favorite: next } });
        } catch (err) {
          // Favourites save immediately, so a failure has to roll back.
          if (next) state.favorites.delete(p.id); else state.favorites.add(p.id);
          star.classList.toggle("on", !next);
          star.textContent = next ? "\u2606" : "\u2605";
          toast(err.message, true);
        }
      });
      host.appendChild(row);
    });
    refreshSaveState();
  }

  function sameSelection(a, b) {
    if (a.size !== b.size) return false;
    return [...a].every(([id, v]) => {
      const other = b.get(id);
      return other && other.sort === v.sort && other.add === v.add && other.order === v.order;
    });
  }

  function refreshSaveState() {
    const dirty = !sameSelection(state.selected, state.saved);
    $("#save-selection").disabled = !dirty;
    $("#save-hint").textContent = dirty ? "Unsaved changes" : "";
    let sorted = 0, car = 0;
    state.selected.forEach((v) => { if (v.sort) sorted++; if (v.add) car++; });
    $("#selcount").textContent = `${sorted} sorted · ${car} on car`;
  }

  const entriesFromSelection = () =>
    [...state.selected].map(([id, v]) => ({
      id, order: v.order, sort: v.sort, add: v.add,
      favorite: state.favorites.has(id),
    }));

  function renderTeslaLink(st) {
    const on = !!st.tesla_url;
    $("#tesla-on").hidden = !on;
    $("#tesla-off").hidden = on;
    if (on) {
      $("#tesla-url").textContent = st.tesla_url;
      $("#tesla-open").href = st.tesla_url;
    }
    // The link is only reachable from the car if the base address is right, and the
    // loopback default never is.
    const loopback = /^https?:\/\/(127\.0\.0\.1|localhost)\b/.test(st.tesla_url || "");
    $("#tesla-hint").textContent = on && loopback
      ? "This link points at 127.0.0.1, which the car can't reach. Set the address above to this machine's LAN address or hostname."
      : "";
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
      state.saved = toMap(st.entries);
      state.selected = toMap(st.entries);
      state.favorites = toFavorites(st.entries);
    }
    $("#dashboard").hidden = !st.connected;
    $("#step-connect").hidden = !st.configured || st.connected;
    $("#step-credentials").hidden = !!st.configured;
    $("#running-pill").hidden = !st.running;
    $("#run-now").disabled = st.running;
    $("#interval").value = String(st.interval_minutes);
    $("#default-order").value = st.default_order;
    $("#run-on-start").checked = !!st.run_on_start;
    $("#reauth").hidden = !st.connected || !(st.missing_scopes || []).length;
    $("#auth-warning").hidden = !st.auth_warning;
    $("#auth-on").hidden = !st.auth_enabled || st.auth_from_env;
    $("#auth-env").hidden = !st.auth_from_env;
    $("#password-form").hidden = !!st.auth_from_env;
    $("#current-wrap").hidden = !st.auth_enabled || st.auth_from_env;
    renderTeslaLink(st);
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

  // Public address — lives on the setup card, so it must work before connecting.
  const publicUrlBtn = $("#save-public-url");
  if (publicUrlBtn) {
    publicUrlBtn.addEventListener("click", async () => {
      try {
        await api("/api/settings", { method: "POST", body: { public_url: $("#public-url").value } });
        location.reload();  // the redirect URI shown above is rendered server-side
      } catch (err) { toast(err.message, true); }
    });
  }

  const passwordForm = $("#password-form");
  if (passwordForm) {
    passwordForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fd = new FormData(passwordForm);
      const next = String(fd.get("password") || "");
      if (!next && !confirm("Remove the password and leave this page open to anyone who can reach it?")) return;
      try {
        const data = await api("/api/password", {
          method: "POST",
          body: { password: next, current: fd.get("current") || "" },
        });
        if (data.csrf_token) csrfToken = data.csrf_token;
        passwordForm.reset();
        toast(data.enabled ? "Password set" : "Password removed");
        await poll();
      } catch (err) { toast(err.message, true); }
    });
  }

  const teslaAction = async (action, confirmText) => {
    if (confirmText && !confirm(confirmText)) return;
    try {
      const data = await api("/api/tesla-link", { method: "POST", body: { action } });
      state.status.tesla_url = data.tesla_url;
      renderTeslaLink(state.status);
      toast(data.tesla_url ? "Link ready" : "Link turned off");
    } catch (err) { toast(err.message, true); }
  };
  if ($("#tesla-enable")) {
    $("#tesla-enable").addEventListener("click", () => teslaAction("enable"));
    $("#tesla-regen").addEventListener("click", () =>
      teslaAction("regenerate", "Generate a new link? The one saved in the car will stop working."));
    $("#tesla-disable").addEventListener("click", () =>
      teslaAction("disable", "Turn off the Tesla page? The saved link will stop working."));
  }

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
        await api("/api/selection", { method: "POST", body: { playlists: entriesFromSelection() } });
        state.saved = new Map(state.selected);
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
    $("#default-order").addEventListener("change", (ev) =>
      saveSetting({ default_order: ev.target.value }, "Default order updated"));
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
