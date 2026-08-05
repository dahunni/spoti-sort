"""Container entrypoint: one process serving the UI and owning the schedule."""

from __future__ import annotations

import logging
import os
import signal
import sys

from spotisort.config import Config
from spotisort.web import create_app


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # Unbuffered so `docker logs` shows output as it happens. The old setup lost
    # cron's output entirely because the daemon was started from a `docker exec`.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except AttributeError:
        pass

    log = logging.getLogger("spotisort")
    config = Config()
    app = create_app(config)
    state = app.extensions["spotisort"]

    state.scheduler.start(run_now=config.run_on_start and bool(config.sort_entries))

    def shutdown(signum, _frame):
        log.info("signal %s received, stopping", signum)
        state.scheduler.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    host = os.environ.get("HOST", "0.0.0.0")
    log.info("spoti-sort listening on http://%s:%d", host, config.port)
    log.info("redirect URI in use: %s", config.redirect_uri)
    if not config.has_credentials:
        log.info("no Spotify credentials yet - open the web UI to enter them")

    from waitress import serve
    serve(app, host=host, port=config.port, threads=8, ident="spoti-sort")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
