#!/usr/bin/env python3
"""Run the real app against an in-memory fake Spotify.

The whole UI — setup, playlist picker, scheduler, Tesla page — works without
Spotify credentials, a network connection, or touching a real account. Every route
and template is the production one; only `SpotifyClient` is swapped out.

    python tools/devserver.py                 # http://127.0.0.1:8099
    python tools/devserver.py --port 9000 --keep

The fixture is deliberately awkward on purpose:

* one playlist already contains the "currently playing" track, to exercise the
  duplicate check,
* one is not editable, to exercise the skip path,
* the four selected playlists cover every combination of the sort/add roles and
  both sort orders.

Config lives in a throwaway directory that is recreated on each run unless
``--keep`` is passed, so first-run behaviour (setup card, Tesla onboarding) is
easy to retest.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
import tempfile
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NOW_URI = "spotify:track:4cOdK2wGLETKBW3PvgPWqT"

NAMES = ["Tesla Roadtrip", "Deep Focus", "Late Night Drive", "Gym 2024",
         "Discover Weekly", "Kitchen Disco", "Ambient Work", "Punk Mornings"]
GRADIENTS = ["#ff5f6d,#5b247a", "#00c6ff,#0072ff", "#f7971e,#ffd200", "#11998e,#38ef7d",
             "#8e2de2,#4a00e0", "#e52d27,#b31217", "#1f4037,#99f2c8", "#fc466b,#3f5efb"]


def cover(i: int) -> str:
    """A data: URI stand-in for album art, so nothing is fetched from the network."""
    a, b = GRADIENTS[i % len(GRADIENTS)].split(",")
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
           '<stop offset="0" stop-color="%s"/><stop offset="1" stop-color="%s"/>'
           '</linearGradient></defs><rect width="100" height="100" fill="url(#g)"/>'
           '<circle cx="50" cy="50" r="17" fill="#0e0e12" opacity=".8"/></svg>' % (a, b))
    return "data:image/svg+xml," + urllib.parse.quote(svg)


def build_playlists(rng):
    from spotisort.sorter import item_uri  # noqa: F401  (imported for symmetry)

    playlists = []
    for i, name in enumerate(NAMES):
        items = [{"added_at": "2024-%02d-%02dT00:00:00Z" % (rng.randint(1, 12), rng.randint(1, 28)),
                  "item": {"id": "t%d" % k, "name": "Track %d" % k,
                           "uri": "spotify:track:t%d" % k}}
                 for k in range(rng.randint(6, 40))]
        if i == 1:  # already contains the playing track -> duplicate check
            items.append({"added_at": "2023-01-01T00:00:00Z",
                          "item": {"id": "now", "name": "Never Gonna Give You Up",
                                   "uri": NOW_URI}})
        playlists.append({
            "id": "pl%02d" % i, "name": name, "image": cover(i),
            "editable": i != 4,     # one playlist we may not touch
            "items": items,
        })
    return playlists


def make_client(playlists):
    from spotipy.exceptions import SpotifyException

    from spotisort.sorter import (NEWEST_FIRST, OLDEST_FIRST, PlaylistResult,
                                  RunResult, apply_move, item_uri, plan_moves)

    class FakeClient:
        """Mirrors the SpotifyClient surface the app actually uses."""

        def _pl(self, pid):
            return next(p for p in playlists if p["id"] == pid)

        def me(self):
            return {"id": "me", "display_name": "Dev User", "images": [{"url": ""}]}

        def my_playlists(self):
            return [{"id": p["id"], "name": p["name"],
                     "owner": "Dev User" if p["editable"] else "Spotify",
                     "total": len(p["items"]), "image": p["image"],
                     "editable": p["editable"]} for p in playlists]

        def playlist_total(self, pid):
            return len(self._pl(pid)["items"])

        def playlist_uris(self, pid):
            return {u for u in (item_uri(i) for i in self._pl(pid)["items"]) if u}

        def now_playing(self):
            return {"playing": True, "is_playing": True, "uri": NOW_URI,
                    "id": "4cOdK2wGLETKBW3PvgPWqT", "name": "Never Gonna Give You Up",
                    "artist": "Rick Astley", "album": "Whenever You Need Somebody",
                    "image": cover(0), "progress_ms": 61000, "duration_ms": 213000,
                    "addable": True}

        def add_to_playlist(self, pid, uri, order=NEWEST_FIRST):
            p = self._pl(pid)
            pos = len(p["items"])
            p["items"].append({"added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                               "item": {"id": "new", "name": "Added track", "uri": uri}})
            if order != OLDEST_FIRST and pos > 0:
                p["items"] = apply_move(p["items"], pos, 0)
                pos = 0
            return {"position": pos, "snapshot": "snap-%d" % len(p["items"])}

        def remove_from_playlist(self, pid, uri, position, snapshot):
            p = self._pl(pid)
            # Same stale-snapshot rejection the real API performs.
            if snapshot != "snap-%d" % len(p["items"]):
                raise SpotifyException(400, -1, "stale snapshot")
            del p["items"][position]

        def sort_playlist(self, pid, order, dry_run=False):
            time.sleep(0.6)          # make the "running" state visible in the UI
            p = self._pl(pid)
            r = PlaylistResult(playlist_id=pid, name=p["name"],
                               total=len(p["items"]), order=order)
            if not p["editable"]:
                r.status, r.detail = "skipped", "not owned by you and not collaborative"
                return r
            moves = plan_moves([i["added_at"] for i in p["items"]], order)
            if not dry_run:
                for a, b in moves:
                    p["items"] = apply_move(p["items"], a, b)
            r.moves = len(moves)
            r.detail = "%d move(s)" % len(moves) if moves else "already in order"
            r.uris = self.playlist_uris(pid)
            return r

        def sort_all(self, entries, dry_run=False):
            run = RunResult(started_at=time.time())
            for e in entries:
                run.playlists.append(self.sort_playlist(e["id"], e["order"], dry_run))
            run.finished_at = time.time()
            return run

    return FakeClient()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--host", default="127.0.0.1",
                    help="use 0.0.0.0 to reach it from a phone or the car")
    ap.add_argument("--config", default=os.path.join(tempfile.gettempdir(), "spotisort-dev"))
    ap.add_argument("--keep", action="store_true", help="keep the existing dev config")
    ap.add_argument("--password", default="dev-password-1234",
                    help="UI password; a Tesla link cannot exist without one")
    ap.add_argument("--no-connect", action="store_true",
                    help="leave it disconnected to work on the setup flow")
    args = ap.parse_args()

    if not args.keep and os.path.isdir(args.config):
        shutil.rmtree(args.config)
    os.makedirs(args.config, exist_ok=True)

    public = "http://%s:%d" % ("127.0.0.1" if args.host == "127.0.0.1" else args.host, args.port)
    os.environ.update(CONFIG_DIR=args.config, PORT=str(args.port),
                      CLIENT_ID="dev", CLIENT_SECRET="dev", PUBLIC_URL=public)

    from spotisort import __version__, web
    from spotisort.config import Config

    rng = random.Random(7)
    playlists = build_playlists(rng)

    cfg = Config(args.config)
    cfg.set_entries([
        {"id": "pl00", "order": "newest_first", "sort": True,  "add": True},
        {"id": "pl01", "order": "newest_first", "sort": False, "add": True},
        {"id": "pl02", "order": "oldest_first", "sort": True,  "add": True},
        {"id": "pl03", "order": "newest_first", "sort": True,  "add": False},
    ])
    cfg.update(interval_minutes=60)
    if args.password and not cfg.auth_enabled:
        cfg.set_ui_password(args.password)
    if not cfg.tesla_token:
        cfg.new_tesla_token()

    app = web.create_app(cfg)
    state = app.extensions["spotisort"]
    if not args.no_connect:
        client = make_client(playlists)
        state.client = lambda: client
        type(state).connected = property(lambda self: True)
    state.scheduler.start(run_now=False)

    print("\n  spoti-sort %s (dev, fake Spotify)" % __version__)
    print("  UI       %s" % public)
    print("  Tesla    %s" % cfg.tesla_url)
    print("  password %s" % (args.password or "(none)"))
    print("  config   %s\n" % args.config)

    from waitress import serve
    serve(app, host=args.host, port=args.port, threads=8)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
