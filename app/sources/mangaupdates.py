import httpx

from app.sources.archive import RawArchive
from app.sources.base import retrying
from app.sources.dto import RelationRef, SimilarRef, SourceHit, TagVote, WorkPayload
from app.sources.ratelimit import RateLimiter

API_URL = "https://api.mangaupdates.com/v1"
MU_TYPES = {"manga": "manga", "manhwa": "manhwa", "manhua": "manhua"}
ADULT_GENRES = {"Hentai", "Adult"}


def _year(value) -> int | None:
    try:
        return int(str(value)[:4])
    except (TypeError, ValueError):
        return None


def parse_series(d: dict) -> WorkPayload:
    genres = [g["genre"] for g in d.get("genres") or []]
    cats = d.get("categories") or []
    max_votes = max((c.get("votes_plus") or 0 for c in cats), default=1) or 1

    tags = [TagVote(name=g, kind="genre", weight=1.0) for g in genres] + [
        TagVote(
            name=c["category"],
            kind="trope",
            weight=min(1.0, (c.get("votes_plus") or 0) / max_votes),
            votes=c.get("votes"),
        )
        for c in cats
    ]

    similar = [
        SimilarRef(
            source="mangaupdates",
            source_key=str(r["series_id"]),
            title=r.get("series_name") or "?",
            votes=r.get("weight") or 0,
        )
        for r in d.get("category_recommendations") or []
        if r.get("series_id")
    ]

    relations = [
        RelationRef(
            source="mangaupdates",
            source_key=str(r["related_series_id"]),
            title=r.get("related_series_name") or "?",
            rel_type=(r.get("relation_type") or "related").strip().lower(),
        )
        for r in d.get("related_series") or []
        if r.get("related_series_id")
    ]

    return WorkPayload(
        source="mangaupdates",
        source_key=str(d["series_id"]),
        url=d.get("url") or f"https://www.mangaupdates.com/series/{d['series_id']}",
        titles={
            "english": (d["title"],),
            "synonym": tuple(a["title"] for a in d.get("associated") or []),
        },
        type=MU_TYPES.get((d.get("type") or "").lower()),
        year=_year(d.get("year")),
        status=(d.get("status") or "")[:80] or None,
        description=d.get("description"),
        cover_url=((d.get("image") or {}).get("url") or {}).get("original"),
        is_adult=bool(ADULT_GENRES & set(genres)),
        score=d.get("bayesian_rating"),
        score_votes=d.get("rating_votes") or 0,
        tags=tuple(tags),
        similar=tuple(similar),
        relations=tuple(relations),
    )


def parse_search(d: dict) -> list[SourceHit]:
    hits = []
    for item in d.get("results") or []:
        r = item.get("record") or {}
        if not r.get("series_id"):
            continue
        hits.append(
            SourceHit(
                source="mangaupdates",
                source_key=str(r["series_id"]),
                title=r.get("title") or "?",
                year=_year(r.get("year")),
                type=MU_TYPES.get((r.get("type") or "").lower()),
                score=r.get("bayesian_rating"),
                cover_url=((r.get("image") or {}).get("url") or {}).get("thumb"),
            )
        )
    return hits


class MangaUpdatesSource:
    name = "mangaupdates"

    def __init__(self, client: httpx.AsyncClient, archive: RawArchive,
                 limiter: RateLimiter | None = None):
        self._client = client
        self._archive = archive
        self._limiter = limiter or RateLimiter(1.0)

    async def search(self, query: str) -> list[SourceHit]:
        await self._limiter.wait()

        async def call():
            r = await self._client.post(
                f"{API_URL}/series/search",
                json={"search": query, "stype": "title", "perpage": 8},
            )
            r.raise_for_status()
            return r.json()

        return parse_search(await retrying(call))

    async def fetch(self, source_key: str) -> WorkPayload:
        await self._limiter.wait()

        async def call():
            r = await self._client.get(f"{API_URL}/series/{source_key}")
            r.raise_for_status()
            return r.json()

        data = await retrying(call)
        self._archive.store("mangaupdates", source_key, "series", data)
        return parse_series(data)
