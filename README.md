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
- [How the sorting works](#how)
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
docker run -d --name spoti-sort -p 8080:8080 -v /path/on/host:/config ghcr.io/dahunni/spoti-sort:latest
```

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

It must match character for character. Use `127.0.0.1`, not `localhost`: Spotify no
longer accepts `localhost` for new apps.

Copy the Client ID and secret into the UI (or set them as environment variables), click
**Authorise with Spotify**, and pick your playlists. That's it — no shell, no manual
first-run script.

> **Reaching the UI on another address?** If you browse to `http://192.168.1.50:8080`
> or through a reverse proxy, set `REDIRECT_URI` to that address plus `/callback` and add
> the identical value to your Spotify app. The UI always displays the value in use.

## ⚙️ Configuration <a name="configuration"></a>

Everything can be set in the web UI. Environment variables are optional and take
precedence where both exist.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIENT_ID` | – | Spotify app client ID. Settable in the UI instead. |
| `CLIENT_SECRET` | – | Spotify app client secret. Settable in the UI instead. |
| `REDIRECT_URI` | `http://127.0.0.1:$PORT/callback` | Must match the Spotify app exactly. |
| `PORT` | `8080` | Port the UI listens on. |
| `HOST` | `0.0.0.0` | Bind address. |
| `CONFIG_DIR` | `/config` | Where settings and the token are stored. |
| `UI_PASSWORD` | – | If set, the UI asks for this password. |
| `PLAYLIST_IDS` | – | Seeds the playlist selection on first boot. Accepts ids, URLs or `spotify:` URIs, separated by commas or whitespace. |
| `INTERVAL_MINUTES` | `60` | Overrides the schedule. |
| `SORT_ORDER` | `newest_first` | `newest_first` or `oldest_first`. |
| `LOG_LEVEL` | `INFO` | Python log level. |

### A note on exposure

The UI has no authentication unless you set `UI_PASSWORD`, and anyone who reaches it can
reorder your playlists. Keep it on your LAN, or set a password before putting it behind a
reverse proxy.

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

Playlists you don't own and that aren't collaborative can't be reordered by anyone; the UI
greys them out and runs report them as skipped.

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
