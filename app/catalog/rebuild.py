"""Replay the raw archive into a fresh canonical layer. User layer is preserved
by snapshotting it keyed on stable (source, source_key) refs and tag names."""

import sqlite3

from app.catalog.normalize import upsert_payload
from app.sources.anilist import parse_media
from app.sources.archive import RawArchive
from app.sources.mangaupdates import parse_series

CANONICAL_TABLES = ("similarities", "work_relations", "work_tags", "work_titles",
                    "work_links", "match_reviews", "work_sources", "tag_aliases", "tags",
                    "crawl_queue", "crawl_jobs", "works")


def _snapshot_user_layer(conn) -> dict:
    def ref(work_id):
        row = conn.execute(
            "SELECT source, source_key FROM work_sources WHERE work_id=? ORDER BY rowid LIMIT 1",
            (work_id,),
        ).fetchone()
        return (row["source"], row["source_key"]) if row else None

    return {
        "user_list": [(ref(r["work_id"]), r["status"], r["note"])
                      for r in conn.execute("SELECT * FROM user_list")],
        "seeds": [(ref(r["work_id"]), r["affinity"])
                  for r in conn.execute("SELECT * FROM seeds")],
        "trope_weights": [(r["name"], r["mode"], r["weight"]) for r in conn.execute(
            "SELECT t.name, tw.mode, tw.weight FROM trope_weights tw JOIN tags t ON t.id=tw.tag_id")],
        "discard_feedback": [(ref(r["work_id"]), r["name"], r["note"]) for r in conn.execute(
            "SELECT df.work_id, t.name, df.note FROM discard_feedback df"
            " LEFT JOIN tags t ON t.id=df.tag_id")],
    }


def _restore_user_layer(conn, snap: dict) -> None:
    def work_by_ref(r):
        if r is None:
            return None
        row = conn.execute(
            "SELECT work_id FROM work_sources WHERE source=? AND source_key=?", r
        ).fetchone()
        return row["work_id"] if row else None

    def tag_by_name(name):
        if name is None:
            return None
        row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
        if row:
            return row["id"]
        return conn.execute(
            "INSERT INTO tags(name, kind) VALUES(?, 'trope')", (name,)).lastrowid

    conn.execute("DELETE FROM user_list")
    conn.execute("DELETE FROM seeds")
    conn.execute("DELETE FROM trope_weights")
    conn.execute("DELETE FROM discard_feedback")
    for r, status, note in snap["user_list"]:
        if (wid := work_by_ref(r)) is not None:
            conn.execute("INSERT OR IGNORE INTO user_list(work_id, status, note) VALUES(?,?,?)",
                         (wid, status, note))
    for r, affinity in snap["seeds"]:
        if (wid := work_by_ref(r)) is not None:
            conn.execute("INSERT OR IGNORE INTO seeds(work_id, affinity) VALUES(?,?)",
                         (wid, affinity))
    for name, mode, weight in snap["trope_weights"]:
        conn.execute("INSERT OR IGNORE INTO trope_weights(tag_id, mode, weight) VALUES(?,?,?)",
                     (tag_by_name(name), mode, weight))
    for r, tag_name, note in snap["discard_feedback"]:
        if (wid := work_by_ref(r)) is not None:
            conn.execute("INSERT INTO discard_feedback(work_id, tag_id, note) VALUES(?,?,?)",
                         (wid, tag_by_name(tag_name), note))


def rebuild(conn: sqlite3.Connection, archive: RawArchive) -> int:
    snap = _snapshot_user_layer(conn)
    # trope_weights/discard_feedback reference tags WITHOUT cascade; with
    # foreign_keys=ON, DELETE FROM tags would violate FK while they exist.
    # The snapshot already captured them by tag name — wipe first, restore later.
    conn.execute("DELETE FROM trope_weights")
    conn.execute("DELETE FROM discard_feedback")
    for table in CANONICAL_TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()

    # dictionary first: MU aliases must attach to stable dictionary tags
    from app.catalog.trope_dict import ARCHIVE_KIND, upsert_dictionary

    dictionary = archive.latest("anilist", ARCHIVE_KIND)
    if dictionary:
        upsert_dictionary(conn, dictionary)

    count = 0
    for source, _key, kind, _fetched_at, payload in archive.iter_all():
        if source == "anilist" and kind == "media":
            wp = parse_media(payload["data"]["Media"])
        elif source == "mangaupdates" and kind == "series":
            wp = parse_series(payload)
        else:
            continue
        upsert_payload(conn, wp)
        count += 1

    _restore_user_layer(conn, snap)
    conn.commit()
    return count


if __name__ == "__main__":
    from app import db
    from app.config import load_config

    cfg = load_config()
    catalog = db.connect(cfg.catalog_path)
    db.init_catalog(catalog)
    archive_conn = db.connect(cfg.archive_path)
    db.init_archive(archive_conn)
    n = rebuild(catalog, RawArchive(archive_conn))
    print(f"rebuilt catalog from {n} archived payloads")
