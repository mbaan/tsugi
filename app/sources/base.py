import asyncio
from typing import Protocol

import httpx

from app.sources.dto import SourceHit, WorkPayload


class Source(Protocol):
    name: str

    async def search(self, query: str) -> list[SourceHit]: ...

    async def fetch(self, source_key: str) -> WorkPayload: ...


async def retrying(fn, attempts: int = 3, sleep=asyncio.sleep):
    """Run async fn() with retries; honors Retry-After on 429."""
    for attempt in range(attempts):
        try:
            return await fn()
        except httpx.HTTPStatusError as exc:
            if attempt == attempts - 1:
                raise
            retry_after = exc.response.headers.get("Retry-After")
            try:
                delay = float(retry_after) if retry_after else 1.5**attempt
            except ValueError:  # Retry-After may be an HTTP-date (RFC 7231)
                delay = 1.5**attempt
            await sleep(delay)
        except httpx.HTTPError:
            if attempt == attempts - 1:
                raise
            await sleep(1.5**attempt)
