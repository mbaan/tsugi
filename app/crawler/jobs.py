import asyncio
import sqlite3
from collections.abc import Mapping

from app import db
from app.catalog.normalize import upsert_payload
from app.crawler.frontier import expansion_targets
from app.sources.base import Source


def create_job(conn: sqlite3.Connection, seed_work_id: int, max_depth: int,
               budget: int = 300) -> int:
    job_id = conn.execute(
        "INSERT INTO crawl_jobs(seed_work_id, max_depth, budget) VALUES(?,?,?)",
        (seed_work_id, max_depth, budget),
    ).lastrowid
    for row in conn.execute(
        "SELECT source, source_key FROM work_sources WHERE work_id=?", (seed_work_id,)
    ).fetchall():
        conn.execute(
            "INSERT OR IGNORE INTO crawl_queue(job_id, source, source_key, depth)"
            " VALUES(?,?,?,0)",
            (job_id, row["source"], row["source_key"]),
        )
    conn.commit()
    return job_id


def _fresh_work_id(conn, source: str, source_key: str, ttl_days: float) -> int | None:
    row = conn.execute(
        "SELECT w.id FROM works w JOIN work_sources ws ON ws.work_id=w.id"
        " WHERE ws.source=? AND ws.source_key=? AND w.is_stub=0"
        " AND ws.last_fetched_at >= datetime('now', ?)",
        (source, source_key, f"-{int(ttl_days)} days"),
    ).fetchone()
    return row["id"] if row else None


async def run_job(conn: sqlite3.Connection, sources: Mapping[str, Source], job_id: int) -> None:
    """Drain the job's pending queue (BFS by depth).

    Interrupted jobs (status 'running') are resumed by the app lifespan; re-invoking
    on a 'truncated' job continues it — budget is compared against total fetched.
    """
    job = conn.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
    gate = db.get_float(conn, "quality_gate")
    ttl = db.get_float(conn, "ttl_days")
    status = "done"
    while True:
        await asyncio.sleep(0)  # yield: all-fresh stretches would otherwise starve the event loop
        item = conn.execute(
            "SELECT * FROM crawl_queue WHERE job_id=? AND state='pending'"
            " ORDER BY depth, id LIMIT 1",
            (job_id,),
        ).fetchone()
        if item is None:
            break

        work_id = _fresh_work_id(conn, item["source"], item["source_key"], ttl)
        if work_id is None:
            fetched = conn.execute(
                "SELECT fetched FROM crawl_jobs WHERE id=?", (job_id,)
            ).fetchone()["fetched"]
            if fetched >= job["budget"]:
                status = "truncated"
                break
            source = sources.get(item["source"])
            if source is None:
                conn.execute("UPDATE crawl_queue SET state='skipped' WHERE id=?", (item["id"],))
                conn.commit()
                continue
            try:
                payload = await source.fetch(item["source_key"])
            except Exception:
                conn.execute("UPDATE crawl_queue SET state='error' WHERE id=?", (item["id"],))
                conn.execute("UPDATE crawl_jobs SET errors=errors+1 WHERE id=?", (job_id,))
                conn.commit()
                continue
            work_id = upsert_payload(conn, payload)
            conn.execute("UPDATE crawl_jobs SET fetched=fetched+1 WHERE id=?", (job_id,))

        conn.execute("UPDATE crawl_queue SET state='done' WHERE id=?", (item["id"],))

        quality = conn.execute(
            "SELECT quality FROM works WHERE id=?", (work_id,)
        ).fetchone()["quality"]
        if item["depth"] < job["max_depth"] and quality is not None and quality >= gate:
            for t_source, t_key in expansion_targets(conn, work_id):
                conn.execute(
                    "INSERT OR IGNORE INTO crawl_queue(job_id, source, source_key, depth)"
                    " VALUES(?,?,?,?)",
                    (job_id, t_source, t_key, item["depth"] + 1),
                )
        conn.commit()

    conn.execute(
        "UPDATE crawl_jobs SET status=?, finished_at=datetime('now') WHERE id=?",
        (status, job_id),
    )
    conn.commit()
