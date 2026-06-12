import asyncio
import time


class RateLimiter:
    """Minimum-interval limiter: at most one call per min_interval seconds.

    Safe for concurrent waiters: each caller reserves the next free slot
    under a lock, so gathered coroutines stay spaced by min_interval.
    """

    def __init__(self, min_interval: float, sleep=asyncio.sleep, clock=time.monotonic):
        self._min = min_interval
        self._sleep = sleep
        self._clock = clock
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = self._clock()
            slot = max(now, self._next)
            self._next = slot + self._min
        delay = slot - now
        if delay > 0:
            await self._sleep(delay)
