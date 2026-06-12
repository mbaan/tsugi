"""One-shot: capture real API payloads as test fixtures. Run from project root."""

import json
from pathlib import Path

import httpx

OUT = Path("tests/fixtures")
HEADERS = {"User-Agent": "tsugi/0.1 (personal project)"}

ANILIST_MEDIA_QUERY = """
query ($id: Int, $search: String) {
  Media(id: $id, search: $search, type: MANGA) {
    id format status countryOfOrigin startDate { year }
    averageScore popularity favourites isAdult description(asHtml: false)
    title { romaji english native } synonyms siteUrl
    coverImage { large }
    genres
    tags { name rank category isMediaSpoiler }
    recommendations(sort: RATING_DESC, perPage: 25) {
      nodes { rating mediaRecommendation { id type title { romaji english } } }
    }
    relations { edges { relationType node { id type title { romaji english } } } }
    externalLinks { site url }
  }
}
"""

ANILIST_SEARCH_QUERY = """
query ($search: String) {
  Page(perPage: 8) {
    media(search: $search, type: MANGA) {
      id title { english romaji } startDate { year } countryOfOrigin averageScore
    }
  }
}
"""


def save(name: str, payload: dict) -> None:
    (OUT / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote tests/fixtures/{name}")


def main() -> None:
    with httpx.Client(headers=HEADERS, timeout=30) as client:
        r = client.post(
            "https://graphql.anilist.co",
            json={"query": ANILIST_MEDIA_QUERY, "variables": {"id": 105398}},
        )
        r.raise_for_status()
        save("anilist_media.json", r.json())

        r = client.post(
            "https://graphql.anilist.co",
            json={"query": ANILIST_SEARCH_QUERY, "variables": {"search": "Solo Leveling"}},
        )
        r.raise_for_status()
        save("anilist_search.json", r.json())

        r = client.post(
            "https://api.mangaupdates.com/v1/series/search",
            json={"search": "Solo Leveling", "stype": "title", "perpage": 8},
        )
        r.raise_for_status()
        save("mu_search.json", r.json())

        r = client.get("https://api.mangaupdates.com/v1/series/15180124327")
        r.raise_for_status()
        save("mu_series.json", r.json())


if __name__ == "__main__":
    main()
