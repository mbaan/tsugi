import sqlite3

MIN_VOTES = {"anilist": 5}  # AniList edges below 5 votes are noise; MU weights pass as-is
TOP_K = 10


def expansion_targets(conn: sqlite3.Connection, work_id: int) -> list[tuple[str, str]]:
    """Top-K similarity targets per source as (source, source_key); cross-franchise only."""
    me = conn.execute("SELECT franchise_id FROM works WHERE id=?", (work_id,)).fetchone()
    if me is None:
        return []
    rows = conn.execute(
        "SELECT s.source, s.votes, ws.source AS t_source, ws.source_key AS t_key,"
        " w.franchise_id AS t_franchise"
        " FROM similarities s"
        " JOIN works w ON w.id = s.to_work_id"
        " JOIN work_sources ws ON ws.work_id = w.id AND ws.source = s.source"
        " WHERE s.from_work_id=? ORDER BY s.source, s.votes DESC",
        (work_id,),
    ).fetchall()
    out: list[tuple[str, str]] = []
    counts: dict[str, int] = {}
    for r in rows:
        if r["t_franchise"] == me["franchise_id"]:
            continue
        if r["votes"] < MIN_VOTES.get(r["source"], 0):
            continue
        if counts.get(r["source"], 0) >= TOP_K:
            continue
        counts[r["source"]] = counts.get(r["source"], 0) + 1
        out.append((r["t_source"], r["t_key"]))
    return out
