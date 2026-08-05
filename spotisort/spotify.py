"""Spotify API access: OAuth wiring, paging, and the reorder executor."""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Dict, List, Optional

import spotipy
from spotipy.cache_handler import CacheFileHandler
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from .sorter import ITEM_FIELDS, PlaylistResult, RunResult, apply_move, plan_moves

log = logging.getLogger(__name__)

# Least privilege: read playlists, reorder and add to them, and see what is playing
# (for the Tesla page). The original code also asked for `user-library-modify`,
# which it never used.
SORT_SCOPES = ("playlist-read-private playlist-read-collaborative "
               "playlist-modify-private playlist-modify-public")
NOW_PLAYING_SCOPES = "user-read-currently-playing user-read-playback-state"
SCOPE = SORT_SCOPES + " " + NOW_PLAYING_SCOPES

MAX_ATTEMPTS = 5


def missing_scopes(token: Optional[Dict[str, Any]]) -> List[str]:
    """Scopes we now ask for that an existing cached token predates."""
    if not token:
        return SCOPE.split()
    granted = set((token.get("scope") or "").replace(",", " ").split())
    return [s for s in SCOPE.split() if s not in granted]


class NotAuthenticated(Exception):
    pass


# A token can be perfectly valid and every call still refused — most commonly
# because the Spotify app is in Development mode, where only accounts added under
# User Management may use it. That reads as "not connected" unless it is named.
NOT_REGISTERED = "not_registered"


def describe_account_error(exc: SpotifyException) -> Dict[str, str]:
    message = (exc.msg or "").strip()
    if exc.http_status == 403 and "not registered" in message.lower():
        return {
            "kind": NOT_REGISTERED,
            "message": "Your Spotify account is not on this app's user list.",
        }
    if exc.http_status == 403:
        return {"kind": "forbidden", "message": message or "Spotify refused the request."}
    if exc.http_status == 401:
        return {"kind": "expired", "message": "The stored authorisation is no longer valid."}
    return {"kind": "error", "message": message or "Spotify returned HTTP %s." % exc.http_status}


def make_oauth(client_id: str, client_secret: str, redirect_uri: str, cache_path: str) -> SpotifyOAuth:
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPE,
        cache_handler=CacheFileHandler(cache_path=cache_path),
        open_browser=False,
    )


def _items_path(playlist_id: str) -> str:
    """Path for a playlist's contents.

    Spotify's 2026 migration replaced `/playlists/{id}/tracks` with
    `/playlists/{id}/items` for every verb and started returning 403 on the old
    path, so spotipy's `playlist_items` / `playlist_add_items` /
    `playlist_reorder_items` helpers (which still target `/tracks`) cannot be used.
    These calls go through spotipy's authenticated transport but build the path
    and payloads here. Request bodies changed too: removal keys on `items`, not
    `tracks`.
    """
    return "playlists/%s/items" % playlist_id


def _retry(call: Callable[[], Any], what: str) -> Any:
    """Retry on rate limits and transient server errors.

    A single 429 used to end the whole run mid-sort and leave the playlist half
    ordered, which the next scheduled run then had to untangle.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return call()
        except SpotifyException as exc:
            retryable = exc.http_status == 429 or (exc.http_status or 0) >= 500
            if not retryable or attempt == MAX_ATTEMPTS:
                raise
            delay = float(exc.headers.get("Retry-After", 0) or 0) if exc.headers else 0.0
            if not delay:
                delay = min(30.0, 2 ** attempt) + random.uniform(0, 0.5)
            log.warning("%s: HTTP %s, retrying in %.1fs (attempt %d/%d)",
                        what, exc.http_status, delay, attempt, MAX_ATTEMPTS)
            time.sleep(delay)


class SpotifyClient:
    def __init__(self, auth_manager: SpotifyOAuth):
        self._auth = auth_manager
        # spotipy retries connection-level failures; API-level 429/5xx are handled
        # by `_retry` so we can honour Retry-After ourselves.
        self.sp = spotipy.Spotify(auth_manager=auth_manager, requests_timeout=30, retries=2)
        self._me: Optional[Dict[str, Any]] = None

    @classmethod
    def from_cache(cls, oauth: SpotifyOAuth) -> "SpotifyClient":
        token = oauth.cache_handler.get_cached_token()
        if not token:
            raise NotAuthenticated("no cached token")
        if oauth.is_token_expired(token):
            if not token.get("refresh_token"):
                raise NotAuthenticated("cached token expired and has no refresh token")
            oauth.refresh_access_token(token["refresh_token"])
        return cls(oauth)

    def me(self) -> Dict[str, Any]:
        if self._me is None:
            self._me = _retry(lambda: self.sp.me(), "me")
        return self._me

    def my_playlists(self) -> List[Dict[str, Any]]:
        """Every playlist visible to the user, flagged with whether we may edit it."""
        user_id = self.me()["id"]
        out: List[Dict[str, Any]] = []
        offset = 0
        while True:
            page = _retry(lambda o=offset: self.sp.current_user_playlists(limit=50, offset=o), "playlists")
            for item in page.get("items", []):
                if not item:
                    continue
                owner = (item.get("owner") or {}).get("id")
                images = item.get("images") or []
                out.append({
                    "id": item["id"],
                    "name": item.get("name") or "(untitled)",
                    "owner": (item.get("owner") or {}).get("display_name") or owner or "",
                    # The 2026 migration dropped `tracks` from playlist objects, so
                    # this is usually unknown now. Counting would cost a call each.
                    "total": (item.get("tracks") or {}).get("total"),
                    "image": images[-1]["url"] if images else None,
                    "editable": owner == user_id or bool(item.get("collaborative")),
                })
            if not page.get("next"):
                break
            offset += len(page.get("items") or [])
            if not page.get("items"):
                break
        return out

    def now_playing(self) -> Dict[str, Any]:
        """The currently playing item, flattened for the Tesla page.

        One API call. Returns ``{"playing": False}`` when nothing is active, which
        is also what a 204 from Spotify looks like through spotipy.
        """
        current = _retry(
            lambda: self.sp.current_playback(additional_types="track,episode"),
            "current playback",
        )
        item = (current or {}).get("item")
        if not current or not item:
            return {"playing": False}

        images = ((item.get("album") or {}).get("images")
                  or (item.get("show") or {}).get("images") or [])
        artists = [a.get("name") for a in (item.get("artists") or []) if a.get("name")]
        if not artists and item.get("show"):
            artists = [item["show"].get("name") or ""]

        return {
            "playing": True,
            "is_playing": bool(current.get("is_playing")),
            "uri": item.get("uri"),
            "id": item.get("id"),
            "name": item.get("name") or "",
            "artist": ", ".join(artists),
            "album": (item.get("album") or {}).get("name") or "",
            # images are ordered widest first; index 1 is the ~300px copy
            "image": images[min(1, len(images) - 1)]["url"] if images else None,
            "progress_ms": current.get("progress_ms") or 0,
            "duration_ms": item.get("duration_ms") or 0,
            # Local files have no uri Spotify will accept back into a playlist.
            "addable": bool(item.get("uri")) and not item.get("is_local"),
        }

    def add_to_playlist(self, playlist_id: str, uri: str) -> Dict[str, Any]:
        """Append a track and return what's needed to undo it exactly.

        The item lands at the end, so the current length is its position. Knowing
        that lets an undo remove *that* copy via
        ``playlist_remove_specific_occurrences_of_items`` rather than every
        occurrence of the track, which would delete a legitimate earlier copy.
        Costs one extra read per add, which is worth it to make undo safe.
        """
        # Appended at the end, so the current length is the new item's position.
        position = self.playlist_total(playlist_id)
        response = _retry(
            lambda: self.sp._post(_items_path(playlist_id), payload={"uris": [uri]}),
            "add to %s" % playlist_id,
        )
        return {"position": position, "snapshot": (response or {}).get("snapshot_id")}

    def remove_from_playlist(self, playlist_id: str, uri: str, position: int,
                             snapshot: Optional[str]) -> None:
        """Remove the single copy at ``position``.

        The snapshot pins the playlist as it was just after the add; if anything
        changed since (including one of our own sort runs) Spotify rejects this
        rather than removing the wrong row.
        """
        payload: Dict[str, Any] = {"items": [{"uri": uri, "positions": [position]}]}
        if snapshot:
            payload["snapshot_id"] = snapshot
        _retry(
            lambda: self.sp._delete(_items_path(playlist_id), payload=payload),
            "remove from %s" % playlist_id,
        )

    def playlist_meta(self, playlist_id: str) -> Dict[str, Any]:
        # `tracks.total` no longer exists on playlist objects; use playlist_total().
        return _retry(
            lambda: self.sp.playlist(playlist_id, fields="name,snapshot_id,collaborative,owner.id"),
            "playlist %s" % playlist_id,
        )

    def playlist_total(self, playlist_id: str) -> int:
        """Number of entries, from the items paging object."""
        page = _retry(
            lambda: self.sp._get(_items_path(playlist_id), limit=1, fields="total"),
            "count %s" % playlist_id,
        )
        return int((page or {}).get("total") or 0)

    def playlist_items(self, playlist_id: str) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        offset = 0
        while True:
            page = _retry(
                lambda o=offset: self.sp._get(
                    _items_path(playlist_id), fields=ITEM_FIELDS, limit=100, offset=o),
                "items %s" % playlist_id,
            )
            batch = page.get("items") or []
            items.extend(batch)
            if not page.get("next") or not batch:
                break
            offset += len(batch)
        return items

    def sort_playlist(self, playlist_id: str, order: str, dry_run: bool = False,
                      pause: float = 0.05) -> PlaylistResult:
        result = PlaylistResult(playlist_id=playlist_id, order=order)
        try:
            meta = self.playlist_meta(playlist_id)
            result.name = meta.get("name") or playlist_id
            owner = (meta.get("owner") or {}).get("id")
            if owner != self.me()["id"] and not meta.get("collaborative"):
                result.status = "skipped"
                result.detail = "not owned by you and not collaborative"
                return result

            items = self.playlist_items(playlist_id)
            result.total = len(items)
            moves = plan_moves([i.get("added_at") for i in items], order)
            if not moves:
                result.detail = "already in order"
                return result
            if dry_run:
                result.moves = len(moves)
                result.detail = "dry run: %d move(s) needed" % len(moves)
                return result

            # Chain the snapshot id through the run. If the playlist changes underneath
            # us (a track added from the phone mid-run) the API rejects the stale write
            # instead of shuffling the wrong rows.
            snapshot = meta.get("snapshot_id")
            local = list(range(len(items)))
            for range_start, insert_before in moves:
                response = _retry(
                    lambda s=range_start, b=insert_before, snap=snapshot: self.sp._put(
                        _items_path(playlist_id),
                        payload=dict({"range_start": s, "insert_before": b, "range_length": 1},
                                     **({"snapshot_id": snap} if snap else {}))),
                    "reorder %s" % playlist_id,
                )
                snapshot = (response or {}).get("snapshot_id", snapshot)
                local = apply_move(local, range_start, insert_before)
                result.moves += 1
                if pause:
                    time.sleep(pause)
            result.detail = "%d move(s)" % result.moves
            log.info("sorted %s (%s): %d move(s) over %d items",
                     result.name, playlist_id, result.moves, result.total)
        except SpotifyException as exc:
            result.status = "error"
            if exc.http_status == 404:
                result.detail = "playlist not found"
            elif exc.http_status == 403:
                result.detail = "not allowed to modify this playlist"
            else:
                result.detail = exc.msg or str(exc)
            log.warning("playlist %s failed: %s", playlist_id, result.detail)
        except Exception as exc:  # noqa: BLE001 - one bad playlist must not kill the run
            result.status = "error"
            result.detail = str(exc)
            log.exception("playlist %s failed", playlist_id)
        return result

    def sort_all(self, entries: List[Dict[str, str]], dry_run: bool = False) -> RunResult:
        """Sort each selected playlist using its own configured order."""
        run = RunResult(started_at=time.time())
        for entry in entries:
            run.playlists.append(
                self.sort_playlist(entry["id"], entry["order"], dry_run=dry_run))
        run.finished_at = time.time()
        return run
