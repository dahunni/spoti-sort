"""Flask app: configuration UI, OAuth callback, and the JSON API the page polls."""

from __future__ import annotations

import functools
import hmac
import logging
import time
from typing import Any, Dict, List, Optional

from flask import (Flask, abort, jsonify, redirect, render_template, request,
                   session, url_for)
from spotipy.exceptions import SpotifyException

from .config import Config, clamp_interval, clean_public_url
from .scheduler import Scheduler
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

    def targets(self) -> List[Dict[str, str]]:
        """Playlists the Tesla page may add to: the selected, editable ones."""
        known: Dict[str, Dict[str, Any]] = {}
        try:
            known = {p["id"]: p for p in self.playlists()}
        except (NotAuthenticated, SpotifyException, OSError):
            pass
        out = []
        for entry in self.config.entries:
            meta = known.get(entry["id"])
            if meta and not meta.get("editable"):
                continue
            out.append({"id": entry["id"], "name": (meta or {}).get("name") or entry["id"]})
        return out

    def _add_key(self, playlist_id: str, uri: str) -> str:
        return "%s|%s" % (playlist_id, uri)

    def recently_added(self, playlist_id: str, uri: str) -> bool:
        now = time.time()
        for key, when in list(self._recent_adds.items()):
            if now - when > ADD_GUARD_SECONDS:
                del self._recent_adds[key]
        return self._add_key(playlist_id, uri) in self._recent_adds

    def remember_add(self, playlist_id: str, uri: str) -> None:
        self._recent_adds[self._add_key(playlist_id, uri)] = time.time()

    # -- runs --------------------------------------------------------------

    def run_sort(self) -> Dict[str, Any]:
        entries = self.config.entries
        if not entries:
            return {"started_at": time.time(), "finished_at": time.time(), "duration": 0,
                    "moves": 0, "ok": True, "playlists": [], "note": "no playlists selected"}
        try:
            client = self.client()
        except NotAuthenticated as exc:
            return {"started_at": time.time(), "finished_at": time.time(), "duration": 0,
                    "moves": 0, "ok": False, "playlists": [], "error": str(exc)}
        return client.sort_all(entries).as_dict()

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
            "configured": self.config.has_credentials,
            "credentials_from_env": self.config.credentials_from_env,
            "connected": connected,
            "user": user,
            "auth_error": self._auth_error,
            "redirect_uri": self.config.redirect_uri,
            "redirect_uri_from_env": self.config.redirect_uri_from_env,
            "public_url": self.config.public_url,
            "public_url_from_env": self.config.public_url_from_env,
            "missing_scopes": self.missing_scopes() if connected else [],
            "tesla_url": self.config.tesla_url,
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

    def authed() -> bool:
        return not config.ui_password or session.get("ui_ok") is True

    def login_required(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            if not authed():
                if request.path.startswith("/api/"):
                    return jsonify({"error": "unauthorised"}), 401
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapper

    # -- pages -------------------------------------------------------------

    @app.get("/login")
    def login():
        if authed():
            return redirect(url_for("index"))
        return render_template("login.html", error=request.args.get("error"))

    @app.post("/login")
    def do_login():
        supplied = request.form.get("password", "")
        if hmac.compare_digest(supplied, config.ui_password):
            session["ui_ok"] = True
            session.permanent = True
            return redirect(request.args.get("next") or url_for("index"))
        return redirect(url_for("login", error="Wrong password"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.get("/")
    @login_required
    def index():
        return render_template("index.html", status=state.status(),
                               has_password=bool(config.ui_password))

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
        if not config.playlists:
            return jsonify({"error": "no playlists selected"}), 400
        if not state.scheduler.trigger():
            return jsonify({"error": "a run is already in progress"}), 409
        return jsonify({"ok": True})

    @app.post("/api/tesla-link")
    @login_required
    def api_tesla_link():
        action = (request.get_json(silent=True) or {}).get("action")
        if action in ("enable", "regenerate"):
            config.new_tesla_token()
        elif action == "disable":
            config.clear_tesla_token()
        else:
            return jsonify({"error": "unknown action"}), 400
        return jsonify({"ok": True, "tesla_url": config.tesla_url})

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
            # 404 rather than 401: a revoked or mistyped link shouldn't confirm
            # that the endpoint exists.
            if not good or not hmac.compare_digest(str(token), good):
                abort(404)
            return view(*args, **kwargs)
        return wrapper

    @app.get("/tesla/<token>")
    @tesla_auth
    def tesla_page():
        return render_template("tesla.html")

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
            target["added"] = state.recently_added(target["id"], uri) if uri else False
        return jsonify({"now": now, "targets": targets})

    @app.post("/api/tesla/<token>/add")
    @tesla_auth
    def tesla_add():
        body = request.get_json(silent=True) or {}
        playlist_id = str(body.get("playlist_id") or "")
        uri = str(body.get("uri") or "")
        if not playlist_id or not uri:
            return jsonify({"error": "missing playlist or track"}), 400
        # Containment: the link may only add to playlists already selected in the
        # main UI, so a leaked URL cannot reach the rest of the account.
        if playlist_id not in {t["id"] for t in state.targets()}:
            return jsonify({"error": "that playlist is not enabled"}), 403
        if state.recently_added(playlist_id, uri):
            return jsonify({"ok": True, "duplicate": True, "message": "Already added"})
        try:
            state.client().add_to_playlist(playlist_id, uri)
        except NotAuthenticated:
            return jsonify({"error": "spoti-sort is not connected to Spotify"}), 503
        except SpotifyException as exc:
            return jsonify({"error": exc.msg or "Spotify rejected the add"}), 502
        state.remember_add(playlist_id, uri)
        log.info("tesla: added %s to %s", uri, playlist_id)
        return jsonify({"ok": True, "message": "Added"})

    @app.errorhandler(404)
    def not_found(_exc):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not found"}), 404
        return render_template("gone.html"), 404

    return app
