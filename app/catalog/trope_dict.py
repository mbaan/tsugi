"""Prefill the canonical trope dictionary from AniList (genres + tag collection).

AniList is the master vocabulary; MangaUpdates aliases into it via
canonical_tag_id's normalized matching. The raw response is archived so
catalog rebuilds replay it offline (no network during rebuild).
"""

import sqlite3

import httpx

from app.catalog.tags import fold_tag_name
from app.sources.archive import RawArchive

API_URL = "https://graphql.anilist.co"
ARCHIVE_KIND = "tag_collection"
DICTIONARY_QUERY = (
    "query { GenreCollection MediaTagCollection { name description category isAdult } }"
)
ADULT_GENRES = {"Hentai"}


def _upsert_entry(conn: sqlite3.Connection, *, name: str, kind: str,
                  description: str | None, category: str | None, is_adult: int) -> None:
    row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
    if row:
        # promote crawled tags: dictionary metadata wins, kind becomes authoritative
        conn.execute(
            "UPDATE tags SET kind=?, description=COALESCE(?, description),"
            " category=COALESCE(?, category), is_adult=? WHERE id=?",
            (kind, description, category, is_adult, row["id"]),
        )
        tag_id = row["id"]
    else:
        tag_id = conn.execute(
            "INSERT INTO tags(name, kind, category, description, is_adult, norm_name)"
            " VALUES(?,?,?,?,?,?)",
            (name, kind, category, description, is_adult, fold_tag_name(name)),
        ).lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO tag_aliases(source, source_tag_name, tag_id)"
        " VALUES('anilist', ?, ?)",
        (name, tag_id),
    )


def upsert_dictionary(conn: sqlite3.Connection, payload: dict) -> int:
    data = payload["data"]
    count = 0
    for genre in data.get("GenreCollection") or []:
        _upsert_entry(conn, name=genre, kind="genre", description=None,
                      category="Genre", is_adult=int(genre in ADULT_GENRES))
        count += 1
    for tag in data.get("MediaTagCollection") or []:
        _upsert_entry(conn, name=tag["name"], kind="trope",
                      description=tag.get("description"), category=tag.get("category"),
                      is_adult=int(bool(tag.get("isAdult"))))
        count += 1
    conn.commit()
    return count


def dictionary_present(conn: sqlite3.Connection) -> bool:
    # only dictionary imports write descriptions; crawled tags never have one
    row = conn.execute("SELECT EXISTS(SELECT 1 FROM tags WHERE description IS NOT NULL)")
    return row.fetchone()[0] == 1


async def import_dictionary(conn: sqlite3.Connection, client: httpx.AsyncClient,
                            archive: RawArchive) -> int:
    r = await client.post(API_URL, json={"query": DICTIONARY_QUERY})
    r.raise_for_status()
    data = r.json()
    if data.get("errors"):  # GraphQL errors arrive as HTTP 200
        raise RuntimeError(f"AniList GraphQL error: {data['errors'][0].get('message')}")
    archive.store("anilist", "all", ARCHIVE_KIND, data)
    return upsert_dictionary(conn, data)


async def import_dictionary_safe(conn: sqlite3.Connection, client: httpx.AsyncClient,
                                 archive: RawArchive) -> None:
    try:
        n = await import_dictionary(conn, client, archive)
        print(f"trope dictionary: imported {n} entries")
    except Exception as exc:  # picker degrades to indexed tags; retried next startup
        print(f"trope dictionary import failed: {exc!r}")


if __name__ == "__main__":
    import asyncio

    from app import db
    from app.config import load_config

    async def _main() -> None:
        cfg = load_config()
        catalog = db.connect(cfg.catalog_path)
        db.init_catalog(catalog)
        archive_conn = db.connect(cfg.archive_path)
        db.init_archive(archive_conn)
        async with httpx.AsyncClient(
            headers={"User-Agent": "tsugi/0.1 (personal project)"}, timeout=30
        ) as client:
            n = await import_dictionary(catalog, client, RawArchive(archive_conn))
        print(f"imported {n} dictionary entries")
        catalog.close()
        archive_conn.close()

    asyncio.run(_main())
