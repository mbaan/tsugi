import httpx
import pytest

from app.sources.base import retrying


def _status_error(headers=None):
    request = httpx.Request("GET", "https://example.test")
    response = httpx.Response(429, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("rate limited", request=request, response=response)


async def test_retries_then_succeeds_with_numeric_retry_after():
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _status_error({"Retry-After": "7"})
        return "ok"

    assert await retrying(fn, sleep=fake_sleep) == "ok"
    assert sleeps == [7.0]


async def test_http_date_retry_after_falls_back_to_backoff():
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _status_error({"Retry-After": "Wed, 10 Jun 2026 12:00:00 GMT"})
        return "ok"

    assert await retrying(fn, sleep=fake_sleep) == "ok"
    assert sleeps == [1.0]  # 1.5**0


async def test_last_attempt_reraises():
    async def fake_sleep(s):
        pass

    async def fn():
        raise httpx.ConnectError("boom")

    with pytest.raises(httpx.ConnectError):
        await retrying(fn, attempts=3, sleep=fake_sleep)
