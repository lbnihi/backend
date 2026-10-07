"""In-memory sliding-window rate limiter (per process; run a single uvicorn worker to keep limits exact)."""

import time
from collections import defaultdict, deque

from app.config import settings

IP_WINDOW_SECONDS = 60 * 60
PHONE_WINDOW_SECONDS = 24 * 60 * 60


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, window: int, now: float) -> deque[float]:
        hits = self._hits[key]
        while hits and now - hits[0] > window:
            hits.popleft()
        if not hits:
            self._hits.pop(key, None)
            return self._hits[key]
        return hits

    def is_allowed(self, key: str, limit: int, window: int) -> bool:
        return len(self._prune(key, window, time.monotonic())) < limit

    def hit(self, key: str) -> None:
        self._hits[key].append(time.monotonic())

    def reset(self) -> None:
        self._hits.clear()


limiter = SlidingWindowLimiter()


def check_order_limits(ip: str, phone: str) -> bool:
    """10 orders per IP per hour, 3 orders per phone per day."""
    return limiter.is_allowed(f"ip:{ip}", settings.order_rate_limit_per_ip, IP_WINDOW_SECONDS) and limiter.is_allowed(
        f"phone:{phone}", settings.order_rate_limit_per_phone, PHONE_WINDOW_SECONDS
    )


def record_order(ip: str, phone: str) -> None:
    limiter.hit(f"ip:{ip}")
    limiter.hit(f"phone:{phone}")


def check_contact_limit(ip: str) -> bool:
    key = f"contact:{ip}"
    if not limiter.is_allowed(key, 5, IP_WINDOW_SECONDS):
        return False
    limiter.hit(key)
    return True
