"""Persistent configuration.

Everything lives in one mounted directory (``/config`` by default) so a container
restart keeps both the settings and the OAuth token. Credentials may come from the
environment or be entered in the web UI; the environment always wins so that a
compose file stays the source of truth when one is used.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .sorter import NEWEST_FIRST, OLDEST_FIRST

log = logging.getLogger(__name__)

DEFAULTS: Dict[str, Any] = {
    # [{"id": "...", "order": "newest_first"}, ...] - each playlist carries its own
    # order. Plain id strings are still accepted and migrated on load.
    "playlists": [],
    "interval_minutes": 60,
    # Applies to playlists that don't specify one, and to newly selected ones.
    "order": NEWEST_FIRST,
    "client_id": "",
    "client_secret": "",
    "run_on_start": True,
    # Address this UI is actually reached on, e.g. "http://192.168.1.50:8080".
    # Drives both the OAuth redirect URI and the Tesla page link.
    "public_url": "",
    # Bearer token embedded in the Tesla page URL so the car can bookmark it.
    "tesla_token": "",
}

ORDERS = (NEWEST_FIRST, OLDEST_FIRST)

MIN_INTERVAL = 5
MAX_INTERVAL = 60 * 24 * 7


def _env(name: str) -> str:
    # `os.environ.get` alone treats an empty value as set, which produced a `None`
    # crash deep in the OAuth code rather than a clear message.
    return (os.environ.get(name) or "").strip()


class Config:
    def __init__(self, config_dir: Optional[str] = None):
        self.dir = config_dir or _env("CONFIG_DIR") or "/config"
        self.path = os.path.join(self.dir, "config.json")
        self.token_path = os.path.join(self.dir, "tokens.json")
        self.state_path = os.path.join(self.dir, "state.json")
        self._lock = threading.Lock()
        self._data = dict(DEFAULTS)
        self._ensure_dir()
        self._load()
        self._seed_from_env()

    def _ensure_dir(self) -> None:
        try:
            os.makedirs(self.dir, exist_ok=True)
        except OSError as exc:
            raise SystemExit(
                "Cannot create the config directory %r (%s). Mount a writable volume "
                "at /config, or set CONFIG_DIR." % (self.dir, exc)
            )
        if not os.access(self.dir, os.W_OK):
            raise SystemExit(
                "The config directory %r is not writable. Mount a writable volume at "
                "/config, or set CONFIG_DIR." % self.dir
            )

    def _load(self) -> None:
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                self._data.update({k: v for k, v in stored.items() if k in DEFAULTS})
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable config %s: %s", self.path, exc)
        # Config written before per-playlist ordering stores bare id strings; they
        # inherit whatever the single global order was at the time.
        self._data["playlists"] = normalise_entries(self._data["playlists"], self._data["order"])

    def _seed_from_env(self) -> None:
        """Accept the old PLAYLIST_IDS/interval variables as first-boot defaults."""
        changed = False
        # Read the default order first so seeded playlists pick it up.
        order = _env("SORT_ORDER")
        if order in ORDERS:
            self._data["order"] = order
            changed = True
        if not self._data["playlists"]:
            raw = _env("PLAYLIST_IDS")
            if raw:
                self._data["playlists"] = normalise_entries([raw], self._data["order"])
                changed = True
        interval = _env("INTERVAL_MINUTES")
        if interval.isdigit():
            self._data["interval_minutes"] = clamp_interval(int(interval))
            changed = True
        if changed:
            self.save()

    # -- accessors ---------------------------------------------------------

    @property
    def client_id(self) -> str:
        return _env("CLIENT_ID") or self._data["client_id"]

    @property
    def client_secret(self) -> str:
        return _env("CLIENT_SECRET") or self._data["client_secret"]

    @property
    def credentials_from_env(self) -> bool:
        return bool(_env("CLIENT_ID") and _env("CLIENT_SECRET"))

    @property
    def has_credentials(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def entries(self) -> List[Dict[str, str]]:
        """Selected playlists as ``{"id", "order"}``, each with its own sort order."""
        return [dict(e) for e in self._data["playlists"]]

    @property
    def playlists(self) -> List[str]:
        return [e["id"] for e in self._data["playlists"]]

    def set_entries(self, raw: Any) -> List[Dict[str, str]]:
        entries = normalise_entries(raw, self._data["order"])
        self.update(playlists=entries)
        return entries

    @property
    def interval_minutes(self) -> int:
        return int(self._data["interval_minutes"])

    @property
    def order(self) -> str:
        """Default order, used for playlists that don't carry one of their own."""
        return self._data["order"]

    @property
    def run_on_start(self) -> bool:
        return bool(self._data["run_on_start"])

    @property
    def public_url(self) -> str:
        """Base address the UI is reached on, without a trailing slash."""
        return _env("PUBLIC_URL").rstrip("/") or str(self._data["public_url"]).rstrip("/")

    @property
    def base_url(self) -> str:
        return self.public_url or "http://127.0.0.1:%d" % self.port

    @property
    def redirect_uri(self) -> str:
        # Must match the Spotify dashboard entry byte for byte. REDIRECT_URI stays
        # available for the case where the callback address differs from the base
        # address (an odd reverse-proxy setup, say).
        return _env("REDIRECT_URI") or self.base_url + "/callback"

    @property
    def redirect_uri_from_env(self) -> bool:
        return bool(_env("REDIRECT_URI"))

    @property
    def public_url_from_env(self) -> bool:
        return bool(_env("PUBLIC_URL"))

    # -- tesla page --------------------------------------------------------

    @property
    def tesla_token(self) -> str:
        return str(self._data["tesla_token"])

    @property
    def tesla_url(self) -> str:
        token = self.tesla_token
        return self.base_url + "/tesla/" + token if token else ""

    def new_tesla_token(self) -> str:
        # url-safe and long enough that guessing it is not a concern; it is the
        # only credential the car presents.
        token = secrets.token_urlsafe(24)
        self.update(tesla_token=token)
        return token

    def clear_tesla_token(self) -> None:
        self.update(tesla_token="")

    @property
    def port(self) -> int:
        raw = _env("PORT")
        return int(raw) if raw.isdigit() else 8080

    @property
    def ui_password(self) -> str:
        return _env("UI_PASSWORD")

    def get(self, key: str) -> Any:
        return self._data.get(key)

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for key, value in kwargs.items():
                if key in DEFAULTS:
                    self._data[key] = value
        self.save()

    def save(self) -> None:
        payload = dict(self._data)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.path)
        # The file can hold a client secret when the UI was used to enter one.
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # -- misc --------------------------------------------------------------

    def secret_key(self) -> bytes:
        path = os.path.join(self.dir, "secret_key")
        if os.path.isfile(path):
            try:
                with open(path, "rb") as fh:
                    data = fh.read().strip()
                if data:
                    return data
            except OSError:
                pass
        data = secrets.token_hex(32).encode()
        try:
            with open(path, "wb") as fh:
                fh.write(data)
            os.chmod(path, 0o600)
        except OSError as exc:
            log.warning("could not persist session key: %s", exc)
        return data

    def load_state(self) -> Dict[str, Any]:
        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self, state: Dict[str, Any]) -> None:
        try:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(tmp, self.state_path)
        except OSError as exc:
            log.warning("could not persist state: %s", exc)

    def forget_token(self) -> None:
        try:
            os.remove(self.token_path)
        except OSError:
            pass


def parse_playlist_ids(raw: str) -> List[str]:
    """Accept commas, whitespace, newlines, full URLs and spotify: URIs.

    The old ``split(", ")`` demanded exactly one comma and one space, so a stray
    newline from a compose file turned into an opaque 404.
    """
    out: List[str] = []
    for chunk in raw.replace("\n", ",").replace(" ", ",").split(","):
        token = chunk.strip()
        if not token:
            continue
        if "spotify:playlist:" in token:
            token = token.rsplit(":", 1)[-1]
        elif "/playlist/" in token:
            token = token.split("/playlist/", 1)[1]
        token = token.split("?", 1)[0].strip("/")
        if token:
            out.append(token)
    seen = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def normalise_entries(raw: Any, default_order: str) -> List[Dict[str, str]]:
    """Coerce anything playlist-shaped into ``[{"id", "order"}]``, de-duplicated.

    Accepts the current form (dicts), the pre-per-playlist-order form (bare id
    strings, which inherit ``default_order``), and raw user input containing URLs
    or ``spotify:`` URIs. First occurrence of an id wins.
    """
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []

    out: List[Dict[str, str]] = []
    seen = set()
    for item in raw:
        if isinstance(item, dict):
            raw_id = item.get("id")
            ids = parse_playlist_ids(raw_id) if isinstance(raw_id, str) else []
            order = item.get("order")
        elif isinstance(item, str):
            ids = parse_playlist_ids(item)
            order = None
        else:
            continue  # anything else is junk, not an id spelled oddly
        if order not in ORDERS:
            order = default_order if default_order in ORDERS else NEWEST_FIRST
        for playlist_id in ids:
            if playlist_id in seen:
                continue
            seen.add(playlist_id)
            out.append({"id": playlist_id, "order": order})
    return out


def clean_public_url(raw: str) -> str:
    """Normalise a user-typed base address, or raise ValueError with the reason.

    Spotify matches redirect URIs byte for byte, so a stray trailing slash or a
    missing scheme is a real failure and worth rejecting up front rather than at
    the end of the authorisation round trip.
    """
    value = (raw or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "http://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("address must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError("address needs a hostname, e.g. http://192.168.1.50:8080")
    if parsed.query or parsed.fragment:
        raise ValueError("address must not contain a query string or fragment")
    # Trim the path rather than the whole string, so "http://" fails the netloc
    # check above instead of being silently repaired into something meaningless.
    return "%s://%s%s" % (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"))


def clamp_interval(minutes: int) -> int:
    return max(MIN_INTERVAL, min(MAX_INTERVAL, minutes))
