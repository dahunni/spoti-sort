"""Reordering logic.

The playlist is sorted purely by *position*, never by track id. That matters:
a playlist may contain the same track twice, local files and podcast episodes
have a null id, and removed tracks come back from the API as ``"track": null``.
Any id-based bookkeeping falls apart on all four cases.

The plan is computed offline and then replayed against the API, so the number
of write calls is known up front and is provably minimal (see :func:`plan_moves`).
"""

from __future__ import annotations

import bisect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

# Only what we actually need. Spotify's 2026 migration renamed the per-entry key
# from `track` to `item`; episodes carry `name` too, and anything unavailable comes
# back as null and is handled by `_sort_key`.
ITEM_FIELDS = "next,items(added_at,item(id,name,uri))"


def item_uri(entry: Dict[str, Any]) -> Optional[str]:
    """The playable URI of a playlist entry, or None for local files."""
    return ((entry or {}).get("item") or {}).get("uri")

NEWEST_FIRST = "newest_first"
OLDEST_FIRST = "oldest_first"

Move = Tuple[int, int]  # (range_start, insert_before), in Spotify's coordinates


def apply_move(items: List[Any], range_start: int, insert_before: int, length: int = 1) -> List[Any]:
    """Replicate the Spotify reorder semantics on a local list.

    ``insert_before`` is expressed in the coordinate system *before* the slice is
    removed, so moving an item downwards lands it at ``insert_before - 1``. Getting
    this backwards silently desynchronises the local model from the real playlist.
    """
    chunk = items[range_start:range_start + length]
    rest = items[:range_start] + items[range_start + length:]
    if insert_before > range_start:
        insert_before -= length
    return rest[:insert_before] + chunk + rest[insert_before:]


def _lis_indices(seq: Sequence[int]) -> List[int]:
    """Indices of a longest strictly increasing subsequence of ``seq``."""
    tails_idx: List[int] = []   # tails_idx[k] = index of the smallest tail of an LIS of length k+1
    tails_val: List[int] = []
    prev = [-1] * len(seq)

    for i, value in enumerate(seq):
        k = bisect.bisect_left(tails_val, value)
        if k == len(tails_val):
            tails_val.append(value)
            tails_idx.append(i)
        else:
            tails_val[k] = value
            tails_idx[k] = i
        prev[i] = tails_idx[k - 1] if k > 0 else -1

    out: List[int] = []
    i = tails_idx[-1] if tails_idx else -1
    while i != -1:
        out.append(i)
        i = prev[i]
    out.reverse()
    return out


def _sort_key(added_at: Optional[str]) -> str:
    # Very old playlist entries can have a null `added_at`; treat them as oldest
    # rather than crashing on a None comparison.
    return added_at or ""


def plan_moves(added_ats: Sequence[Optional[str]], order: str = NEWEST_FIRST) -> List[Move]:
    """Return the single-item moves that sort the playlist, shortest plan first.

    Every element outside a longest increasing subsequence of the target ranks has
    to move at least once, and this emits exactly one move for each of them, so the
    plan is minimal. In practice a playlist that was sorted yesterday and has since
    gained three tracks costs three API calls, not one per track.
    """
    n = len(added_ats)
    if n < 2:
        return []

    reverse = order != OLDEST_FIRST
    # `sorted` is stable and `reverse=True` does not reverse ties, so tracks sharing
    # an `added_at` keep their existing relative order and never churn.
    target = sorted(range(n), key=lambda i: _sort_key(added_ats[i]), reverse=reverse)

    rank = [0] * n
    for position, original_index in enumerate(target):
        rank[original_index] = position

    keep = set(_lis_indices(rank))
    current = list(range(n))
    moves: List[Move] = []

    for position, element in enumerate(target):
        if element in keep:
            continue
        origin = current.index(element)
        if position == 0:
            insert_before = 0
        else:
            # Everything earlier in `target` is already in place relative to the
            # kept subsequence, so parking this item behind its predecessor is enough.
            insert_before = current.index(target[position - 1]) + 1
        if origin == insert_before or origin == insert_before - 1:
            continue  # already sitting where it belongs
        moves.append((origin, insert_before))
        current = apply_move(current, origin, insert_before)

    return moves


@dataclass
class PlaylistResult:
    playlist_id: str
    name: str = ""
    total: int = 0
    moves: int = 0
    status: str = "ok"          # ok | skipped | error
    detail: str = ""
    order: str = NEWEST_FIRST
    # Track URIs seen during the run, for the duplicate-check cache. Not serialised.
    uris: Optional[set] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "playlist_id": self.playlist_id,
            "name": self.name,
            "total": self.total,
            "moves": self.moves,
            "status": self.status,
            "detail": self.detail,
            "order": self.order,
        }


@dataclass
class RunResult:
    started_at: float = 0.0
    finished_at: float = 0.0
    playlists: List[PlaylistResult] = field(default_factory=list)

    @property
    def moves(self) -> int:
        return sum(p.moves for p in self.playlists)

    @property
    def ok(self) -> bool:
        return all(p.status != "error" for p in self.playlists)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration": round(self.finished_at - self.started_at, 1),
            "moves": self.moves,
            "ok": self.ok,
            "playlists": [p.as_dict() for p in self.playlists],
        }
