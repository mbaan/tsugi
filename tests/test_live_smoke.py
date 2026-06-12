"""Optional live checks against the real APIs. Run with: uv run pytest -m live"""

import httpx
import pytest

from app import db
from app.sources.anilist import AniListSource
from app.sources.archive import RawArchive
from app.sources.mangaupdates import MangaUpdatesSource

pytestmark = pytest.mark.live


@pytest.fixture
async def live_client():
    async with httpx.AsyncClient(
        headers={"User-Agent": "tsugi/0.1 (personal project)"}, timeout=30
    ) as client:
        yield client


@pytest.fixture
def live_archive(tmp_path):
    conn = db.connect(tmp_path / "archive.sqlite")
    db.init_archive(conn)
    return RawArchive(conn)


async def test_anilist_live(live_client, live_archive):
    p = await AniListSource(live_client, live_archive).fetch("105398")
    assert "Solo Leveling" in p.titles["english"]
    assert p.similar


async def test_mangaupdates_live(live_client, live_archive):
    p = await MangaUpdatesSource(live_client, live_archive).fetch("15180124327")
    assert p.type == "manhwa"
    assert p.tags
