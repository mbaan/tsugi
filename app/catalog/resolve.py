import re
import sqlite3
import unicodedata
from dataclasses import dataclass

from app.sources.dto import WorkPayload


def normalize_title(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()


@dataclass(frozen=True)
class Resolution:
    work_id: int | None  # set => attach to this work
    candidates: tuple[int, ...] = ()  # non-empty with work_id None => needs review


def _all_names(payload: WorkPayload) -> set[str]:
    # discard "": CJK-only titles have no ASCII fingerprint and must not match
    names = {normalize_title(t) for group in payload.titles.values() for t in group if t}
    names.discard("")
    return names


def _compatible(a, b) -> bool:
    return a is None or b is None or a == b


def _year_ok(a, b) -> bool:
    return a is None or b is None or abs(a - b) <= 1


def resolve(conn: sqlite3.Connection, payload: WorkPayload) -> Resolution:
    row = conn.execute(
        "SELECT work_id FROM work_sources WHERE source=? AND source_key=?",
        (payload.source, payload.source_key),
    ).fetchone()
    if row:
        return Resolution(work_id=row["work_id"])

    # Merge-replay: if the kept ref isn't in work_sources yet (rebuild processed the
    # merged payload before the kept one), we deliberately fall through to name
    # matching — the kept payload attaches by name later, converging the same way.
    row = conn.execute(
        "SELECT kept_source, kept_source_key FROM work_merges"
        " WHERE merged_source=? AND merged_source_key=?",
        (payload.source, payload.source_key),
    ).fetchone()
    if row:
        kept = conn.execute(
            "SELECT work_id FROM work_sources WHERE source=? AND source_key=?",
            (row["kept_source"], row["kept_source_key"]),
        ).fetchone()
        if kept:
            return Resolution(work_id=kept["work_id"])

    names = _all_names(payload)
    if not names:
        return Resolution(work_id=None)
    placeholders = ",".join("?" * len(names))
    rows = conn.execute(
        f"SELECT DISTINCT w.id, w.type, w.year FROM works w"
        f" JOIN work_titles wt ON wt.work_id = w.id"
        f" WHERE wt.norm_title IN ({placeholders})",
        tuple(names),
    ).fetchall()

    strong = [r["id"] for r in rows
              if _compatible(r["type"], payload.type) and _year_ok(r["year"], payload.year)]
    near = [r["id"] for r in rows if r["id"] not in strong]

    if len(strong) == 1 and not near:
        return Resolution(work_id=strong[0])
    if not strong and not near:
        return Resolution(work_id=None)
    return Resolution(work_id=None, candidates=tuple(strong + near))


def merge_works(conn: sqlite3.Connection, kept_id: int, dup_id: int) -> None:
    """Fold dup into kept; record the decision by stable source refs for rebuild replay."""
    if kept_id == dup_id:
        return
    kept_ref = conn.execute(
        "SELECT source, source_key FROM work_sources WHERE work_id=? ORDER BY rowid LIMIT 1",
        (kept_id,),
    ).fetchone()
    for row in conn.execute(
        "SELECT source, source_key FROM work_sources WHERE work_id=?", (dup_id,)
    ).fetchall():
        if kept_ref:
            conn.execute(
                "INSERT OR IGNORE INTO work_merges(kept_source, kept_source_key,"
                " merged_source, merged_source_key) VALUES(?,?,?,?)",
                (kept_ref["source"], kept_ref["source_key"], row["source"], row["source_key"]),
            )
    for sql in (
        "UPDATE OR IGNORE work_sources SET work_id=? WHERE work_id=?",
        "UPDATE OR IGNORE work_titles SET work_id=? WHERE work_id=?",
        "UPDATE OR IGNORE work_tags SET work_id=? WHERE work_id=?",
        "UPDATE OR IGNORE work_links SET work_id=? WHERE work_id=?",
        "UPDATE OR IGNORE similarities SET from_work_id=? WHERE from_work_id=?",
        "UPDATE OR IGNORE similarities SET to_work_id=? WHERE to_work_id=?",
        "UPDATE OR IGNORE work_relations SET work_id=? WHERE work_id=?",
        "UPDATE OR IGNORE work_relations SET related_work_id=? WHERE related_work_id=?",
        "UPDATE OR IGNORE user_list SET work_id=? WHERE work_id=?",
        "UPDATE OR IGNORE seeds SET work_id=? WHERE work_id=?",
        "UPDATE OR IGNORE discard_feedback SET work_id=? WHERE work_id=?",
    ):
        conn.execute(sql, (kept_id, dup_id))
    conn.execute("DELETE FROM works WHERE id=?", (dup_id,))
    # Re-root any franchise members that pointed at the deleted id (franchise_id has
    # no FK constraint — without this they would dangle and split the group).
    conn.execute("UPDATE works SET franchise_id=? WHERE franchise_id=?", (kept_id, dup_id))
    conn.commit()
