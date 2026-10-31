from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Optional


class SlidingWindow:
    def __init__(self, rpm: int, window: float = 60.0):
        self.rpm = max(0, int(rpm))
        self.window = max(0.1, float(window))
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        while self._events and self._events[0] <= cutoff:
            self._events.popleft()

    def peek(self, now: Optional[float] = None) -> tuple[bool, float, int]:
        if self.rpm <= 0:
            return True, 0.0, -1
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(now)
            if len(self._events) >= self.rpm:
                retry = max(0.0, self.window - (now - self._events[0]))
                return False, retry, 0
            return True, 0.0, self.rpm - len(self._events)

    def acquire(self, now: Optional[float] = None) -> tuple[bool, float, int]:
        if self.rpm <= 0:
            return True, 0.0, -1
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(now)
            if len(self._events) >= self.rpm:
                retry = max(0.0, self.window - (now - self._events[0]))
                return False, retry, 0
            self._events.append(now)
            return True, 0.0, self.rpm - len(self._events)

    def release(self, now: Optional[float] = None) -> None:
        if self.rpm <= 0:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            self._prune(now)
            if self._events:
                self._events.pop()


class RateLimitResult:
    def __init__(
        self,
        allowed: bool,
        retry_after: float = 0.0,
        limit: int = -1,
        remaining: int = -1,
        queued: bool = False,
        queued_ms: int = 0,
        queue_full: bool = False,
        queue_position: int = 0,
    ):
        self.allowed = allowed
        self.retry_after = max(0.0, retry_after)
        self.limit = limit
        self.remaining = remaining
        self.queued = queued
        self.queued_ms = max(0, int(queued_ms))
        self.queue_full = bool(queue_full)
        self.queue_position = max(0, int(queue_position))

    @property
    def retry_after_int(self) -> int:
        return max(1, int(math.ceil(self.retry_after))) if not self.allowed else 0


class RateLimiter:
    def __init__(
        self,
        default_rpm: int,
        provider_rpms: Optional[dict[str, int]] = None,
        window: float = 60.0,
    ):
        self.default_rpm = max(0, int(default_rpm or 0))
        self.window = max(0.1, float(window))
        self._global = SlidingWindow(self.default_rpm, self.window)
        self._providers = {
            name: SlidingWindow(rpm, self.window)
            for name, rpm in (provider_rpms or {}).items()
        }
        self._lock = threading.RLock()

    def _windows(self, provider: str) -> list[SlidingWindow]:
        windows: list[SlidingWindow] = []
        if self.default_rpm > 0:
            windows.append(self._global)
        if provider:
            provider_window = self._providers.get(provider)
            if provider_window is None and self.default_rpm > 0:
                provider_window = self._providers.setdefault(
                    provider, SlidingWindow(self.default_rpm, self.window)
                )
            if provider_window is not None:
                windows.append(provider_window)
        return windows

    def try_acquire(self, provider: str) -> RateLimitResult:
        windows = self._windows(provider)
        if not windows:
            return RateLimitResult(True, limit=-1, remaining=-1)
        with self._lock:
            retry = 0.0
            for window in windows:
                allowed, window_retry, _ = window.peek()
                if not allowed:
                    retry = max(retry, window_retry)
            if retry > 0 or any(not w.peek()[0] for w in windows):
                return RateLimitResult(False, retry, windows[0].rpm, 0)
            remaining = -1
            for window in windows:
                _, _, left = window.acquire()
                if left >= 0:
                    remaining = left if remaining < 0 else min(remaining, left)
        return RateLimitResult(True, 0.0, windows[0].rpm, remaining)

    def acquire(self, provider: str) -> RateLimitResult:
        return self.try_acquire(provider)

    def preview(self, provider: str) -> RateLimitResult:
        windows = self._windows(provider)
        if not windows:
            return RateLimitResult(True, limit=-1, remaining=-1)
        with self._lock:
            retry = 0.0
            remaining = -1
            for window in windows:
                allowed, window_retry, left = window.peek()
                if not allowed:
                    retry = max(retry, window_retry)
                if left >= 0:
                    remaining = left if remaining < 0 else min(remaining, left)
        if retry > 0 or remaining == 0:
            return RateLimitResult(False, retry, windows[0].rpm, 0)
        return RateLimitResult(True, 0.0, windows[0].rpm, remaining)

    def stats(self) -> dict:
        with self._lock:
            return {
                "default_rpm": self.default_rpm,
                "window_seconds": self.window,
                "providers": {
                    name: {"rpm": window.rpm, "used": len(window._events)}
                    for name, window in self._providers.items()
                },
            }


class QueuedLimiter:
    """FIFO 等待队列包装器。

    达到 RPM 上限时不直接拒绝，而是先放入队列等待窗口释放配额；
    队列已满或等待超时才返回 429。
    """

    def __init__(
        self,
        limiter: RateLimiter,
        enabled: bool = True,
        max_size: int = 200,
        max_wait: float = 120.0,
    ):
        self.limiter = limiter
        self.enabled = bool(enabled)
        self.max_size = max(0, int(max_size))
        self.max_wait = max(0.0, float(max_wait))
        self._cond = threading.Condition()
        self._waiters: deque[dict] = deque()
        self._counter = 0

    def _is_first_for_provider(self, waiter: dict) -> bool:
        for item in self._waiters:
            if item["provider"] == waiter["provider"]:
                return item is waiter
        return True

    def acquire(self, provider: str) -> RateLimitResult:
        if not self.enabled or self.max_wait <= 0:
            return self.limiter.try_acquire(provider)

        started = time.monotonic()
        deadline = started + self.max_wait
        with self._cond:
            last = self.limiter.preview(provider)
            has_waiters = any(
                item["provider"] == provider for item in self._waiters
            )
            if last.allowed and not has_waiters:
                attempt = self.limiter.try_acquire(provider)
                if attempt.allowed:
                    return attempt
                last = attempt
            if self.max_size <= 0 or len(self._waiters) >= self.max_size:
                last.allowed = False
                last.queue_full = True
                if last.retry_after <= 0:
                    last.retry_after = self.limiter.window
                return last

            self._counter += 1
            waiter = {
                "id": self._counter,
                "provider": provider,
                "started": started,
            }
            self._waiters.append(waiter)
            try:
                while True:
                    if self._is_first_for_provider(waiter):
                        attempt = self.limiter.try_acquire(provider)
                        if attempt.allowed:
                            attempt.queued = True
                            attempt.queued_ms = int(
                                (time.monotonic() - started) * 1000
                            )
                            attempt.queue_position = 1
                            return attempt
                        last = attempt
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        last.allowed = False
                        last.queued = True
                        last.queued_ms = int((time.monotonic() - started) * 1000)
                        last.queue_position = self._position(waiter)
                        if last.retry_after <= 0:
                            last.retry_after = self.limiter.window
                        return last
                    self._cond.wait(min(0.25, remaining))
            finally:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass
                self._cond.notify_all()

    def _position(self, waiter: dict) -> int:
        for index, item in enumerate(self._waiters, start=1):
            if item is waiter:
                return index
        return 1

    def stats(self) -> dict:
        with self._cond:
            waiting = len(self._waiters)
            positions = {}
            for item in self._waiters:
                positions[item["provider"]] = positions.get(item["provider"], 0) + 1
        stats = self.limiter.stats()
        stats.update(
            {
                "queue_enabled": self.enabled,
                "queue_max_size": self.max_size,
                "queue_max_wait": self.max_wait,
                "queue_waiting": waiting,
                "queue_waiting_by_provider": positions,
            }
        )
        return stats
