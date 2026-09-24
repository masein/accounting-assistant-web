"""In-memory rate limiter for auth and API endpoints.

Sliding window per identity key. Two styles of use:

* ``is_allowed(key)`` — check-and-record in one step (API throttling).
* ``would_allow(key)`` / ``hit(key)`` / ``reset(key)`` — check first, record
  only on failure, clear on success (login brute-force protection: five wrong
  passwords lock the *attempts*, five right ones must not lock the user out).

Idle identities are pruned periodically so the map cannot grow without bound
(security review 2026-09-24, L4). Process-local: with several workers each
worker keeps its own counts (roadmap §2.2 moves this to a shared store).
"""
from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:
    """Simple sliding-window rate limiter (per identity key)."""

    _PRUNE_EVERY = 500

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._ops = 0

    def _live(self, identity: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        hits = self._hits[identity] = [t for t in self._hits[identity] if t > cutoff]
        self._ops += 1
        if self._ops % self._PRUNE_EVERY == 0:
            self._prune(cutoff)
        return hits

    def _prune(self, cutoff: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[key]

    def would_allow(self, identity: str) -> bool:
        """Is another attempt within the limit? Records nothing."""
        return len(self._live(identity, time.monotonic())) < self.max_requests

    def hit(self, identity: str) -> None:
        """Record one attempt."""
        self._live(identity, time.monotonic()).append(time.monotonic())

    def reset(self, identity: str) -> None:
        self._hits.pop(identity, None)

    def is_allowed(self, identity: str) -> bool:
        """Check and record in one step."""
        now = time.monotonic()
        hits = self._live(identity, now)
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        return True

    def remaining(self, identity: str) -> int:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        hits = [t for t in self._hits.get(identity, []) if t > cutoff]
        return max(0, self.max_requests - len(hits))
