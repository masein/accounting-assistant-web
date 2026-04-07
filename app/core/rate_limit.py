"""In-memory rate limiter for auth and API endpoints."""
from __future__ import annotations

import time
from collections import defaultdict


class RateLimiter:
    """Simple sliding-window rate limiter (per identity key)."""

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, identity: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        # Prune old hits
        hits = self._hits[identity] = [t for t in self._hits[identity] if t > cutoff]
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        return True

    def remaining(self, identity: str) -> int:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        hits = [t for t in self._hits.get(identity, []) if t > cutoff]
        return max(0, self.max_requests - len(hits))
