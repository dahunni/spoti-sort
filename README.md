<h3 align="center">spoti-sort</h3>

<div align="center">

[![Status](https://img.shields.io/badge/status-active-success.svg)]()
[![GitHub Issues](https://img.shields.io/github/issues/dahunni/spoti-sort.svg)](https://github.com/dahunni/spoti-sort/issues)
[![GitHub Pull Requests](https://img.shields.io/github/issues-pr/dahunni/spoti-sort.svg)](https://github.com/dahunni/spoti-sort/pulls)
[![License](https://img.shields.io/badge/license-GPLv3-blue.svg)](/LICENSE)

</div>

---

<p align="center">
A small Docker service that keeps Spotify playlists in <b>date-added</b> order — without
deleting and re-adding a single track. Built for players that ignore sorting entirely,
like the one in a Tesla.
<br>
</p>

## 📝 Table of Contents

- [About](#about)
- [Getting started](#getting_started)
- [Configuration](#configuration)
- [The Tesla page](#tesla)
- [How the sorting works](#how)
- [Spotify API notes](#apinotes)
- [Development](#development)
- [Built using](#built_using)
- [Authors](#authors)

## 🧐 About <a name="about"></a>

Spotify's own clients can *display* a playlist in date-added order, but many players —
the Tesla media player, most car head units, plenty of speakers — always play it in
stored order. The usual workaround is to delete every track and re-add it in the right
sequence, which resets every *Date added* to today and destroys exactly the information
you were sorting by.

spoti-sort reorders the playlist in place through the Spotify API instead. Nothing is
removed, so timestamps, play counts and the playlist itself all survive. It runs on a
schedule and has a web UI for setup, playlist selection and monitoring.

## 🏁 Getting started <a name="getting_started"></a>

### 1. Run the container

```bash
docker run -d --name spoti-sort -p 8080:8080 -v /path/on/host:/config dahunni/spoti-sort:latest
```

Images are published for `linux/amd64` and `linux/arm64`, so the same tag works on a PC,
a NAS and a Raspberry Pi.

Or with the included compose file:

```bash
docker compose up -d
```

The `/config` volume holds your settings and the Spotify token. Mount it, or you'll be
setting the container up again after every restart.

### 2. Open the UI

Go to <http://127.0.0.1:8080>. It walks you through the remaining two steps and shows
the exact redirect URI to paste into Spotify.

### 3. Create a Spotify app

At the [Spotify developer dashboard](https://developer.spotify.com/dashboard), create an
app, tick **Web API**, and add the redirect URI shown in the UI — by default:

```
http://127.0.0.1:8080/callback
```

It must match character for character.

**Spotify only accepts an HTTPS redirect URI, or HTTP on a loopback literal**
(`127.0.0.1`, `[::1]`). A plain `http://` LAN address — or `localhost` — is refused with
*"Insecure redirect URI"*. So authorisation runs over `127.0.0.1` no matter what address
you reach the UI on; only the redirect URI is constrained, and your address is still used
for the Tesla link. Put the instance behind HTTPS and the redirect URI follows it
automatically.

If you're setting up from another machine, the callback will land on a `127.0.0.1` page
that can't load — that's expected. Copy the failed address out of the browser bar and
paste it into **Finish** on the authorise card; the code is in it.

Copy the Client ID and secret into the UI (or set them as environment variables), click
**Authorise with Spotify**, and pick your playlists. That's it — no shell, no manual
first-run script.

### 4. Choose what each playlist is for

Every playlist has two independent switches:

| Switch | Effect |
| --- | --- |
| **Sort** | Kept in date-added order on the schedule, with its own order direction. |
| **Car** | Offered as a one-tap target on the [Tesla page](#tesla), with an optional ★. |

They're unrelated. A "Discoveries" playlist you throw tracks into from the car never has
to be reordered; a big archive you keep in date order never has to clutter the car screen.
Turn both off and the playlist is simply deselected.

> **Reaching the UI on another address?** If you browse to `http://192.168.1.50:8080` or
> through a reverse proxy, type that address into the **address** field in step 2 of the
> setup card; `PUBLIC_URL` does the same from the environment. That address drives the
> Tesla link. The redirect URI below it only follows the address when it is HTTPS —
> otherwise it stays on loopback, because that is all Spotify accepts.

## ⚙️ Configuration <a name="configuration"></a>

Everything can be set in the web UI. Environment variables are optional and take
precedence where both exist.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIENT_ID` | – | Spotify app client ID. Settable in the UI instead. |
| `CLIENT_SECRET` | – | Spotify app client secret. Settable in the UI instead. |
| `PUBLIC_URL` | – | Address the UI is actually reached on, e.g. `http://192.168.1.50:8080`. Settable in the UI instead. Drives the Tesla link, and the redirect URI when it is HTTPS. |
| `REDIRECT_URI` | `$PUBLIC_URL/callback` if HTTPS, else `http://127.0.0.1:$PORT/callback` | Overrides the choice entirely. Must match the Spotify app exactly, and Spotify requires HTTPS or a loopback literal. |
| `PORT` | `8080` | Port the UI listens on. |
| `HOST` | `0.0.0.0` | Bind address. |
| `CONFIG_DIR` | `/config` | Where settings and the token are stored. |
| `UI_PASSWORD` | – | If set, the UI asks for this password. Can be set in the UI instead, where it is stored as a PBKDF2 hash. |
| `TRUST_PROXY` | – | Set to `1` behind a reverse proxy so `X-Forwarded-Proto`/`-For` are honoured. |
| `PLAYLIST_IDS` | – | Seeds the playlist selection on first boot. Accepts ids, URLs or `spotify:` URIs, separated by commas or whitespace. |
| `INTERVAL_MINUTES` | `60` | Overrides the schedule. |
| `SORT_ORDER` | `newest_first` | Default order for newly selected playlists: `newest_first` or `oldest_first`. Per-playlist orders are set in the UI. |
| `LOG_LEVEL` | `INFO` | Python log level. |

### A note on exposure

Set a password in **Access** before exposing this beyond your own network — the UI warns
you when it's reachable at a non-loopback address without one. Passwords are stored as
salted PBKDF2 hashes, logins are CSRF-protected and rate-limited with an escalating
lockout, and responses carry a strict CSP plus `X-Frame-Options`, `nosniff` and
`no-referrer`. Behind a reverse proxy set `TRUST_PROXY=1` so HTTPS and client addresses
are detected correctly.

The [Tesla page](#tesla) is authenticated by its own link instead, and the password does
not apply to it — that's what lets the car use it without signing in.

## 🚗 The Tesla page <a name="tesla"></a>

A single bookmarkable page for the car. It shows what's playing and adds it to one of your
enabled playlists with one tap — useful because the Tesla player can't add to a playlist
at all.

**A password is required before a link can be created.** The link authenticates by URL
alone, so on an instance with no password it would be the only lock on the door; removing
the password later turns the link off too.

Open **Tesla page → Create link** in the UI and bookmark the resulting URL in the car's
browser. The first time the page is opened it shows an instruction to save it to the
favourites via the star at the top right — the car's browser routinely loses cookies and
local storage, and the URL is the only way back in, so that flag is stored on the server
rather than in the browser. Regenerating the link brings the instruction back.

Each playlist button shows its cover art, and one that **already contains the current
track** is dimmed and marked rather than offered — the check covers copies added at any
time from anywhere, not just this session. Buttons are ordered ★ favourites first, then
whichever you added to most recently. Tap a playlist to add the current track; tap it
again while it's green to undo, which removes exactly the copy that tap created.

An added track lands where the playlist's own order says it should: at the top of a
newest-first playlist, at the end of an oldest-first one. It is in the right place
immediately, without waiting for the next scheduled run.

The link contains its own access key, so the car never sees your Spotify login and never
has to sign in — but that also means **anyone with the link can use it**. It is
deliberately narrow. It can:

- read what's currently playing, and
- append the current track to a playlist you marked **Car**, or undo an add it just made.

It cannot change settings, reach playlists you didn't mark **Car**, delete anything it
didn't add itself, or see your credentials. Undo only ever removes the one copy a tap
created, and only while spoti-sort still holds the record for it — everything else in the
playlist is untouchable from the car. **Regenerate** invalidates the old link; **Turn
off** disables the page entirely.

Set the public address first, or the link will point at `127.0.0.1`, which the car can't
reach. The UI warns you when that's the case.

Reading playback needs two permissions that earlier versions didn't request. If you set
spoti-sort up before this feature existed, the UI shows a **re-authorise** prompt — sorting
keeps working until you do, the Tesla page doesn't.

### API usage

Roughly: one call every 5 seconds while the page is open and something is playing, plus
one per track you add.

That's well inside Spotify's limits, and the page is built not to waste calls — the server
collapses all viewers into at most one upstream request every 4 seconds, polling drops to
one call per 20 seconds when playback is paused or idle, and stops entirely when the tab
isn't visible. The progress bar animates locally rather than polling for it. Leaving the
page open in a parked car costs nothing.

Duplicate protection is local: adding the same track to the same playlist twice within 15
minutes is refused without an API call, which covers the realistic case of double-tapping
on a touchscreen. Re-adding a track you added months ago isn't detected — that would mean
reading the whole playlist on every tap. A stray duplicate is harmless anyway, since the
sorter handles duplicates correctly.

> Use it parked or hand it to a passenger. Adding a song is one tap, but reading a screen
> while driving isn't the point of this.

## 🔍 How the sorting works <a name="how"></a>

Each run reads a playlist, computes the shortest sequence of single-track **moves** that
brings the stored order in line with the date order, and applies them one at a time via
`playlist_reorder_items`.

- The plan is provably minimal — one move per track that is not part of a longest already-correct
  subsequence. A playlist sorted yesterday that gained three tracks overnight costs three
  API writes, not a rebuild.
- An already-sorted playlist costs one read and zero writes.
- Positions, not track ids, drive the whole thing, so duplicated tracks, local files and
  podcast episodes are all handled correctly.
- Each write carries the playlist's `snapshot_id`. If you add a track from your phone
  mid-run, Spotify rejects the stale write instead of shuffling the wrong rows.
- Rate limits (HTTP 429) and server errors are retried with backoff, honouring `Retry-After`.
- Tracks sharing an `added_at` keep their existing relative order, so they never churn.

**Each playlist has its own sort order**, chosen next to it in the UI — a running mix can
stay oldest-first while your main playlist stays newest-first. The *default order* setting
only applies to playlists as you select them; changing it never rewrites choices you've
already made. Settings written by an older version, which had a single global order, are
migrated on first start: every playlist inherits the order that was in force.

The stored form is a list of objects, so `config.json` stays hand-editable:

```json
{
  "playlists": [
    { "id": "37i9dQZF1DX0XUsuxWHRQd", "order": "newest_first" },
    { "id": "1AbcDefGhiJklMnoPqrStu", "order": "oldest_first" }
  ]
}
```

Playlists you don't own and that aren't collaborative can't be reordered by anyone; the UI
greys them out and runs report them as skipped.

## 📡 Spotify API notes <a name="apinotes"></a>

Spotify's 2026 Web API migration replaced `/playlists/{id}/tracks` with
`/playlists/{id}/items` for every verb; the old path now returns a bare `403 Forbidden`.
spoti-sort calls the new path directly rather than through spotipy's `playlist_items` /
`playlist_add_items` / `playlist_reorder_items` helpers, which still target the old one.

Two knock-on changes that show up in the UI:

- Playlist entries key on `item`, not `track`.
- Playlist objects no longer carry `tracks.total`, so the playlist picker shows no track
  count. Fetching one would cost an API call per playlist. Run results still report exact
  totals, since a sort reads the whole playlist anyway.

If you see `403 Forbidden` on playlist endpoints while `/me` works, check that your Spotify
app has **Web API** enabled and that your account is listed under **User Management** — a
development-mode app only works for accounts you add, including your own.

The Docker Hub page text lives in [DOCKERHUB.md](DOCKERHUB.md) — paste it into the
repository's **full description** when it changes, alongside the short description in that
file's header.

## 🔖 Versioning <a name="versioning"></a>

[SemVer](https://semver.org) on a 2.x line: **2** marks the rewritten generation — 1.x was
the original cron-driven script with no web UI. Minor releases add features, patch releases
fix them.

`spotisort/__init__.py` holds the version and is the single source of truth. The web UI
shows it next to the title, `GET /api/status` returns it, and the Docker image is tagged
from it — so `docker images` and the badge in the UI can never disagree.

Each release is published as `dahunni/spoti-sort:<version>` plus `:latest`.

## 🛠 Development <a name="development"></a>

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
CONFIG_DIR=./config .venv/bin/python app.py
```

```bash
python -m unittest discover -s tests -v
```

The tests cover the reorder planner, replaying every generated plan through a local model
of Spotify's `insert_before` semantics — including duplicates, null timestamps, ties and
5000-track playlists.

## ⛏️ Built using <a name="built_using"></a>

- [Python](https://python.org/) — programming language
- [Spotipy](https://github.com/plamere/spotipy/) — Spotify API integration
- [Flask](https://flask.palletsprojects.com/) + [waitress](https://docs.pylonsproject.org/projects/waitress/) — web UI
- [Docker](https://docker.com/) — packaging

## ✍️ Authors <a name="authors"></a>

- [@dahunni](https://github.com/dahunni) — idea & initial work

See also the list of [contributors](https://github.com/dahunni/spoti-sort/contributors)
who participated in this project.
