"""spoti-sort.

Versioning is SemVer on a 2.x line. The 2 marks the rewritten generation: 1.x was
the original cron-driven script with no web UI. Minor releases add features, patch
releases fix them. This string is the single source of truth — the web UI reads it,
and the Docker image is tagged from it.
"""

__version__ = "2.1.1"
