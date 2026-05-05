"""Global async rate limiter for Gemini API calls.

Uses a token-bucket algorithm capped at 4 requests/minute (conservative margin
below the free-tier 5 req/min limit). Excess calls are queued, not rejected.
"""

import asyncio
import time

# ---------- Configuration ----------
MAX_REQUESTS_PER_MINUTE = 4
_REFILL_INTERVAL = 60.0 / MAX_REQUESTS_PER_MINUTE  # seconds between token refills


class _TokenBucket:
    """Async-safe token bucket shared across all agents."""

    def __init__(self, rate: int = MAX_REQUESTS_PER_MINUTE):
        self._rate = rate
        self._tokens = rate
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        new_tokens = elapsed / _REFILL_INTERVAL
        if new_tokens >= 1:
            self._tokens = min(self._rate, self._tokens + int(new_tokens))
            self._last_refill = now

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
            # No token available — wait for next refill window
            await asyncio.sleep(_REFILL_INTERVAL / 2)


# Module-level singleton
_bucket: _TokenBucket | None = None


def get_rate_limiter() -> _TokenBucket:
    """Return the global rate limiter singleton (creates on first call)."""
    global _bucket
    if _bucket is None:
        _bucket = _TokenBucket()
    return _bucket


# Synchronous wrapper for use in threaded translate() calls
_sync_lock = None


def _get_sync_lock():
    global _sync_lock
    if _sync_lock is None:
        import threading
        _sync_lock = threading.Lock()
    return _sync_lock


# Simple sync rate limiter for thread-pool usage
_sync_timestamps: list[float] = []


def sync_acquire() -> None:
    """Block until a Gemini request slot is available (thread-safe)."""
    lock = _get_sync_lock()
    while True:
        with lock:
            now = time.monotonic()
            # Remove timestamps older than 60 seconds
            while _sync_timestamps and _sync_timestamps[0] < now - 60.0:
                _sync_timestamps.pop(0)
            if len(_sync_timestamps) < MAX_REQUESTS_PER_MINUTE:
                _sync_timestamps.append(now)
                return
            # Calculate how long to wait for the oldest slot to expire
            wait_time = _sync_timestamps[0] - (now - 60.0)
        time.sleep(wait_time)
