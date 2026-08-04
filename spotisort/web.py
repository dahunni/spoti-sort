"""Flask app: configuration UI, OAuth callback, and the JSON API the page polls."""

from __future__ import annotations

import functools
import hmac
import logging
import time
from typing import Any, Dict, List, Optional

from flask import (Flask, jsonify, redirect, render_template, request,
                   session, url_for)
from spotipy.exceptions import SpotifyException

from .config import Config, clamp_interval
from .scheduler import Scheduler
from .sorter import NEWEST_FIRST, OLDEST_FIRST
from .spotify import NotAuthenticated, SpotifyClient, make_oauth

log = logging.getLogger(__name__)

# Playlist metadata changes rarely and the list can be long; refetching it on every
# poll would burn the rate limit for nothing.
PLAYLIST_CACHE_TTL = 300


class App:
    def __init__(self, config: Config):
        self.config = config
        self._client: Optional[SpotifyClient] = None
        self._playlists: List[Dict[str, Any]] = []
        self._playlists_at = 0.0
        self._auth_error = ""

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

    @app.errorhandler(404)
    def not_found(_exc):
        return jsonify({"error": "not found"}), 404

    return app
