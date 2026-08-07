# spoti-sort

**Keeps Spotify playlists in date-added order — without deleting and re-adding a single track.**
Built for players that ignore sorting entirely, like the one in a Tesla.

[GitHub](https://github.com/dahunni/spoti-sort) · [Issues](https://github.com/dahunni/spoti-sort/issues) · GPLv3

---

## Why

Spotify's own clients can *display* a playlist in date-added order, but many players — the
Tesla media player, most car head units, plenty of speakers — always play it in **stored**
order. The usual workaround is to delete every track and re-add it in sequence, which
resets every *Date added* to today and destroys exactly the information you were sorting by.

spoti-sort reorders playlists **in place** through the Spotify API. Nothing is removed, so
timestamps, play counts and the playlist itself all survive.

## Features

- **In-place sorting** on a schedule, newest-first or oldest-first, **per playlist**
- **Minimal writes** — a playlist that gained 3 tracks costs 3 moves, not a rebuild; one
  already in order costs zero writes
- **Web UI** for setup, playlist selection and monitoring — no shell, no config files
- **Tesla page** — a bookmarkable page for the car that shows what's playing and adds it to
  a playlist with one tap, something the Tesla player can't do at all
- **Duplicate-aware** — a playlist that already contains the track is marked, not offered
- **Safe by construction** — handles duplicate tracks, local files and podcast episodes;
  every write carries a snapshot so a concurrent edit is rejected rather than misapplied
- Optional **password**, CSRF protection and login rate limiting for exposed instances

## Quick start

```bash
docker run -d \
  --name spoti-sort \
  -p 8080:8080 \
  -v /path/on/host:/config \
  --restart unless-stopped \
  dahunni/spoti-sort:latest
```

Or with Compose:

```yaml
services:
  spoti-sort:
    image: dahunni/spoti-sort:latest
    container_name: spoti-sort
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./config:/config
    environment:
      - TZ=Europe/Berlin
      # Set this if you reach the UI on anything other than http://127.0.0.1:8080
      # - PUBLIC_URL=http://192.168.1.50:8080
```

Then open **http://127.0.0.1:8080** and follow the setup card. It creates nothing until you
tell it to, and walks you through the Spotify app step by step.

> **Mount `/config`.** It holds your settings and the Spotify refresh token. Without it you
> will be setting the container up again after every restart.

## Setting up the Spotify app

You need your own Spotify app — Spotify only lets a program touch your playlists through
one. The UI shows the exact redirect URI to paste in, but two rules catch people out:

1. **Spotify only accepts an HTTPS redirect URI, or HTTP on a loopback literal**
   (`127.0.0.1`, `[::1]`). A plain `http://` LAN address, or `localhost`, is refused with
   *"Insecure redirect URI"*. spoti-sort therefore authorises over `127.0.0.1` regardless of
   the address you browse to; put the instance behind HTTPS and the redirect URI follows it.
   Setting up from another machine? The callback lands on a `127.0.0.1` page that can't
   load — that's expected. Paste that failed address into the **Finish** box in the UI.
2. **Add your own account** under **User Management** in the Spotify dashboard. A new app is
   in Development mode and works only for accounts you list — including your own. Without
   this, every API call returns `403 The user is not registered for this application`.

Also tick **Web API** when creating the app.

## Configuration

Everything can be set in the web UI. Environment variables are optional and win where both
exist.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLIENT_ID` | – | Spotify app client ID. Settable in the UI instead. |
| `CLIENT_SECRET` | – | Spotify app client secret. Settable in the UI instead. |
| `PUBLIC_URL` | – | Address the UI is actually reached on, e.g. `http://192.168.1.50:8080`. Settable in the UI instead. Drives the Tesla link, and the redirect URI when it is HTTPS. |
| `REDIRECT_URI` | `$PUBLIC_URL/callback` if HTTPS, else `http://127.0.0.1:$PORT/callback` | Overrides the choice entirely. Must match the Spotify app exactly. |
| `PORT` | `8080` | Port the UI listens on. |
| `HOST` | `0.0.0.0` | Bind address. |
| `CONFIG_DIR` | `/config` | Where settings and the token are stored. |
| `UI_PASSWORD` | – | If set, the UI asks for this password. Can be set in the UI instead, where it is stored as a PBKDF2 hash. |
| `TRUST_PROXY` | – | Set to `1` behind a reverse proxy so `X-Forwarded-Proto`/`-For` are honoured. |
| `PLAYLIST_IDS` | – | Seeds the playlist selection on first boot. Accepts ids, URLs or `spotify:` URIs. |
| `INTERVAL_MINUTES` | `60` | Overrides the schedule. |
| `SORT_ORDER` | `newest_first` | Default order for newly selected playlists. Per-playlist orders are set in the UI. |
| `LOG_LEVEL` | `INFO` | Python log level. |

**Volume:** `/config` — settings, OAuth token, session key.
**Port:** `8080`.

## The Tesla page

A single bookmarkable page for the car: it shows what's playing and adds it to one of your
enabled playlists with one tap. The link carries its own access key, so the car never signs
in — which is also why **a UI password is required before a link can be created**, and why
removing the password revokes the link.

The link is deliberately narrow. It can read what's playing and add the current track to a
playlist you marked **Car**, or take that same track back off one. It cannot change
settings, reach other playlists, touch any track other than the one playing, or see your
credentials.

Added tracks land where the playlist's order says they belong — top of a newest-first
playlist, end of an oldest-first one — so they're in the right place immediately.

Because the car's browser can't paste and the link is too long to retype, **Create pairing
code** gives you four digits to type into the sign-in page from the car instead; it
redirects straight to the Tesla page. Single use, expires after eight hours, and only shown
as an input while a code is live.

## Tags and architectures

| Tag | Contents |
| --- | --- |
| `latest` | Newest release |
| `2.1.0` | Pinned release |

Published for **linux/amd64** and **linux/arm64** — the same tag works on a PC, a NAS and a
Raspberry Pi.

Versioning is [SemVer](https://semver.org) on a 2.x line: **2** marks the rewritten
generation (1.x was a cron-driven script with no web UI). Minor releases add features, patch
releases fix them. The running version is shown next to the title in the UI.

## A note on exposure

Set a password under **Access** before exposing this beyond your own network — the UI warns
you when it's reachable at a non-loopback address without one. Passwords are stored as
salted PBKDF2 hashes, logins are CSRF-protected and rate-limited with an escalating lockout,
and responses carry a strict CSP. Behind a reverse proxy set `TRUST_PROXY=1`.

## Source and licence

Source, full documentation and issues: **https://github.com/dahunni/spoti-sort**
Licensed under GPLv3.
