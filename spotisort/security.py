"""Password hashing, login throttling and CSRF, for instances exposed beyond a LAN."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Dict, Optional, Tuple

PBKDF2_ROUNDS = 240_000
SALT_BYTES = 16

# Login throttling. Deliberately per-source-address and in memory: a restart clears
# it, which is fine — the point is to make online guessing impractical, not to be a
# durable ban list.
MAX_FAILURES = 5
BASE_LOCKOUT = 15.0      # seconds, doubled per failure past the threshold
MAX_LOCKOUT = 3600.0


def hash_password(password: str) -> str:
    """PBKDF2-SHA256, stored as ``pbkdf2_sha256$rounds$salt$hash``."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return "pbkdf2_sha256$%d$%s$%s" % (PBKDF2_ROUNDS, salt.hex(), digest.hex())


def verify_password(password: str, stored: str) -> bool:
    if not stored or not password:
        return False
    try:
        scheme, rounds, salt_hex, digest_hex = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


class LoginLimiter:
    """Exponential lockout per client address after repeated failures."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: Dict[str, Tuple[int, float]] = {}   # key -> (failures, locked_until)

    def retry_after(self, key: str) -> float:
        """Seconds the caller must wait, 0 if they may try now."""
        with self._lock:
            failures, until = self._state.get(key, (0, 0.0))
        return max(0.0, until - time.time())

    def record_failure(self, key: str) -> float:
        with self._lock:
            failures, _ = self._state.get(key, (0, 0.0))
            failures += 1
            delay = 0.0
            if failures >= MAX_FAILURES:
                delay = min(MAX_LOCKOUT, BASE_LOCKOUT * (2 ** (failures - MAX_FAILURES)))
            self._state[key] = (failures, time.time() + delay)
            self._prune()
            return delay

    def record_success(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)

    def _prune(self) -> None:
        # Called under the lock. Drop entries that are no longer locked and stale.
        now = time.time()
        for key in [k for k, (_, until) in self._state.items() if until and until + 3600 < now]:
            del self._state[key]


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(supplied: Optional[str], expected: Optional[str]) -> bool:
    if not supplied or not expected:
        return False
    return hmac.compare_digest(str(supplied), str(expected))


def client_key(remote_addr: Optional[str]) -> str:
    return remote_addr or "unknown"


def trust_proxy() -> int:
    """Number of proxy hops to trust for X-Forwarded-*; 0 disables."""
    raw = (os.environ.get("TRUST_PROXY") or "").strip()
    if raw.isdigit():
        return int(raw)
    return 1 if raw.lower() in ("1", "true", "yes", "on") else 0
