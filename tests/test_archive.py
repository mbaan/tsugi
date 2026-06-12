from app.sources.archive import RawArchive


def test_store_and_iter_roundtrip(archive_db):
    archive = RawArchive(archive_db)
    archive.store("anilist", "105398", "media", {"id": 105398, "title": "솔로 레벨링"})
    archive.store("mangaupdates", "15180124327", "series", {"title": "Solo Leveling"})
    rows = list(archive.iter_all())
    assert len(rows) == 2
    source, key, kind, fetched_at, payload = rows[0]
    assert (source, key, kind) == ("anilist", "105398", "media")
    assert payload["title"] == "솔로 레벨링"
    assert fetched_at  # timestamp recorded


def test_compression_applied(archive_db):
    archive = RawArchive(archive_db)
    archive.store("anilist", "1", "media", {"x": "y" * 10_000})
    blob = archive_db.execute("SELECT payload_zlib FROM source_raw").fetchone()[0]
    assert len(blob) < 1_000


def test_latest_returns_newest_payload_of_kind(archive_db):
    from app.sources.archive import RawArchive

    a = RawArchive(archive_db)
    assert a.latest("anilist", "tag_collection") is None
    a.store("anilist", "all", "tag_collection", {"v": 1})
    a.store("anilist", "all", "tag_collection", {"v": 2})
    assert a.latest("anilist", "tag_collection") == {"v": 2}
