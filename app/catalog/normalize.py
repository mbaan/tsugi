import math
import sqlite3

from app import db
from app.catalog.franchise import union_franchise
from app.catalog.resolve import normalize_title, resolve
from app.catalog.tags import canonical_tag_id
from app.sources.dto import WorkPayload


def _choose_title(payload: WorkPayload) -> str:
    for kind in ("english", "romaji", "native", "synonym"):
        names = payload.titles.get(kind)
        if names:
            return names[0]
    return f"{payload.source}:{payload.source_key}"


def _create_work(conn, *, title: str, type: str | None = None, year: int | None = None,
                 is_stub: int = 1) -> int:
    work_id = conn.execute(
        "INSERT INTO works(canonical_title, type, year, is_stub) VALUES(?,?,?,?)",
        (title, type, year, is_stub),
    ).lastrowid
    conn.execute("UPDATE works SET franchise_id=? WHERE id=?", (work_id, work_id))
    return work_id


def ensure_stub(conn: sqlite3.Connection, source: str, source_key: str, title: str) -> int:
    row = conn.execute(
        "SELECT work_id FROM work_sources WHERE source=? AND source_key=?", (source, source_key)
    ).fetchone()
    if row:
        return row["work_id"]
    work_id = _create_work(conn, title=title)
    norm = normalize_title(title)
    if norm:
        conn.execute(
            "INSERT OR IGNORE INTO work_titles(work_id, title, norm_title, kind, source)"
            " VALUES(?,?,?,?,?)",
            (work_id, title, norm, "english", source),
        )
    conn.execute(
        "INSERT INTO work_sources(work_id, source, source_key) VALUES(?,?,?)",
        (work_id, source, source_key),
    )
    return work_id


def recompute_quality(conn: sqlite3.Connection, work_id: int) -> None:
    prior = db.get_float(conn, "shrink_prior")
    k = db.get_float(conn, "shrink_k")
    rows = conn.execute(
        "SELECT source, raw_score, raw_votes FROM work_sources"
        " WHERE work_id=? AND raw_score IS NOT NULL",
        (work_id,),
    ).fetchall()
    num = den = 0.0
    total_votes = 0
    for r in rows:
        votes = r["raw_votes"] or 0
        q = r["raw_score"]
        if r["source"] == "anilist":  # MU bayesian_rating is already shrunk
            q = (q * votes + prior * k) / (votes + k)
        w = math.log1p(votes) or 1.0
        num += q * w
        den += w
        total_votes += votes
    if den:
        conn.execute(
            "UPDATE works SET quality=?, quality_votes=? WHERE id=?",
            (num / den, total_votes, work_id),
        )


def upsert_payload(conn: sqlite3.Connection, payload: WorkPayload) -> int:
    res = resolve(conn, payload)
    if res.work_id is not None:
        work_id = res.work_id
    else:
        work_id = _create_work(
            conn, title=_choose_title(payload), type=payload.type, year=payload.year
        )
        for cand in res.candidates:
            conn.execute(
                "INSERT INTO match_reviews(work_id, candidate_work_id, reason) VALUES(?,?,?)",
                (work_id, cand,
                 f"name match with conflicting metadata ({payload.source}:{payload.source_key})"),
            )

    for kind, names in payload.titles.items():
        for t in names:
            norm = normalize_title(t)
            if not norm:  # CJK-only titles have no ASCII fingerprint — not match keys
                continue
            conn.execute(
                "INSERT OR IGNORE INTO work_titles(work_id, title, norm_title, kind, source)"
                " VALUES(?,?,?,?,?)",
                (work_id, t, norm, kind, payload.source),
            )

    conn.execute(
        "INSERT INTO work_sources(work_id, source, source_key, url, raw_score, raw_votes,"
        " last_fetched_at) VALUES(?,?,?,?,?,?,datetime('now'))"
        " ON CONFLICT(source, source_key) DO UPDATE SET url=excluded.url,"
        " raw_score=excluded.raw_score, raw_votes=excluded.raw_votes,"
        " last_fetched_at=excluded.last_fetched_at",
        (work_id, payload.source, payload.source_key, payload.url,
         payload.score, payload.score_votes),
    )

    cur = conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
    canonical = cur["canonical_title"]
    if payload.titles.get("english"):
        canonical = payload.titles["english"][0]
    elif cur["is_stub"]:
        canonical = _choose_title(payload)
    type_ = cur["type"]
    if payload.type and (payload.source == "mangaupdates" or not type_):
        type_ = payload.type
    # year uses COALESCE(year, ?) — first-seen year wins (stable identity field);
    # status/description/cover use COALESCE(?, col) — fresh payload wins.
    conn.execute(
        "UPDATE works SET canonical_title=?, type=?, year=COALESCE(year, ?),"
        " status=COALESCE(?, status), description=COALESCE(?, description),"
        " cover_url=COALESCE(?, cover_url), banner_url=COALESCE(?, banner_url),"
        " cover_color=COALESCE(?, cover_color), is_adult=MAX(is_adult, ?), is_stub=0,"
        " updated_at=datetime('now') WHERE id=?",
        (canonical, type_, payload.year, payload.status, payload.description,
         payload.cover_url, payload.banner_url, payload.cover_color,
         int(payload.is_adult), work_id),
    )

    for tv in payload.tags:
        tag_id = canonical_tag_id(conn, payload.source, tv.name, tv.kind, tv.category)
        conn.execute(
            "INSERT OR REPLACE INTO work_tags(work_id, tag_id, source, weight, votes)"
            " VALUES(?,?,?,?,?)",
            (work_id, tag_id, payload.source, tv.weight, tv.votes),
        )

    for site, url in payload.links:
        conn.execute(
            "INSERT OR IGNORE INTO work_links(work_id, site, url, source) VALUES(?,?,?,?)",
            (work_id, site, url, payload.source),
        )

    for ref in payload.similar:
        target = ensure_stub(conn, ref.source, ref.source_key, ref.title)
        if target != work_id:
            conn.execute(
                "INSERT OR REPLACE INTO similarities(from_work_id, to_work_id, source, votes)"
                " VALUES(?,?,?,?)",
                (work_id, target, payload.source, ref.votes),
            )

    for ref in payload.relations:
        target = ensure_stub(conn, ref.source, ref.source_key, ref.title)
        if target != work_id:
            conn.execute(
                "INSERT OR IGNORE INTO work_relations(work_id, related_work_id, rel_type)"
                " VALUES(?,?,?)",
                (work_id, target, ref.rel_type),
            )
            union_franchise(conn, work_id, target)

    recompute_quality(conn, work_id)
    conn.commit()
    return work_id
