from app import db


def test_catalog_tables_exist(catalog):
    names = {r["name"] for r in catalog.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == {"works", "work_titles", "work_sources", "work_links", "tags", "tag_aliases",
                     "work_tags", "similarities", "work_relations", "match_reviews", "user_list",
                     "discard_feedback", "trope_weights", "seeds", "ratings", "work_merges", "crawl_jobs",
                     "crawl_queue", "settings", "base_list", "refresh_log"}


def test_refresh_log_keeps_row_when_work_deleted(catalog):
    wid = catalog.execute("INSERT INTO works(canonical_title) VALUES('L')").lastrowid
    catalog.execute("INSERT INTO refresh_log(action, work_id, label) VALUES('acquired',?,'L')", (wid,))
    catalog.commit()
    catalog.execute("DELETE FROM works WHERE id=?", (wid,))
    catalog.commit()
    row = catalog.execute("SELECT action, work_id, label, at FROM refresh_log").fetchone()
    assert row["action"] == "acquired" and row["work_id"] is None  # SET NULL, log survives
    assert row["label"] == "L" and row["at"]


def test_settings_defaults_and_override(catalog):
    assert db.get_float(catalog, "w_similarity") == 0.70
    assert db.get_float(catalog, "k_rate") == 6.0
    assert db.get_float(catalog, "min_votes") == 10
    assert db.get_float(catalog, "window_floor") == 0.5
    db.set_setting(catalog, "w_similarity", "0.4")
    assert db.get_float(catalog, "w_similarity") == 0.4


def test_archive_table_exists(archive_db):
    names = {r["name"] for r in archive_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "source_raw" in names


def test_new_columns_exist_on_fresh_db(catalog):
    tag_cols = {r["name"] for r in catalog.execute("PRAGMA table_info(tags)")}
    work_cols = {r["name"] for r in catalog.execute("PRAGMA table_info(works)")}
    assert {"description", "is_adult", "norm_name"} <= tag_cols
    assert {"banner_url", "cover_color"} <= work_cols


def test_migrate_upgrades_v1_database(tmp_path):
    conn = db.connect(tmp_path / "old.sqlite")
    # v1 table shapes (no new columns)
    conn.executescript(
        "CREATE TABLE works(id INTEGER PRIMARY KEY, canonical_title TEXT NOT NULL);"
        "CREATE TABLE tags(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,"
        " kind TEXT NOT NULL, category TEXT);"
    )
    conn.execute("INSERT INTO tags(name, kind) VALUES('Dungeon/s Exploring', 'trope')")
    conn.commit()
    db.init_catalog(conn)  # must ALTER instead of failing, and backfill norm_name
    row = conn.execute("SELECT norm_name, is_adult FROM tags").fetchone()
    assert row["norm_name"] == "dungeon exploring"
    assert row["is_adult"] == 0
    conn.close()


def test_ratings_table_cascades_with_work(catalog):
    wid = catalog.execute("INSERT INTO works(canonical_title) VALUES('R')").lastrowid
    catalog.execute("INSERT INTO ratings(work_id, overall, art) VALUES(?, 9, 7)", (wid,))
    catalog.commit()
    row = catalog.execute("SELECT overall, art, story FROM ratings WHERE work_id=?", (wid,)).fetchone()
    assert (row["overall"], row["art"], row["story"]) == (9, 7, None)
    catalog.execute("DELETE FROM works WHERE id=?", (wid,))
    catalog.commit()
    assert catalog.execute("SELECT COUNT(*) c FROM ratings").fetchone()["c"] == 0


def test_base_list_table_and_refresh_default(catalog):
    catalog.execute(
        "INSERT INTO base_list(source, source_key, rank_kind, rank) VALUES('anilist','1','score',1)")
    catalog.commit()
    row = catalog.execute("SELECT * FROM base_list").fetchone()
    assert row["rank"] == 1 and row["pulled_at"]
    from app import db
    assert db.get_setting(catalog, "background_refresh") == "1"
