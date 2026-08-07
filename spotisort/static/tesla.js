/* Tesla page.
 *
 * Written for the car's browser, which is an older Chromium on some builds: no
 * optional chaining, no nullish coalescing, no async/await.
 *
 * Polling is kept deliberately cheap. The server collapses all callers into one
 * upstream request every few seconds, and this page slows down when nothing is
 * playing and stops entirely when the tab is hidden.
 */
(function () {
  "use strict";

  var BASE = location.pathname.replace(/\/+$/, "");           // /tesla/<token>
  var API = BASE.replace("/tesla/", "/api/tesla/");
  var FAST = 5000;    // something is playing
  var SLOW = 20000;   // idle, paused, or erroring
  var timer = null;
  var current = { uri: "", targetsKey: "", duration: 0, progress: 0, at: 0, playing: false };

  function el(id) { return document.getElementById(id); }

  function request(path, options, done) {
    var xhr = new XMLHttpRequest();
    xhr.open(options.method || "GET", path, true);
    xhr.setRequestHeader("Content-Type", "application/json");
    xhr.timeout = 15000;
    xhr.onload = function () {
      var data = {};
      try { data = JSON.parse(xhr.responseText); } catch (e) { data = {}; }
      done(xhr.status >= 200 && xhr.status < 300 ? null : (data.error || "Request failed"), data);
    };
    xhr.onerror = function () { done("No connection to spoti-sort"); };
    xhr.ontimeout = function () { done("spoti-sort did not respond"); };
    xhr.send(options.body ? JSON.stringify(options.body) : null);
  }

  function flash(message, bad) {
    var box = el("flash");
    box.textContent = message;
    box.className = "flash" + (bad ? " bad" : "");
    box.hidden = false;
    clearTimeout(box._t);
    box._t = setTimeout(function () { box.hidden = true; }, 2600);
  }

  function showIdle(title, note, isError) {
    el("player").hidden = true;
    var idle = el("idle");
    idle.hidden = false;
    idle.className = "idle" + (isError ? " error" : "");
    el("idle-title").textContent = title;
    el("idle-note").textContent = note || "";
  }

  function renderTargets(targets, now) {
    // Order matters here: the server sorts favourites and recently-used first, so a
    // change in ranking has to rebuild the row.
    var key = targets.map(function (t) {
      return t.id + ":" + t.name + ":" + (t.favorite ? 1 : 0) +
             ":" + (t.contains ? 1 : 0) + ":" + (t.image || "");
    }).join("|") + "@" + now.uri;
    if (key === current.targetsKey) return;   // avoid rebuilding under the user's finger
    current.targetsKey = key;

    var host = el("targets");
    host.innerHTML = "";
    if (!targets.length) {
      el("note").textContent = "No playlists enabled yet — pick some in spoti-sort first.";
      return;
    }
    el("note").textContent = !now.addable
      ? "This track can't be added to a playlist."
      : "Tap to add · tap again to remove";

    targets.forEach(function (target) {
      var button = document.createElement("button");
      // `contains` means it was already there before this session, so there is no
      // per-add undo record for it — tapping still removes it, just via a
      // remove-every-copy call instead of an undo of a specific position.
      var already = target.contains && !target.added;
      button.className = "target" + (target.added ? " done" : "") +
                         (already ? " already" : "") +
                         (target.favorite ? " fav" : "");
      button.type = "button";
      button.disabled = !now.addable;
      var art = target.image
        ? '<img class="cover" src="' + target.image + '" alt="">'
        : '<span class="cover"></span>';
      button.innerHTML = art +
        '<span class="text"><span class="label"></span><span class="sub"></span></span>' +
        '<span class="star">★</span><span class="tick">✓</span>';
      button.querySelector(".label").textContent = target.name;
      button.querySelector(".sub").textContent = already ? "already in this playlist" : "";
      button.onclick = function () { toggle(button, target, now); };
      host.appendChild(button);
    });
  }

  // One button, both directions: tap to add, tap again to take it back off.
  // Without this an accidental tap was permanent from inside the car.
  function toggle(button, target, now) {
    if (button.disabled) return;
    var wasDone = button.className.indexOf("done") >= 0;
    var wasAlready = button.className.indexOf("already") >= 0;
    var undo = wasDone || wasAlready;
    var wasFav = button.className.indexOf("fav") >= 0 ? " fav" : "";
    button.disabled = true;
    button.className = "target busy" + wasFav;
    request(API + (undo ? "/remove" : "/add"), {
      method: "POST",
      body: { playlist_id: target.id, uri: now.uri }
    }, function (err, data) {
      button.disabled = false;
      if (err) {
        // Leave the button showing the state the playlist is actually in.
        button.className = "target" + (wasDone ? " done" : "") +
                           (wasAlready ? " already" : "") + wasFav;
        flash(err, true);
        return;
      }
      if (undo) {
        button.className = "target" + wasFav;
        button.querySelector(".sub").textContent = "";
        flash("Removed from " + target.name);
      } else if (data.contains) {
        // Was already in the playlist from before; nothing was added, but it can
        // still be tapped again to remove it.
        button.className = "target already" + wasFav;
        button.querySelector(".sub").textContent = "already in this playlist";
        flash("Already in " + target.name);
      } else {
        button.className = "target done" + wasFav;
        flash(data.duplicate ? "Already in " + target.name : "Added to " + target.name);
      }
    });
  }

  function paint(now) {
    el("idle").hidden = true;
    el("player").hidden = false;

    if (now.uri !== current.uri) {
      el("title").textContent = now.name || "Unknown track";
      el("artist").textContent = now.artist || "";
      var art = el("art");
      var fallback = el("art-fallback");
      if (now.image) {
        art.src = now.image;
        art.hidden = false;
        fallback.hidden = true;
      } else {
        art.hidden = true;
        fallback.hidden = false;
      }
      current.uri = now.uri;
    }
    current.duration = now.duration_ms || 0;
    current.progress = now.progress_ms || 0;
    current.at = Date.now();
    current.playing = !!now.is_playing;
    drawProgress();
  }

  // Advance the bar locally between polls rather than polling for it.
  function drawProgress() {
    if (!current.duration) { el("bar-fill").style.width = "0%"; return; }
    var elapsed = current.progress + (current.playing ? Date.now() - current.at : 0);
    var pct = Math.max(0, Math.min(100, (elapsed / current.duration) * 100));
    el("bar-fill").style.width = pct.toFixed(1) + "%";
  }

  function schedule(delay) {
    clearTimeout(timer);
    if (document.hidden) return;      // the car parked this tab; stop calling Spotify
    timer = setTimeout(poll, delay);
  }

  function poll() {
    request(API + "/state", {}, function (err, data) {
      if (err) {
        showIdle("Can't reach spoti-sort", err, true);
        current.uri = "";
        current.targetsKey = "";
        schedule(SLOW);
        return;
      }
      var now = data.now || {};
      if (!now.playing) {
        showIdle("Nothing playing", "Start a track in Spotify and it'll show up here.");
        current.uri = "";
        current.targetsKey = "";
        schedule(SLOW);
        return;
      }
      paint(now);
      renderTargets(data.targets || [], now);
      schedule(now.is_playing ? FAST : SLOW);
    });
  }

  // Dismissal is recorded on the server. The car loses local storage between
  // drives, so a client-side flag would show this banner again every time.
  var onboardDone = el("onboard-done");
  if (onboardDone) {
    onboardDone.onclick = function () {
      onboardDone.disabled = true;
      request(API + "/onboarded", { method: "POST" }, function (err) {
        var panel = el("onboard");
        if (panel) panel.parentNode.removeChild(panel);
        if (err) flash("Saved, but couldn't record it — you may see this again", true);
      });
    };
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) clearTimeout(timer);
    else poll();
  });

  setInterval(drawProgress, 1000);
  poll();
})();
