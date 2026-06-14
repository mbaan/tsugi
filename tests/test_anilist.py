import asyncio
import json

import httpx

from app.sources.anilist import AniListSource, parse_hit, parse_media
from app.sources.archive import RawArchive
from app.sources.dto import WorkPayload
from app.sources.ratelimit import RateLimiter
from tests.conftest import load_fixture


def test_workpayload_constructs_with_defaults():
    p = WorkPayload(source="anilist", source_key="1", url="u", titles={"english": ("X",)})
    assert p.is_adult is False
    assert p.tags == ()


def media_fixture() -> dict:
    return load_fixture("anilist_media.json")["data"]["Media"]


def test_parse_media_core_fields():
    p = parse_media(media_fixture())
    assert p.source == "anilist"
    assert p.source_key == "105398"
    assert p.type == "manhwa"  # countryOfOrigin KR
    assert p.year == 2018
    assert "Solo Leveling" in p.titles["english"]
    assert len(p.titles["synonym"]) >= 5
    assert p.score == 8.4  # averageScore 84 / 10
    assert p.score_votes > 100_000  # popularity proxy
    assert p.is_adult is False


def test_parse_media_tags_weighted_and_genres():
    p = parse_media(media_fixture())
    by_name = {t.name: t for t in p.tags}
    assert by_name["Dungeon"].weight == 0.95
    assert by_name["Dungeon"].kind == "tag"
    assert by_name["Action"].kind == "genre"
    assert by_name["Action"].weight == 1.0
    assert all(0.0 <= t.weight <= 1.0 for t in p.tags)


def test_parse_media_similar_votes_and_relations():
    p = parse_media(media_fixture())
    top = p.similar[0]
    assert top.votes > 1000  # Omniscient Reader edge
    assert top.source == "anilist"
    rel_types = {r.rel_type for r in p.relations}
    assert "sequel" in rel_types
    # anime adaptations (type ANIME) must be excluded: fixture has 4 relation
    # edges of which 1 are type MANGA
    assert len(p.relations) == 1


def test_parse_media_skips_spoiler_tags():
    media = media_fixture()
    media["tags"].append({"name": "Spoiler Thing", "rank": 50, "category": "X", "isMediaSpoiler": True})
    p = parse_media(media)
    assert "Spoiler Thing" not in {t.name for t in p.tags}


def test_parse_search_hit():
    page = load_fixture("anilist_search.json")["data"]["Page"]["media"]
    hit = parse_hit(page[0])
    assert hit.source == "anilist"
    assert hit.title
    assert hit.type in {"manga", "manhwa", "manhua", None}


def test_parse_media_extracts_banner_and_color():
    media = load_fixture("anilist_media.json")["data"]["Media"]
    wp = parse_media(media)
    assert wp.banner_url == "https://img.example/banner.jpg"
    assert wp.cover_color == "#e4a15d"


def test_parse_hit_extracts_cover():
    media = load_fixture("anilist_search.json")["data"]["Page"]["media"][0]
    assert parse_hit(media).cover_url == "https://img.example/m1.jpg"


def test_parse_media_skips_downvoted_recommendations():
    media = media_fixture()
    media["recommendations"]["nodes"].append({
        "rating": -5,
        "mediaRecommendation": {"id": 999, "type": "MANGA", "title": {"romaji": "Bad Rec"}},
    })
    p = parse_media(media)
    assert all(s.votes > 0 for s in p.similar)


def test_parse_media_extracts_release_month():
    p = parse_media(media_fixture())
    assert p.release_month == 3


def test_parse_media_release_month_none_when_absent():
    media = media_fixture()
    media["startDate"] = {"year": 2018}
    assert parse_media(media).release_month is None


def test_browse_top_pages_archives_and_filters_adult(archive_db):
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        page = body["variables"]["page"]
        media = [{"id": page * 100 + i, "title": {"english": f"T{page}-{i}", "romaji": None}}
                 for i in range(2)]
        return httpx.Response(200, json={"data": {"Page": {"media": media}}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    src = AniListSource(client, RawArchive(archive_db), RateLimiter(0))
    out = asyncio.run(src.browse_top("score", pages=2, per_page=2))
    assert [v["variables"]["page"] for v in seen] == [1, 2]
    assert all(v["variables"]["sort"] == ["SCORE_DESC"] for v in seen)
    assert "isAdult: false" in seen[0]["query"]
    assert out == [("100", "T1-0"), ("101", "T1-1"), ("200", "T2-0"), ("201", "T2-1")]
    kinds = [r["kind"] for r in archive_db.execute("SELECT kind FROM source_raw")]
    assert kinds == ["browse", "browse"]
    out = asyncio.run(src.browse_top("popularity", pages=1, per_page=2))
    assert seen[-1]["variables"]["sort"] == ["POPULARITY_DESC"]
