import asyncio

from app.sources.ratelimit import RateLimiter


async def test_first_call_does_not_sleep():
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    clock_value = [100.0]
    limiter = RateLimiter(1.0, sleep=fake_sleep, clock=lambda: clock_value[0])
    await limiter.wait()
    assert sleeps == []


async def test_second_call_within_interval_sleeps_remainder():
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)
        clock_value[0] += s

    clock_value = [100.0]
    limiter = RateLimiter(1.0, sleep=fake_sleep, clock=lambda: clock_value[0])
    await limiter.wait()
    clock_value[0] += 0.25
    await limiter.wait()
    assert sleeps == [0.75]


async def test_steady_state_after_full_interval_does_not_sleep():
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
        clock_value[0] += s

    clock_value = [100.0]
    limiter = RateLimiter(1.0, sleep=fake_sleep, clock=lambda: clock_value[0])
    await limiter.wait()
    clock_value[0] += 1.0
    await limiter.wait()
    assert sleeps == []


async def test_concurrent_waiters_each_reserve_a_slot():
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    clock_value = [100.0]
    limiter = RateLimiter(1.0, sleep=fake_sleep, clock=lambda: clock_value[0])
    await asyncio.gather(limiter.wait(), limiter.wait(), limiter.wait())
    assert sleeps == [1.0, 2.0]
