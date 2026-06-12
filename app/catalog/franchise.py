import sqlite3


def _franchise_of(conn: sqlite3.Connection, work_id: int) -> int:
    row = conn.execute("SELECT franchise_id FROM works WHERE id=?", (work_id,)).fetchone()
    return row["franchise_id"] or work_id


def union_franchise(conn: sqlite3.Connection, a: int, b: int) -> None:
    fa, fb = _franchise_of(conn, a), _franchise_of(conn, b)
    if fa == fb:
        return
    root, other = min(fa, fb), max(fa, fb)
    conn.execute("UPDATE works SET franchise_id=? WHERE franchise_id=?", (root, other))
