import sqlite3
from pathlib import Path

CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS works(
    id INTEGER PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    type TEXT,
    year INTEGER,
    status TEXT,
    description TEXT,
    cover_url TEXT,
    banner_url TEXT,
    cover_color TEXT,
    is_adult INTEGER NOT NULL DEFAULT 0,
    franchise_id INTEGER,
    quality REAL,
    quality_votes INTEGER NOT NULL DEFAULT 0,
    is_stub INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS work_titles(
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    norm_title TEXT NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    UNIQUE(work_id, norm_title, kind)
);
CREATE INDEX IF NOT EXISTS idx_titles_norm ON work_titles(norm_title);
CREATE TABLE IF NOT EXISTS work_sources(
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    url TEXT,
    raw_score REAL,
    raw_votes INTEGER,
    last_fetched_at TEXT,
    UNIQUE(source, source_key)
);
CREATE INDEX IF NOT EXISTS idx_ws_work ON work_sources(work_id);
CREATE TABLE IF NOT EXISTS work_links(
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    site TEXT NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    UNIQUE(work_id, site, url)
);
CREATE TABLE IF NOT EXISTS tags(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    category TEXT,
    description TEXT,
    is_adult INTEGER NOT NULL DEFAULT 0,
    norm_name TEXT
);
CREATE TABLE IF NOT EXISTS tag_aliases(
    source TEXT NOT NULL,
    source_tag_name TEXT NOT NULL,
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    UNIQUE(source, source_tag_name)
);
CREATE TABLE IF NOT EXISTS work_tags(
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    source TEXT NOT NULL,
    weight REAL NOT NULL,
    votes INTEGER,
    UNIQUE(work_id, tag_id, source)
);
CREATE TABLE IF NOT EXISTS similarities(
    from_work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    to_work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    votes INTEGER NOT NULL,
    UNIQUE(from_work_id, to_work_id, source)
);
CREATE INDEX IF NOT EXISTS idx_sim_from ON similarities(source, votes, from_work_id);
CREATE INDEX IF NOT EXISTS idx_sim_to ON similarities(source, votes, to_work_id);
CREATE TABLE IF NOT EXISTS work_relations(
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    related_work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    rel_type TEXT NOT NULL,
    UNIQUE(work_id, related_work_id, rel_type)
);
CREATE TABLE IF NOT EXISTS match_reviews(
    id INTEGER PRIMARY KEY,
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    candidate_work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS user_list(
    work_id INTEGER PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS discard_feedback(
    work_id INTEGER NOT NULL REFERENCES works(id) ON DELETE CASCADE,
    tag_id INTEGER REFERENCES tags(id),
    note TEXT
);
CREATE TABLE IF NOT EXISTS trope_weights(
    tag_id INTEGER PRIMARY KEY REFERENCES tags(id),
    mode TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0
);
CREATE TABLE IF NOT EXISTS seeds(
    work_id INTEGER PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
    affinity REAL NOT NULL DEFAULT 1.0
);
CREATE TABLE IF NOT EXISTS ratings(
    work_id INTEGER PRIMARY KEY REFERENCES works(id) ON DELETE CASCADE,
    overall REAL NOT NULL,
    art REAL,
    story REAL,
    rated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS base_list(
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    rank_kind TEXT NOT NULL,
    rank INTEGER NOT NULL,
    pulled_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY(source, source_key, rank_kind)
);
CREATE TABLE IF NOT EXISTS refresh_log(
    id INTEGER PRIMARY KEY,
    action TEXT NOT NULL,                 -- pulled | acquired | refreshed | expanded | error
    work_id INTEGER REFERENCES works(id) ON DELETE SET NULL,
    label TEXT,                           -- title (self-contained; survives merges) or error text
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS work_merges(
    id INTEGER PRIMARY KEY,
    kept_source TEXT NOT NULL,
    kept_source_key TEXT NOT NULL,
    merged_source TEXT NOT NULL,
    merged_source_key TEXT NOT NULL,
    decided_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(merged_source, merged_source_key)
);
CREATE TABLE IF NOT EXISTS crawl_jobs(
    id INTEGER PRIMARY KEY,
    seed_work_id INTEGER NOT NULL REFERENCES works(id),
    max_depth INTEGER NOT NULL,
    budget INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    fetched INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS crawl_queue(
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES crawl_jobs(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    depth INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    UNIQUE(job_id, source, source_key)
);
CREATE TABLE IF NOT EXISTS settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

ARCHIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_raw(
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    payload_zlib BLOB NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "quality_gate": "7.0",
    "w_similarity": "0.55",
    "w_tropes": "0.30",
    "w_quality": "0.15",
    "ttl_days": "14",
    "show_adult": "0",
    "exclude_seed_franchise": "1",
    "shrink_prior": "6.5",
    "shrink_k": "200",
    "require_floor": "0.3",
    "discard_affinity": "-0.5",
    "discard_tag_weight": "-0.5",
    "background_refresh": "1",
}


def connect(path: Path | str) -> sqlite3.Connection:
    # check_same_thread=False: the web layer shares one connection between the
    # event loop and TestClient's portal thread; we are strictly single-process.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Columns added after v1; existing DBs get them via ALTER (CREATE TABLE IF NOT
# EXISTS never alters). Constant defaults only — SQLite restriction.
MIGRATIONS: dict[str, dict[str, str]] = {
    "tags": {
        "description": "TEXT",
        "is_adult": "INTEGER NOT NULL DEFAULT 0",
        "norm_name": "TEXT",
    },
    "works": {"banner_url": "TEXT", "cover_color": "TEXT"},
}


def _migrate(conn: sqlite3.Connection) -> None:
    from app.catalog.tags import fold_tag_name  # local import: tags.py is db-free

    for table, columns in MIGRATIONS.items():
        present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for column, decl in columns.items():
            if column not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    for r in conn.execute("SELECT id, name FROM tags WHERE norm_name IS NULL").fetchall():
        conn.execute("UPDATE tags SET norm_name=? WHERE id=?", (fold_tag_name(r["name"]), r["id"]))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_norm ON tags(norm_name)")


def init_catalog(conn: sqlite3.Connection) -> None:
    conn.executescript(CATALOG_SCHEMA)
    _migrate(conn)
    # executescript above issued an implicit COMMIT; these INSERTs run in a fresh transaction
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (key, value))
    conn.commit()


def init_archive(conn: sqlite3.Connection) -> None:
    conn.executescript(ARCHIVE_SCHEMA)
    conn.commit()


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def get_float(conn: sqlite3.Connection, key: str) -> float:
    value = get_setting(conn, key)
    if value is None:
        raise KeyError(f"settings key {key!r} not found")
    return float(value)


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
