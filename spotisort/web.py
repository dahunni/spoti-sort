"""Flask app: configuration UI, OAuth callback, and the JSON API the page polls."""

from __future__ import annotations

import functools
import hmac
import logging
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   session, url_for)
from spotipy.exceptions import SpotifyException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import __version__
from .config import Config, clamp_interval, clean_public_url
from .scheduler import Scheduler
from .security import (LoginLimiter, client_key, csrf_ok, new_csrf_token,
                       trust_proxy)
from .sorter import NEWEST_FIRST, OLDEST_FIRST
from .spotify import NotAuthenticated, SpotifyClient, make_oauth, missing_scopes

log = logging.getLogger(__name__)

# Playlist metadata changes rarely and the list can be long; refetching it on every
# poll would burn the rate limit for nothing.
PLAYLIST_CACHE_TTL = 300

# The Tesla page polls; this collapses bursts (and several open tabs) into at most
# one upstream call per interval. Short enough that a track change still shows up
# promptly.
NOW_PLAYING_TTL = 4.0

# Double-tapping "add" on a car touchscreen is easy. Spotify happily stores the
# duplicate, so remember recent adds and refuse the repeat.
ADD_GUARD_SECONDS = 900

# How long a playlist's set of track URIs is trusted before being re-read. Reading
# one costs a call per 100 tracks, so this is deliberately generous; our own adds
# and removals update the set in place, and a sort run refreshes it for free.
MEMBERSHIP_TTL = 900.0


class App:
    def __init__(self, config: Config):
        self.config = config
        self._client: Optional[SpotifyClient] = None
        self._playlists: List[Dict[str, Any]] = []
        self._playlists_at = 0.0
        self._auth_error = ""
        self._now: Dict[str, Any] = {}
        self._now_at = 0.0
        self._recent_adds: Dict[str, float] = {}
        # playlist id -> (fetched_at, set of track URIs)
        self._members: Dict[str, Any] = {}

        state = config.load_state()
        self.scheduler = Scheduler(self.run_sort, config.interval_minutes, self._store_run)
        self.scheduler.last_run = state.get("last_run")

    # -- spotify -----------------------------------------------------------

    def oauth(self):
        if not self.config.has_credentials:
            raise NotAuthenticated("client id/secret not configured")
        return make_oauth(self.config.client_id, self.config.client_secret,
                          self.config.redirect_uri, self.config.token_path)

    def client(self) -> SpotifyClient:
        if self._client is None:
            self._client = SpotifyClient.from_cache(self.oauth())
        return self._client

    def reset_client(self) -> None:
        self._client = None
        self._playlists = []
        self._playlists_at = 0.0

    @property
    def connected(self) -> bool:
        try:
            self.client()
            return True
        except (NotAuthenticated, SpotifyException, OSError):
            return False

    def playlists(self, force: bool = False) -> List[Dict[str, Any]]:
        if force or not self._playlists or time.time() - self._playlists_at > PLAYLIST_CACHE_TTL:
            self._playlists = self.client().my_playlists()
            self._playlists_at = time.time()
        return self._playlists

    # -- tesla page --------------------------------------------------------

    def missing_scopes(self) -> List[str]:
        """Permissions this version needs that the stored token predates."""
        try:
            token = self.oauth().cache_handler.get_cached_token()
        except (NotAuthenticated, OSError):
            return []
        return missing_scopes(token) if token else []

    def now_playing(self) -> Dict[str, Any]:
        if time.time() - self._now_at < NOW_PLAYING_TTL:
            return self._now
        self._now = self.client().now_playing()
        self._now_at = time.time()
        return self._now

    def targets(self) -> List[Dict[str, Any]]:
        """Playlists the Tesla page may add to: the selected, editable ones.

        Ordered for a screen you glance at while parked: starred first, then the
        ones you actually used most recently, then everything else. That keeps the
        playlist you keep reaching for under your thumb without any manual sorting.
        """
        known: Dict[str, Dict[str, Any]] = {}
        try:
            known = {p["id"]: p for p in self.playlists()}
        except (NotAuthenticated, SpotifyException, OSError):
            pass
        out = []
        for position, entry in enumerate(self.config.add_entries):
            meta = known.get(entry["id"])
            if meta and not meta.get("editable"):
                continue
            out.append({
                "id": entry["id"],
                "name": (meta or {}).get("name") or entry["id"],
                "image": (meta or {}).get("image"),
                "order": entry.get("order"),
                "favorite": bool(entry.get("favorite")),
                "last_used": float(entry.get("last_used") or 0.0),
                "position": position,
            })
        out.sort(key=lambda t: (not t["favorite"], -t["last_used"], t["position"]))
        return out

    # -- duplicate detection ----------------------------------------------

    def members(self, playlist_id: str) -> Optional[set]:
        """Track URIs in a playlist, cached. None when it couldn't be read."""
        cached = self._members.get(playlist_id)
        if cached and time.time() - cached[0] < MEMBERSHIP_TTL:
            return cached[1]
        try:
            uris = self.client().playlist_uris(playlist_id)
        except (NotAuthenticated, SpotifyException, OSError) as exc:
            log.warning("could not read %s for duplicate check: %s", playlist_id, exc)
            # Keep a stale set rather than claiming nothing is a duplicate.
            return cached[1] if cached else None
        self._members[playlist_id] = (time.time(), uris)
        return uris

    def remember_member(self, playlist_id: str, uri: str, present: bool) -> None:
        """Keep the cached set in step with a write we just made."""
        cached = self._members.get(playlist_id)
        if not cached:
            return
        at, uris = cached
        if present:
            uris.add(uri)
        else:
            uris.discard(uri)
        self._members[playlist_id] = (at, uris)

    def refresh_members_from_run(self, playlist_id: str, uris: set) -> None:
        self._members[playlist_id] = (time.time(), uris)

    def _add_key(self, playlist_id: str, uri: str) -> str:
        return "%s|%s" % (playlist_id, uri)

    def _prune_adds(self) -> None:
        now = time.time()
        for key, info in list(self._recent_adds.items()):
            if now - info.get("at", 0) > ADD_GUARD_SECONDS:
                del self._recent_adds[key]

    def recent_add(self, playlist_id: str, uri: str) -> Optional[Dict[str, Any]]:
        """The undo record for a recent add, if there is one."""
        self._prune_adds()
        return self._recent_adds.get(self._add_key(playlist_id, uri))

    def remember_add(self, playlist_id: str, uri: str, info: Dict[str, Any]) -> None:
        record = dict(info)
        record["at"] = time.time()
        self._recent_adds[self._add_key(playlist_id, uri)] = record

    def forget_add(self, playlist_id: str, uri: str) -> None:
        self._recent_adds.pop(self._add_key(playlist_id, uri), None)

    # -- runs --------------------------------------------------------------

    def run_sort(self) -> Dict[str, Any]:
        entries = self.config.sort_entries
        if not entries:
            return {"started_at": time.time(), "finished_at": time.time(), "duration": 0,
                    "moves": 0, "ok": True, "playlists": [],
                    "note": "no playlists are set to be sorted"}
        try:
            client = self.client()
        except NotAuthenticated as exc:
            return {"started_at": time.time(), "finished_at": time.time(), "duration": 0,
                    "moves": 0, "ok": False, "playlists": [], "error": str(exc)}
        run = client.sort_all(entries)
        for result in run.playlists:
            if result.uris is not None:
                self.refresh_members_from_run(result.playlist_id, result.uris)
        return run.as_dict()

    def _store_run(self, result: Dict[str, Any]) -> None:
        self.config.save_state({"last_run": result})

    # -- view model --------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        connected = self.connected
        user = None
        if connected:
            try:
                me = self.client().me()
                user = {"name": me.get("display_name") or me.get("id"),
                        "image": (me.get("images") or [{}])[-1].get("url")}
            except (SpotifyException, NotAuthenticated, OSError):
                connected = False

        entries = self.config.entries
        return {
            "version": __version__,
            "configured": self.config.has_credentials,
            "credentials_from_env": self.config.credentials_from_env,
            "connected": connected,
            "user": user,
            "auth_error": self._auth_error,
            "redirect_uri": self.config.redirect_uri,
            "redirect_uri_from_env": self.config.redirect_uri_from_env,
            "redirect_uri_is_loopback": self.config.redirect_uri_is_loopback,
            "port": self.config.port,
            "public_url": self.config.public_url,
            "public_url_parts": self.config.public_url_parts,
            "public_url_from_env": self.config.public_url_from_env,
            "missing_scopes": self.missing_scopes() if connected else [],
            "tesla_url": self.config.tesla_url,
            # Only ever reaches an authenticated caller: `status()` is behind
            # `login_required` on both the page bootstrap and /api/status.
            "pairing_code": self.config.pairing_code,
            "pairing_expires_at": self.config.pairing_expires_at,
            "auth_enabled": self.config.auth_enabled,
            "auth_from_env": self.config.ui_password_from_env,
            # Reachable beyond this machine but with no password on the door.
            "auth_warning": self.config.is_public and not self.config.auth_enabled,
            "now": time.time(),
            "next_run_at": self.scheduler.next_run_at,
            "running": self.scheduler.running,
            "interval_minutes": self.config.interval_minutes,
            "default_order": self.config.order,
            "run_on_start": self.config.run_on_start,
            "entries": entries,
            "last_run": self.scheduler.last_run,
        }


def create_app(config: Optional[Config] = None) -> Flask:
    config = config or Config()
    app = Flask(__name__)
    app.secret_key = config.secret_key()
    state = App(config)
    app.extensions["spotisort"] = state
    limiter = LoginLimiter()

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        # Lax still sends the cookie on the top-level GET back from Spotify's
        # redirect, but not on cross-site POSTs.
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.base_url.startswith("https://"),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        MAX_CONTENT_LENGTH=256 * 1024,
    )

    hops = trust_proxy()
    if hops:
        # Without this, a reverse proxy's https is invisible and both the secure
        # cookie flag and any generated URL come out as http.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    @app.context_processor
    def inject_version():
        # Single source of truth in spotisort/__init__.py; also the Docker tag.
        return {"version": __version__}

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            # Everything is served from this origin; album art comes from Spotify's
            # CDN over https, and the mock uses a data: URI.
            "default-src 'self'; img-src 'self' https: data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'",
        )
        if request.path.startswith(("/tesla/", "/api/tesla/")):
            # The URL is a credential; keep it out of shared caches and crawlers.
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        return response

    def authed() -> bool:
        return not config.auth_enabled or session.get("ui_ok") is True

    def ensure_csrf() -> str:
        token = session.get("csrf")
        if not token:
            token = new_csrf_token()
            session["csrf"] = token
        return token

    def login_required(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            if not authed():
                if request.path.startswith("/api/"):
                    return jsonify({"error": "unauthorised"}), 401
                return redirect(url_for("login", next=request.path))
            # Session-cookie auth means a POST needs CSRF protection of its own;
            # SameSite alone isn't a guarantee across all browsers.
            if request.method == "POST" and config.auth_enabled:
                if not csrf_ok(request.headers.get("X-CSRF-Token"), session.get("csrf")):
                    return jsonify({"error": "stale session, reload the page"}), 403
            return view(*args, **kwargs)
        return wrapper

    # -- pages -------------------------------------------------------------

    @app.get("/login")
    def login():
        if authed():
            return redirect(url_for("index"))
        ensure_csrf()
        # The code box is rendered only while a code is actually live, so an
        # instance with none offers nothing to guess at.
        return render_template("login.html", error=request.args.get("error"),
                               pairing_active=config.pairing_active,
                               csrf_token=session.get("csrf"))

    @app.post("/login")
    def do_login():
        key = client_key(request.remote_addr)
        wait = limiter.retry_after(key)
        if wait > 0:
            return redirect(url_for("login", error="Too many attempts. Try again in %d seconds."
                                    % int(wait + 0.5)))
        if not csrf_ok(request.form.get("csrf_token"), session.get("csrf")):
            return redirect(url_for("login", error="Session expired, try again"))
        if config.check_ui_password(request.form.get("password", "")):
            limiter.record_success(key)
            # New session identifier on privilege change, so a pre-set cookie can't
            # be reused as an authenticated one.
            session.clear()
            session["ui_ok"] = True
            session["csrf"] = new_csrf_token()
            session.permanent = True
            target = request.args.get("next") or url_for("index")
            # Only ever redirect within this app.
            if not target.startswith("/") or target.startswith("//"):
                target = url_for("index")
            return redirect(target)
        delay = limiter.record_failure(key)
        log.warning("failed UI login from %s", key)
        if delay:
            return redirect(url_for("login", error="Too many attempts. Try again in %d seconds."
                                    % int(delay + 0.5)))
        return redirect(url_for("login", error="Wrong password"))

    @app.post("/login/pair")
    def do_pair():
        """Trade a four-digit code for the Tesla link.

        Deliberately not a login: a correct code grants the Tesla page and
        nothing else — no session, no UI access. It is a delivery mechanism for a
        URL the car cannot be made to paste, so it can only ever hand over what
        that URL already grants.
        """
        key = "pair:" + client_key(request.remote_addr)
        wait = limiter.retry_after(key)
        if wait > 0:
            return redirect(url_for("login", error="Too many attempts. Try again in %d seconds."
                                    % int(wait + 0.5)))
        if not csrf_ok(request.form.get("csrf_token"), session.get("csrf")):
            return redirect(url_for("login", error="Session expired, try again"))
        # Checked before the code is spent, so a link turned off mid-window does
        # not silently burn the owner's one attempt.
        token = config.tesla_token
        if not token or not config.pairing_active:
            return redirect(url_for("login", error="That code is no longer valid"))
        if not config.consume_pairing_code(request.form.get("code", "")):
            limiter.record_failure(key)
            log.warning("wrong tesla pairing code from %s", key)
            return redirect(url_for("login", error="Wrong code"))
        limiter.record_success(key)
        # A freshly paired car has never seen the bookmark instruction, and the
        # link is the only way back in once this page is closed.
        config.reset_tesla_onboarded()
        log.info("tesla pairing code redeemed from %s", key)
        # Relative on purpose: the car just reached us on an address that works,
        # whereas `public_url` may still be the loopback default.
        return redirect("/tesla/" + token)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.get("/")
    @login_required
    def index():
        status = state.status()
        return render_template("index.html", status=status,
                               has_password=config.auth_enabled,
                               bootstrap={"status": status, "csrf": ensure_csrf()})

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    # -- oauth -------------------------------------------------------------

    @app.get("/connect")
    @login_required
    def connect():
        try:
            url = state.oauth().get_authorize_url()
        except NotAuthenticated as exc:
            return redirect(url_for("index", error=str(exc)))
        # One source of truth for the authorize URL. The old firstrun.py hand-built
        # it with a duplicated scope string that could silently drift from the one
        # the token was actually cached with.
        return redirect(url)

    @app.get("/callback")
    def callback():
        error = request.args.get("error")
        code = request.args.get("code")
        if error:
            state._auth_error = "Spotify returned: %s" % error
            return redirect(url_for("index"))
        if not code:
            state._auth_error = "No authorisation code in the callback."
            return redirect(url_for("index"))
        try:
            state.oauth().get_access_token(code, as_dict=False, check_cache=False)
            state.reset_client()
            state._auth_error = ""
        except Exception as exc:  # noqa: BLE001 - surface any failure in the UI
            log.warning("token exchange failed: %s", exc)
            state._auth_error = "Token exchange failed: %s" % exc
        return redirect(url_for("index"))

    @app.post("/api/exchange")
    @login_required
    def api_exchange():
        """Finish authorisation from a code pasted by hand.

        Spotify's loopback redirect URI only resolves on the machine running
        spoti-sort, so when the UI is open on a laptop or phone the callback lands
        on a page that cannot load. The address bar still holds the code, and this
        accepts either that whole URL or the bare code.
        """
        raw = str((request.get_json(silent=True) or {}).get("code") or "").strip()
        if not raw:
            return jsonify({"error": "paste the address you were redirected to"}), 400
        code = raw
        if "://" in raw or "code=" in raw:
            query = parse_qs(urlparse(raw).query)
            if query.get("error"):
                return jsonify({"error": "Spotify returned: %s" % query["error"][0]}), 400
            found = query.get("code")
            if not found:
                return jsonify({"error": "that address has no ?code= in it"}), 400
            code = found[0]
        try:
            state.oauth().get_access_token(code, as_dict=False, check_cache=False)
        except NotAuthenticated as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001 - spotipy raises assorted types here
            log.warning("manual token exchange failed: %s", exc)
            return jsonify({"error": "Spotify rejected that code. Codes are single-use "
                                     "and expire quickly — authorise again for a fresh one."}), 400
        state.reset_client()
        state._auth_error = ""
        return jsonify({"ok": True})

    @app.post("/api/disconnect")
    @login_required
    def disconnect():
        config.forget_token()
        state.reset_client()
        return jsonify({"ok": True})

    # -- api ---------------------------------------------------------------

    @app.get("/api/status")
    @login_required
    def api_status():
        return jsonify(state.status())

    @app.get("/api/playlists")
    @login_required
    def api_playlists():
        try:
            items = state.playlists(force=request.args.get("refresh") == "1")
        except NotAuthenticated:
            return jsonify({"error": "not connected"}), 409
        except SpotifyException as exc:
            return jsonify({"error": exc.msg or str(exc)}), 502
        return jsonify({"playlists": items, "entries": config.entries})

    @app.post("/api/credentials")
    @login_required
    def api_credentials():
        if config.credentials_from_env:
            return jsonify({"error": "credentials come from the environment"}), 400
        body = request.get_json(silent=True) or {}
        client_id = (body.get("client_id") or "").strip()
        client_secret = (body.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            return jsonify({"error": "both fields are required"}), 400
        config.update(client_id=client_id, client_secret=client_secret)
        state.reset_client()
        return jsonify({"ok": True})

    @app.post("/api/selection")
    @login_required
    def api_selection():
        # Accepts [{"id", "order"}] as well as bare ids, which fall back to the
        # default order.
        body = request.get_json(silent=True) or {}
        entries = config.set_entries(body.get("playlists", []))
        return jsonify({"ok": True, "playlists": entries})

    @app.post("/api/settings")
    @login_required
    def api_settings():
        body = request.get_json(silent=True) or {}
        updates: Dict[str, Any] = {}
        if "interval_minutes" in body:
            try:
                minutes = clamp_interval(int(body["interval_minutes"]))
            except (TypeError, ValueError):
                return jsonify({"error": "interval must be a number"}), 400
            updates["interval_minutes"] = minutes
        if "default_order" in body:
            if body["default_order"] not in (NEWEST_FIRST, OLDEST_FIRST):
                return jsonify({"error": "unknown sort order"}), 400
            updates["order"] = body["default_order"]
        if "run_on_start" in body:
            updates["run_on_start"] = bool(body["run_on_start"])
        if "public_url" in body:
            try:
                updates["public_url"] = clean_public_url(body["public_url"])
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
        config.update(**updates)
        if "interval_minutes" in updates:
            state.scheduler.set_interval(updates["interval_minutes"])
        return jsonify({"ok": True, **updates})

    @app.post("/api/run")
    @login_required
    def api_run():
        if not state.connected:
            return jsonify({"error": "not connected to Spotify"}), 409
        if not config.sort_entries:
            return jsonify({"error": "no playlists are set to be sorted"}), 400
        if not state.scheduler.trigger():
            return jsonify({"error": "a run is already in progress"}), 409
        return jsonify({"ok": True})

    @app.post("/api/favorite")
    @login_required
    def api_favorite():
        body = request.get_json(silent=True) or {}
        playlist_id = str(body.get("playlist_id") or "")
        if not config.set_favorite(playlist_id, bool(body.get("favorite"))):
            return jsonify({"error": "that playlist is not selected"}), 404
        return jsonify({"ok": True, "entries": config.entries})

    @app.post("/api/password")
    @login_required
    def api_password():
        if config.ui_password_from_env:
            return jsonify({"error": "the password is set by UI_PASSWORD"}), 400
        body = request.get_json(silent=True) or {}
        new = str(body.get("password") or "")
        # Changing an existing password requires the old one, so a browser left
        # logged in on a shared machine can't be used to lock the owner out.
        if config.auth_enabled and not config.check_ui_password(str(body.get("current") or "")):
            return jsonify({"error": "current password is wrong"}), 403
        if new and len(new) < 8:
            return jsonify({"error": "use at least 8 characters"}), 400
        config.set_ui_password(new)
        # A Tesla link must never outlive the password: it authenticates by URL
        # alone, so leaving it live on an unprotected instance would hand out
        # exactly the access the password was there to gate.
        revoked = False
        if not config.auth_enabled and config.tesla_token:
            config.clear_tesla_token()
            config.clear_pairing_code()
            revoked = True
        session["ui_ok"] = True
        session["csrf"] = new_csrf_token()
        return jsonify({"ok": True, "enabled": config.auth_enabled,
                        "tesla_revoked": revoked, "csrf_token": session["csrf"]})

    @app.post("/api/tesla-link")
    @login_required
    def api_tesla_link():
        action = (request.get_json(silent=True) or {}).get("action")
        if action in ("enable", "regenerate"):
            # The Tesla link bypasses the login by design, so without a password on
            # the UI it would be the only thing standing between a leaked URL and
            # an otherwise wide-open instance. Refuse to mint one.
            if not config.auth_enabled:
                return jsonify({"error": "Set a password under Access first — the Tesla "
                                         "link works without signing in, so it must not be "
                                         "the only lock on the door."}), 400
            config.new_tesla_token()
        elif action == "disable":
            config.clear_tesla_token()
        else:
            return jsonify({"error": "unknown action"}), 400
        # Any pending code pointed at the link that just went away.
        config.clear_pairing_code()
        return jsonify({"ok": True, "tesla_url": config.tesla_url})

    @app.post("/api/tesla-pair")
    @login_required
    def api_tesla_pair():
        """Mint or cancel the short code the car types in instead of the link."""
        action = (request.get_json(silent=True) or {}).get("action")
        if action == "create":
            if not config.tesla_token:
                return jsonify({"error": "Create the Tesla link first."}), 400
            config.new_pairing_code()
        elif action == "cancel":
            config.clear_pairing_code()
        else:
            return jsonify({"error": "unknown action"}), 400
        return jsonify({"ok": True, "pairing_code": config.pairing_code,
                        "pairing_expires_at": config.pairing_expires_at})

    # -- tesla page --------------------------------------------------------
    #
    # Authenticated by the token in the URL rather than the session, so the car can
    # bookmark one link. That token is deliberately limited: these routes can read
    # what's playing and append to an already-selected playlist, and nothing else.
    # No settings, no credentials, no playlist removal.

    def tesla_auth(view):
        @functools.wraps(view)
        def wrapper(token, *args, **kwargs):
            good = config.tesla_token
            key = "tesla:" + client_key(request.remote_addr)
            if limiter.retry_after(key) > 0:
                abort(404)
            # 404 rather than 401: a revoked or mistyped link shouldn't confirm
            # that the endpoint exists.
            if not good or not hmac.compare_digest(str(token), good):
                limiter.record_failure(key)
                abort(404)
            limiter.record_success(key)
            return view(*args, **kwargs)
        return wrapper

    @app.get("/tesla/<token>")
    @tesla_auth
    def tesla_page():
        # Whether to show the "save this to favourites" instruction is decided here,
        # not in the browser: the Tesla wipes cookies and local storage often enough
        # that a client-side flag would nag on every drive, and losing the URL is
        # the one failure the user cannot recover from inside the car.
        return render_template("tesla.html", onboard=not config.tesla_onboarded)

    @app.post("/api/tesla/<token>/onboarded")
    @tesla_auth
    def tesla_onboarded():
        config.mark_tesla_onboarded()
        return jsonify({"ok": True})

    @app.get("/api/tesla/<token>/state")
    @tesla_auth
    def tesla_state():
        """One call per poll: what's playing plus where it can go."""
        try:
            now = state.now_playing()
        except NotAuthenticated:
            return jsonify({"error": "spoti-sort is not connected to Spotify"}), 503
        except SpotifyException as exc:
            if exc.http_status == 403:
                return jsonify({"error": "Re-authorise spoti-sort to allow reading playback"}), 503
            return jsonify({"error": exc.msg or "Spotify error"}), 502
        targets = state.targets()
        uri = now.get("uri") or ""
        for target in targets:
            target["added"] = bool(state.recent_add(target["id"], uri)) if uri else False
            # Already in the playlist from before — from a previous drive, the
            # desktop app, anywhere. Distinct from "added": that means *this*
            # session put it there and can take it back out.
            members = state.members(target["id"]) if uri else None
            target["contains"] = bool(uri and members is not None and uri in members)
            target["known"] = members is not None
        return jsonify({"now": now, "targets": targets})

    def _tesla_request():
        """Validate an add/remove body. Returns (playlist_id, uri) or an error response."""
        body = request.get_json(silent=True) or {}
        playlist_id = str(body.get("playlist_id") or "")
        uri = str(body.get("uri") or "")
        if not playlist_id or not uri:
            return None, (jsonify({"error": "missing playlist or track"}), 400)
        # Containment: the link may only touch playlists already selected in the
        # main UI, so a leaked URL cannot reach the rest of the account.
        if playlist_id not in {t["id"] for t in state.targets()}:
            return None, (jsonify({"error": "that playlist is not enabled"}), 403)
        return (playlist_id, uri), None

    @app.post("/api/tesla/<token>/add")
    @tesla_auth
    def tesla_add():
        parsed, error = _tesla_request()
        if error:
            return error
        playlist_id, uri = parsed
        if state.recent_add(playlist_id, uri):
            return jsonify({"ok": True, "duplicate": True, "message": "Already added"})
        members = state.members(playlist_id)
        if members is not None and uri in members:
            # Already there from some earlier time, so there is no undo record and
            # nothing to do. Say so rather than silently stacking a duplicate.
            return jsonify({"ok": True, "duplicate": True, "contains": True,
                            "message": "Already in this playlist"})
        entry = next((e for e in config.add_entries if e["id"] == playlist_id), {})
        try:
            info = state.client().add_to_playlist(
                playlist_id, uri, entry.get("order") or config.order)
        except NotAuthenticated:
            return jsonify({"error": "spoti-sort is not connected to Spotify"}), 503
        except SpotifyException as exc:
            return jsonify({"error": exc.msg or "Spotify rejected the add"}), 502
        state.remember_add(playlist_id, uri, info)
        state.remember_member(playlist_id, uri, True)
        config.touch_entry(playlist_id)      # feeds the recently-used ordering
        log.info("tesla: added %s to %s at position %s", uri, playlist_id,
                 info.get("position"))
        return jsonify({"ok": True, "message": "Added"})

    @app.post("/api/tesla/<token>/remove")
    @tesla_auth
    def tesla_remove():
        """Take a track back off a playlist.

        When this page added it, only the exact copy that add created is
        removed — pinned by position and snapshot, never an earlier copy of the
        same track that was already in the playlist. When there is no such
        record (the track was already there before this session), every copy
        of the URI is removed instead, since there is no single position to
        pin.
        """
        parsed, error = _tesla_request()
        if error:
            return error
        playlist_id, uri = parsed
        info = state.recent_add(playlist_id, uri)
        try:
            if info:
                state.client().remove_from_playlist(
                    playlist_id, uri, int(info.get("position") or 0), info.get("snapshot"))
            else:
                members = state.members(playlist_id)
                if members is None or uri not in members:
                    return jsonify({"error": "nothing to undo for this track"}), 409
                state.client().remove_all_from_playlist(playlist_id, uri)
        except NotAuthenticated:
            return jsonify({"error": "spoti-sort is not connected to Spotify"}), 503
        except SpotifyException as exc:
            if exc.http_status in (400, 404):
                # Stale snapshot: the playlist moved on (very likely one of our own
                # sort runs). Removing blind could delete the wrong row.
                return jsonify({"error": "Playlist changed since — remove it in Spotify"}), 409
            return jsonify({"error": exc.msg or "Spotify rejected the removal"}), 502
        state.forget_add(playlist_id, uri)
        state.remember_member(playlist_id, uri, False)
        log.info("tesla: removed %s from %s", uri, playlist_id)
        return jsonify({"ok": True, "message": "Removed"})

    @app.errorhandler(404)
    def not_found(_exc):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not found"}), 404
        return render_template("gone.html"), 404

    return app
