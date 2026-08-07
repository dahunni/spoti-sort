# Architecture & handoff

Orientation for changing this codebase quickly. Read the **Landmines** section before
touching the sorter, the Spotify calls, or the Tesla page — most of them cost real
debugging time to find and are not obvious from the code alone.

## What it is

One long-running Python process that keeps Spotify playlists in date-added order by
**reordering them in place**, plus a web UI and a bookmarkable page for a car. There is no
database; all state is JSON in a mounted `/config` directory.

An earlier version (1.x) was a cron-driven script. Everything below is the 2.x rewrite.

## Getting productive in 60 seconds

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests     # ~84 tests, <3s, no network
.venv/bin/python tools/devserver.py                # full UI on a fake Spotify
```

`tools/devserver.py` runs the **real** app — every route, template and scheduler — against
an in-memory fake Spotify. No credentials, no network, no risk to a real account. It prints
the UI URL, the Tesla URL and the dev password. The fixture deliberately includes a
playlist that already contains the "playing" track (duplicate check), one that isn't
editable (skip path), and all four sort/add role combinations.

Useful flags: `--host 0.0.0.0` to open it on a phone or in the car, `--keep` to preserve
config between runs, `--no-connect` to work on the setup/OAuth flow.

## Layout

| Path | Responsibility |
| --- | --- |
| `app.py` | Entrypoint: logging, config, scheduler start, signal handling, waitress. |
| `spotisort/__init__.py` | `__version__` — **single source of truth** (UI badge, `/api/status`, Docker tag). |
| `spotisort/sorter.py` | Pure logic. Reorder planning, Spotify's `insert_move` semantics, result types. No I/O, no API calls. |
| `spotisort/spotify.py` | Every Spotify call. OAuth, paging, retries, the endpoint paths and payload shapes. |
| `spotisort/config.py` | `/config` persistence, env-var precedence, schema migration, URL/password helpers. |
| `spotisort/scheduler.py` | The single background worker. One run at a time, `next_run_at`, manual trigger. |
| `spotisort/security.py` | PBKDF2 hashing, login lockout, CSRF tokens, proxy trust. |
| `spotisort/web.py` | Flask app: routes, the `App` view-model, caches, all HTTP concerns. The biggest file; start here. |
| `spotisort/templates/` | `base` → `index`/`login`/`gone`. `tesla.html` is **standalone** (different layout, car browser). |
| `spotisort/static/` | `app.*` for the config UI, `tesla.*` for the car page. No build step, no dependencies. |
| `tests/test_sorter.py` | All tests. Pure-logic and config-level; no network. |
| `tools/devserver.py` | Fake-Spotify dev server. |
| `DOCKERHUB.md` | Docker Hub page text; paste on release. |

### Where things live

- **Adding a setting** → `DEFAULTS` in `config.py`, a property beside it, expose in
  `App.status()` (`web.py`), render in `index.html`, wire in `app.js`.
- **Changing what the car screen shows** → `App.targets()` and `/api/tesla/<t>/state` in
  `web.py`, then `renderTargets()` in `tesla.js`.
- **Changing sort behaviour** → `plan_moves()` in `sorter.py`. It is pure; test it directly.
- **A new Spotify call** → `spotify.py` only. Wrap it in `_retry`.

## Data model

`/config/config.json` — see `DEFAULTS` in `config.py`. The interesting part is `playlists`:

```json
{ "id": "37i9…", "order": "newest_first", "sort": true, "add": true,
  "favorite": false, "last_used": 1785830514.0 }
```

`sort` and `add` are **independent roles**: keep it in date order, and/or offer it on the
car page. Also in `/config`: `tokens.json` (OAuth), `state.json` (last run), `secret_key`.

**Migration rule:** `normalise_entries()` accepts every historical shape — bare id strings,
dicts without the role flags — and absent flags mean *true*. Any new field needs the same
treatment plus a test; users upgrade in place and must never lose configuration.

## Landmines

Spotify-side, all confirmed empirically:

1. **`/playlists/{id}/tracks` is dead.** The 2026 migration moved every verb to
   `/playlists/{id}/items` and the old path now 403s. spotipy still targets the old one, so
   `playlist_items` / `playlist_add_items` / `playlist_reorder_items` and the removal
   helpers are **bypassed** — see `_items_path()`. Entries key on `item`, not `track`.
   Removal payloads key on `items`, not `tracks`.
2. **`tracks.total` no longer exists** on playlist objects. Counts come from the items
   paging `total` (`playlist_total()`); the picker shows no count rather than a wrong zero.
3. **Redirect URI must be HTTPS or a loopback literal.** A plain `http://` LAN address or
   `localhost` is refused as *"Insecure redirect URI"*. Hence `redirect_uri` follows the
   public address only when it's HTTPS, and otherwise uses `127.0.0.1` — with a paste-the-
   code fallback for setting up from another machine.
4. **Development-mode apps 403 everything** until the account is added under User
   Management, with the message *"The user is not registered for this application"*.
5. **`insert_before` is evaluated before removal**, so a downward move lands at
   `insert_before - 1`. `apply_move()` is the local mirror of this; the tests replay every
   generated plan through it. Get this wrong and the local model silently desynchronises.
6. **Writes carry `snapshot_id`** so a concurrent edit is rejected instead of misapplied.
   Undo depends on it: it removes one copy *at a recorded position*, never all occurrences.

Browser-side, each of which was a real bug:

7. **CSP is `script-src 'self'`** — no inline scripts. Bootstrap data goes in a
   `<script type="application/json">` data block, which CSP does not treat as executable.
8. **`[hidden] { display: none !important }`** is required in the CSS, or a `display:flex`
   rule silently overrides the `hidden` attribute.
9. **Never wrap a row in a `<label>`** when it contains other controls: clicks on a
   `<select>` toggle the checkbox, and the `preventDefault` that stops it also stops the
   dropdown opening. Use explicit buttons.
10. **`navigator.clipboard` does not exist over plain http** to a LAN address — only
    secure contexts. Copy falls back to `execCommand`, then to selecting the text.
11. **The car browser loses cookies and local storage.** Anything that must survive
    (the "save to favourites" instruction) is stored server-side. The URL is the only
    durable state the car has, which is why the token is in it.

## Request flows

**OAuth** — `/connect` builds the authorize URL from the *saved* address (the UI persists a
pending one first, so the two can't disagree) → Spotify → `/callback` exchanges the code, or
the user pastes the callback URL into `/api/exchange`.

**Sort run** — scheduler → `App.run_sort()` → `sort_all()` over `config.sort_entries` → per
playlist: read items, `plan_moves()`, apply each move with the chained snapshot. Results are
persisted to `state.json` and refresh the membership cache for free.

**Car add** — `/api/tesla/<token>/state` returns now-playing plus targets (cover, favourite,
recency order, `contains`, `added`). `/add` refuses a duplicate, adds, then moves it to the
front for a newest-first playlist, and records position + snapshot so `/remove` can undo
exactly that copy.

### Caches in `web.py`

`PLAYLIST_CACHE_TTL` (playlist list), `NOW_PLAYING_TTL` (collapses car-page polling into one
upstream call), `MEMBERSHIP_TTL` (duplicate check; updated in place on our own writes),
`ADD_GUARD_SECONDS` (double-tap guard and undo records). All in-memory and safe to lose.

## Security model

Two independent doors:

- **UI** — optional password (PBKDF2, or `UI_PASSWORD`), session cookie, CSRF header on
  every state-changing request, escalating per-address lockout.
- **Tesla link** — a URL-borne token, no session. Deliberately narrow: read playback, and add
  or remove the currently playing track on a playlist marked `add`. **A UI password is
  required before a link can exist**, and removing the password revokes the link — the link
  bypasses login by design, so it must never be the only lock. Wrong tokens 404 (not 401)
  and are rate-limited.
- **Pairing code** — four digits, traded on the sign-in page for a redirect to the Tesla
  link, because the car's browser cannot paste. Grants that one URL and never a session.
  Single use, 8h TTL, and only rendered as an input while a code is live. The space is tiny
  (10,000), so it leans on two caps: the per-address lockout, and the code burning itself
  after `PAIRING_MAX_ATTEMPTS` misses from anywhere. Cleared whenever the link it points at
  is regenerated, disabled, or revoked with the password.

## Testing

`tests/test_sorter.py`, all offline. The valuable part is the planner: every generated plan
is replayed through `apply_move` and compared against a sorted reference over 500 randomised
playlists, plus duplicates, null timestamps, ties and 5000-track inputs, with an assertion
that the move count is minimal.

There are no browser tests — UI changes are verified by hand against `tools/devserver.py`.

## Releasing

1. Bump `__version__` in `spotisort/__init__.py` (SemVer, 2.x line).
2. `python -m unittest discover -s tests`
3. Merge to `main`, tag `vX.Y.Z`, push both.
4. Multi-arch build and push:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t dahunni/spoti-sort:X.Y.Z -t dahunni/spoti-sort:latest --push .
```

5. Paste `DOCKERHUB.md` into the Docker Hub full description if it changed.

On Apple Silicon + Colima this needs buildx and QEMU once:
`brew install docker-buildx`, symlink into `~/.docker/cli-plugins/`,
`docker run --privileged --rm tonistiigi/binfmt --install amd64`,
`docker buildx create --driver docker-container --use --bootstrap`.

## Conventions

British spelling in user-facing copy. Comments explain *why*, especially where the code
looks odd because an external constraint forced it — most of the Landmines are documented at
their call site too. Keep `DEFAULTS`, `App.status()` and the templates in step; the UI reads
everything from `status()`.
