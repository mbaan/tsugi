from app.catalog.rebuild import rebuild
from app.catalog.resolve import merge_works
from app.sources.archive import RawArchive
from tests.conftest import load_fixture
from tests.factory import link_source, make_work


def _ingest_fixtures(catalog, archive_db):
    """Archive raw fixtures, then replay them into the catalog (like a real fetch would)."""
    archive = RawArchive(archive_db)
    archive.store("anilist", "105398", "media", load_fixture("anilist_media.json"))
    archive.store("mangaupdates", "15180124327", "series", load_fixture("mu_series.json"))
    return archive


def test_merge_works_moves_rows_and_records_decision(catalog):
    kept = make_work(catalog, "Alpha")
    dup = make_work(catalog, "Alpha (dup)")
    link_source(catalog, kept, "anilist", "1")
    link_source(catalog, dup, "mangaupdates", "9")
    merge_works(catalog, kept, dup)
    assert catalog.execute("SELECT COUNT(*) c FROM works WHERE id=?", (dup,)).fetchone()["c"] == 0
    sources = {r["source"] for r in catalog.execute(
        "SELECT source FROM work_sources WHERE work_id=?", (kept,))}
    assert sources == {"anilist", "mangaupdates"}
    m = catalog.execute("SELECT * FROM work_merges").fetchone()
    assert (m["merged_source"], m["merged_source_key"]) == ("mangaupdates", "9")


def test_merge_works_reroots_franchise_of_dup(catalog):
    # dup is the franchise root of a sibling; after merge the sibling must point at kept
    kept = make_work(catalog, "Alpha")
    dup = make_work(catalog, "Alpha?")
    sibling = make_work(catalog, "Alpha Side Story")
    link_source(catalog, kept, "anilist", "1")
    link_source(catalog, dup, "mangaupdates", "9")
    catalog.execute("UPDATE works SET franchise_id=? WHERE id IN (?, ?)", (dup, dup, sibling))
    catalog.commit()
    merge_works(catalog, kept, dup)
    fr = catalog.execute("SELECT franchise_id FROM works WHERE id=?", (sibling,)).fetchone()
    assert fr["franchise_id"] == kept  # no dangling root pointing at deleted id


def test_rebuild_replays_archive_identically(catalog, archive_db):
    archive = _ingest_fixtures(catalog, archive_db)
    n = rebuild(catalog, archive)
    assert n == 2
    before = catalog.execute(
        "SELECT (SELECT COUNT(*) FROM works), (SELECT COUNT(*) FROM similarities),"
        " (SELECT COUNT(*) FROM work_tags)").fetchone()
    rebuild(catalog, archive)
    after = catalog.execute(
        "SELECT (SELECT COUNT(*) FROM works), (SELECT COUNT(*) FROM similarities),"
        " (SELECT COUNT(*) FROM work_tags)").fetchone()
    assert tuple(before) == tuple(after)


def test_rebuild_preserves_user_layer(catalog, archive_db):
    archive = _ingest_fixtures(catalog, archive_db)
    rebuild(catalog, archive)
    work_id = catalog.execute(
        "SELECT work_id FROM work_sources WHERE source='anilist' AND source_key='105398'"
    ).fetchone()["work_id"]
    catalog.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'read')", (work_id,))
    catalog.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (work_id,))
    catalog.commit()

    rebuild(catalog, archive)

    new_id = catalog.execute(
        "SELECT work_id FROM work_sources WHERE source='anilist' AND source_key='105398'"
    ).fetchone()["work_id"]
    assert catalog.execute(
        "SELECT status FROM user_list WHERE work_id=?", (new_id,)
    ).fetchone()["status"] == "read"
    assert catalog.execute("SELECT COUNT(*) c FROM seeds").fetchone()["c"] == 1


def test_rebuild_with_trope_weights_does_not_crash(catalog, archive_db):
    archive = _ingest_fixtures(catalog, archive_db)
    rebuild(catalog, archive)
    tag_id = catalog.execute("SELECT id FROM tags LIMIT 1").fetchone()["id"]
    catalog.execute(
        "INSERT INTO trope_weights(tag_id, mode, weight) VALUES(?, 'boost', 2.0)", (tag_id,))
    catalog.commit()
    rebuild(catalog, archive)  # must not raise FK violation
    assert catalog.execute("SELECT COUNT(*) c FROM trope_weights").fetchone()["c"] == 1


def test_rebuild_applies_dictionary_before_replay(catalog, archive_db):
    from app.catalog.rebuild import rebuild
    from app.sources.archive import RawArchive
    from tests.conftest import load_fixture

    archive = RawArchive(archive_db)
    archive.store("anilist", "all", "tag_collection",
                  load_fixture("anilist_tag_collection.json"))
    archive.store("mangaupdates", "77", "series", load_fixture("mu_series.json"))
    rebuild(catalog, archive)
    # the MU series' Dungeon-flavored category must alias to the dictionary tag,
    # not create a duplicate
    assert catalog.execute(
        "SELECT COUNT(*) c FROM tags WHERE norm_name='dungeon'"
    ).fetchone()["c"] <= 1
    dungeon = catalog.execute("SELECT description FROM tags WHERE name='Dungeon'").fetchone()
    assert dungeon is not None and dungeon["description"] is not None
