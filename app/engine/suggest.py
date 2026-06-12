"""Suggest rated read items as one-click (anti-)seed candidates.

Seeds stay manual and mood-curated; the app only surfaces candidates.
Ranking signal: tag overlap with the positive-seed centroid.
"""

import sqlite3

from app.engine.score import rating_affinity


def _merged_tags(conn: sqlite3.Connection, work_id: int) -> dict[int, float]:
    return {r["tag_id"]: r["mw"] for r in conn.execute(
        "SELECT tag_id, AVG(weight) mw FROM work_tags WHERE work_id=? GROUP BY tag_id",
        (work_id,))}


def suggest_seeds(conn: sqlite3.Connection, limit_pos: int = 6, limit_neg: int = 3) -> dict:
    positive_seeds = []
    for r in conn.execute(
        "SELECT s.work_id, s.affinity, rt.overall FROM seeds s"
        " LEFT JOIN ratings rt ON rt.work_id=s.work_id"
    ):
        pull = rating_affinity(r["overall"]) if r["overall"] is not None else r["affinity"]
        if pull > 0:
            positive_seeds.append(r["work_id"])

    centroid: dict[int, float] = {}
    for wid in positive_seeds:
        for tag_id, mw in _merged_tags(conn, wid).items():
            centroid[tag_id] = centroid.get(tag_id, 0.0) + mw / len(positive_seeds)

    rated = conn.execute(
        "SELECT w.id, w.canonical_title, w.cover_color, r.overall FROM ratings r"
        " JOIN works w ON w.id=r.work_id"
        " WHERE r.work_id NOT IN (SELECT work_id FROM seeds)"
    ).fetchall()

    def overlap(work_id: int) -> float:
        return sum(mw * centroid.get(tag_id, 0.0)
                   for tag_id, mw in _merged_tags(conn, work_id).items())

    candidates = [{"id": r["id"], "canonical_title": r["canonical_title"],
                   "cover_color": r["cover_color"], "overall": r["overall"],
                   "overlap": overlap(r["id"])} for r in rated]
    seed_bucket = sorted((c for c in candidates if c["overall"] >= 8),
                         key=lambda c: (-c["overlap"], -c["overall"]))[:limit_pos]
    anti_bucket = sorted((c for c in candidates if c["overall"] <= 4),
                         key=lambda c: (c["overall"], -c["overlap"]))[:limit_neg]
    return {"seed": seed_bucket, "anti": anti_bucket}
