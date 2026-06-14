import httpx

from app.sources.archive import RawArchive
from app.sources.base import retrying
from app.sources.dto import RelationRef, SimilarRef, SourceHit, TagVote, WorkPayload
from app.sources.ratelimit import RateLimiter

API_URL = "https://graphql.anilist.co"
ORIGIN_TYPE = {"JP": "manga", "KR": "manhwa", "CN": "manhua", "TW": "manhua"}

MEDIA_QUERY = """
query ($id: Int, $search: String) {
  Media(id: $id, search: $search, type: MANGA) {
    id idMal format status source isLicensed chapters volumes
    countryOfOrigin hashtag updatedAt
    startDate { year month day } endDate { year month day }
    averageScore meanScore popularity favourites trending
    isAdult description(asHtml: false)
    title { romaji english native } synonyms siteUrl
    coverImage { extraLarge large medium color } bannerImage
    genres
    tags { name rank category isMediaSpoiler }
    rankings { rank type allTime year season context }
    recommendations(sort: RATING_DESC, perPage: 25) {
      nodes { rating mediaRecommendation { id type title { romaji english } } }
    }
    relations { edges { relationType node { id type title { romaji english } } } }
    externalLinks { site url }
  }
}
"""

SEARCH_QUERY = """
query ($search: String) {
  Page(perPage: 8) {
    media(search: $search, type: MANGA) {
      id title { english romaji } startDate { year } countryOfOrigin averageScore coverImage { medium }
    }
  }
}
"""

BROWSE_QUERY = """
query ($page: Int, $perPage: Int, $sort: [MediaSort]) {
  Page(page: $page, perPage: $perPage) {
    media(type: MANGA, sort: $sort, isAdult: false) {
      id title { english romaji }
    }
  }
}
"""

BROWSE_SORT = {"score": "SCORE_DESC", "popularity": "POPULARITY_DESC"}


def _node_title(node: dict) -> str:
    t = node.get("title") or {}
    return t.get("english") or t.get("romaji") or "?"


def parse_media(media: dict) -> WorkPayload:
    titles: dict[str, tuple[str, ...]] = {}
    t = media.get("title") or {}
    for kind in ("english", "romaji", "native"):
        if t.get(kind):
            titles[kind] = (t[kind],)
    if media.get("synonyms"):
        titles["synonym"] = tuple(media["synonyms"])

    tags = [
        TagVote(name=g, kind="genre", weight=1.0)
        for g in media.get("genres") or []
    ] + [
        TagVote(
            name=tag["name"],
            kind="tag",
            weight=(tag.get("rank") or 0) / 100,
            category=tag.get("category"),
        )
        for tag in media.get("tags") or []
        if not tag.get("isMediaSpoiler")
    ]

    similar = [
        SimilarRef(
            source="anilist",
            source_key=str(n["mediaRecommendation"]["id"]),
            title=_node_title(n["mediaRecommendation"]),
            votes=n.get("rating") or 0,
        )
        for n in (media.get("recommendations") or {}).get("nodes") or []
        if n.get("mediaRecommendation") and n["mediaRecommendation"].get("type") == "MANGA"
        and (n.get("rating") or 0) > 0  # downvoted recs are anti-signal, not similarity
    ]

    relations = [
        RelationRef(
            source="anilist",
            source_key=str(e["node"]["id"]),
            title=_node_title(e["node"]),
            rel_type=(e.get("relationType") or "related").lower(),
        )
        for e in (media.get("relations") or {}).get("edges") or []
        if e.get("node") and e["node"].get("type") == "MANGA"
    ]

    avg = media.get("averageScore")
    return WorkPayload(
        source="anilist",
        source_key=str(media["id"]),
        url=media.get("siteUrl") or f"https://anilist.co/manga/{media['id']}",
        titles=titles,
        type=ORIGIN_TYPE.get(media.get("countryOfOrigin")),
        year=(media.get("startDate") or {}).get("year"),
        release_month=(media.get("startDate") or {}).get("month"),
        status=(media.get("status") or "").lower() or None,
        chapters=media.get("chapters"),
        description=media.get("description"),
        cover_url=(media.get("coverImage") or {}).get("large"),
        banner_url=media.get("bannerImage"),
        cover_color=(media.get("coverImage") or {}).get("color"),
        is_adult=bool(media.get("isAdult")),
        score=avg / 10 if avg else None,
        score_votes=media.get("popularity") or 0,
        tags=tuple(tags),
        similar=tuple(similar),
        relations=tuple(relations),
        links=tuple((link["site"], link["url"]) for link in media.get("externalLinks") or []),
    )


def parse_hit(media: dict) -> SourceHit:
    return SourceHit(
        source="anilist",
        source_key=str(media["id"]),
        title=_node_title(media),
        year=(media.get("startDate") or {}).get("year"),
        type=ORIGIN_TYPE.get(media.get("countryOfOrigin")),
        score=(media.get("averageScore") or 0) / 10 or None,
        cover_url=(media.get("coverImage") or {}).get("medium"),
    )


class AniListSource:
    name = "anilist"

    def __init__(self, client: httpx.AsyncClient, archive: RawArchive,
                 limiter: RateLimiter | None = None):
        self._client = client
        self._archive = archive
        self._limiter = limiter or RateLimiter(1.0)

    async def _gql(self, query: str, variables: dict) -> dict:
        await self._limiter.wait()

        async def call():
            r = await self._client.post(API_URL, json={"query": query, "variables": variables})
            r.raise_for_status()
            data = r.json()
            if data.get("errors"):  # GraphQL errors arrive as HTTP 200
                raise RuntimeError(f"AniList GraphQL error: {data['errors'][0].get('message')}")
            return data

        return await retrying(call)

    async def search(self, query: str) -> list[SourceHit]:
        data = await self._gql(SEARCH_QUERY, {"search": query})
        return [parse_hit(m) for m in data["data"]["Page"]["media"]]

    async def browse_top(self, kind: str, pages: int = 10,
                         per_page: int = 50) -> list[tuple[str, str]]:
        """Top-ranked manga ids for the base list; slim query, every page archived."""
        out: list[tuple[str, str]] = []
        for page in range(1, pages + 1):
            data = await self._gql(BROWSE_QUERY, {
                "page": page, "perPage": per_page, "sort": [BROWSE_SORT[kind]]})
            self._archive.store("anilist", f"{kind}-p{page}", "browse", data)
            out += [(str(m["id"]), _node_title(m)) for m in data["data"]["Page"]["media"]]
        return out

    async def fetch(self, source_key: str) -> WorkPayload:
        data = await self._gql(MEDIA_QUERY, {"id": int(source_key)})
        self._archive.store("anilist", source_key, "media", data)
        return parse_media(data["data"]["Media"])
