"""Lazy local cache for cover/banner images: serve from data/covers, fetch on
first miss, redirect to the source CDN when fetching isn't possible."""

import html
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

router = APIRouter()

EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png",
              "image/webp": ".webp", "image/gif": ".gif"}
CACHE_HEADERS = {"Cache-Control": "public, max-age=2592000"}


def _placeholder(conn, work_id: int) -> str:
    """Stand-in for never-fetched covers: hashed hue + title initial."""
    row = conn.execute(
        "SELECT canonical_title FROM works WHERE id=?", (work_id,)).fetchone()
    initial = html.escape((row["canonical_title"][:1] if row else "?").upper() or "?")
    hue = (work_id * 137) % 360
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="92" height="128">'
        f'<rect width="100%" height="100%" fill="hsl({hue},30%,32%)" rx="6"/>'
        f'<text x="50%" y="50%" dy=".35em" text-anchor="middle"'
        f' font-family="system-ui" font-size="44" fill="hsl({hue},45%,80%)">{initial}</text></svg>'
    )


def _cached(covers_dir: Path, stem: str) -> Path | None:
    return next(iter(covers_dir.glob(f"{stem}.*")), None)


async def _serve(request: Request, work_id: int, column: str, stem: str) -> Response:
    covers_dir: Path = request.app.state.covers_dir
    if (path := _cached(covers_dir, stem)) is not None:
        return FileResponse(path, headers=CACHE_HEADERS)
    row = request.app.state.catalog.execute(
        f"SELECT {column} AS url FROM works WHERE id=?", (work_id,)  # noqa: S608 — fixed column
    ).fetchone()
    if row is None or not row["url"]:
        # never long-cache the stand-in: the real cover usually lands minutes
        # later (crawl) and a cached placeholder would mask it for a month
        return Response(_placeholder(request.app.state.catalog, work_id),
                        media_type="image/svg+xml", headers={"Cache-Control": "no-store"})
    client: httpx.AsyncClient | None = request.app.state.client
    if client is None:
        return RedirectResponse(row["url"])
    try:
        r = await client.get(row["url"])
        r.raise_for_status()
    except httpx.HTTPError:
        return RedirectResponse(row["url"])
    ext = EXTENSIONS.get(r.headers.get("content-type", "").split(";")[0], ".jpg")
    path = covers_dir / f"{stem}{ext}"
    path.write_bytes(r.content)
    return FileResponse(path, headers=CACHE_HEADERS)


@router.get("/covers/{work_id}/banner")
async def banner(request: Request, work_id: int) -> Response:
    return await _serve(request, work_id, "banner_url", f"{work_id}-banner")


@router.get("/covers/{work_id}")
async def cover(request: Request, work_id: int) -> Response:
    return await _serve(request, work_id, "cover_url", str(work_id))
