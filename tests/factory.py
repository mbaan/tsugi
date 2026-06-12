"""Builders for catalog rows and payloads used across test modules."""

import sqlite3

from app.sources.dto import WorkPayload


def make_payload(source="anilist", source_key="1", title="Alpha", year=2020,
                 type="manhwa", score=8.5, score_votes=5000, **kw) -> WorkPayload:
    titles = kw.pop("titles", {"english": (title,)})
    return WorkPayload(
        source=source, source_key=source_key, url=f"https://example/{source_key}",
        titles=titles, year=year, type=type, score=score, score_votes=score_votes, **kw,
    )


def make_work(conn: sqlite3.Connection, title="Alpha", type="manhwa", year=2020,
              quality=8.0, quality_votes=1000, is_stub=0, is_adult=0) -> int:
    from app.catalog.resolve import normalize_title

    # is_stub=0 (unlike the schema's DEFAULT 1): factory works represent fully
    # fetched entries; pass is_stub=1 explicitly to model crawl-frontier stubs.
    work_id = conn.execute(
        "INSERT INTO works(canonical_title, type, year, quality, quality_votes, is_stub, is_adult)"
        " VALUES(?,?,?,?,?,?,?)",
        (title, type, year, quality, quality_votes, is_stub, is_adult),
    ).lastrowid
    conn.execute("UPDATE works SET franchise_id=? WHERE id=?", (work_id, work_id))
    conn.execute(
        "INSERT INTO work_titles(work_id, title, norm_title, kind, source)"
        " VALUES(?,?,?,?,?)",
        (work_id, title, normalize_title(title), "english", "test"),
    )
    conn.commit()
    return work_id


def link_source(conn, work_id, source, source_key, fetched=True):
    conn.execute(
        "INSERT INTO work_sources(work_id, source, source_key, last_fetched_at)"
        " VALUES(?,?,?,CASE WHEN ? THEN datetime('now') END)",
        (work_id, source, source_key, fetched),
    )
    conn.commit()


def link_similar(conn, a, b, votes, source="anilist"):
    conn.execute(
        "INSERT OR REPLACE INTO similarities(from_work_id, to_work_id, source, votes)"
        " VALUES(?,?,?,?)",
        (a, b, source, votes),
    )
    conn.commit()


def link_tag(conn, work_id, name, weight, votes=None, kind="trope"):
    from app.catalog.tags import canonical_tag_id

    tag_id = canonical_tag_id(conn, "test", name, kind)
    conn.execute(
        "INSERT OR REPLACE INTO work_tags(work_id, tag_id, source, weight, votes)"
        " VALUES(?,?,'test',?,?)",
        (work_id, tag_id, weight, votes),
    )
    conn.commit()
    return tag_id


class FakeSource:
    name = "fake"

    def __init__(self, payloads):
        self.payloads = payloads
        self.fetch_calls: list[str] = []

    async def search(self, query):
        return []

    async def fetch(self, key):
        self.fetch_calls.append(key)
        value = self.payloads[key]
        if isinstance(value, Exception):
            raise value
        return value
