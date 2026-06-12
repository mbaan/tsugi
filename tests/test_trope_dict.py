from app.catalog.tags import canonical_tag_id
from app.catalog.trope_dict import dictionary_present, upsert_dictionary
from tests.conftest import load_fixture


def test_upsert_creates_tags_with_metadata(catalog):
    n = upsert_dictionary(catalog, load_fixture("anilist_tag_collection.json"))
    assert n == 7  # 3 genres + 4 tags
    row = catalog.execute("SELECT * FROM tags WHERE name='Dungeon'").fetchone()
    assert row["kind"] == "trope"
    assert row["category"] == "Setting-Scene"
    assert row["description"].startswith("Partly")
    assert row["is_adult"] == 0
    assert row["norm_name"] == "dungeon"
    adult = catalog.execute("SELECT is_adult FROM tags WHERE name='Bondage'").fetchone()
    assert adult["is_adult"] == 1
    genre = catalog.execute("SELECT kind, is_adult FROM tags WHERE name='Hentai'").fetchone()
    assert genre["kind"] == "genre"
    assert genre["is_adult"] == 1
    alias = catalog.execute(
        "SELECT tag_id FROM tag_aliases WHERE source='anilist' AND source_tag_name='Dungeon'"
    ).fetchone()
    assert alias is not None


def test_upsert_is_idempotent(catalog):
    payload = load_fixture("anilist_tag_collection.json")
    upsert_dictionary(catalog, payload)
    upsert_dictionary(catalog, payload)
    assert catalog.execute("SELECT COUNT(*) c FROM tags").fetchone()["c"] == 7


def test_upsert_promotes_existing_crawled_tag(catalog):
    crawled = canonical_tag_id(catalog, "anilist", "Dungeon", "tag")
    upsert_dictionary(catalog, load_fixture("anilist_tag_collection.json"))
    row = catalog.execute("SELECT * FROM tags WHERE id=?", (crawled,)).fetchone()
    assert row["kind"] == "trope"
    assert row["description"] is not None
    assert catalog.execute("SELECT COUNT(*) c FROM tags WHERE name='Dungeon'").fetchone()["c"] == 1


def test_mu_alias_attaches_to_dictionary_tag(catalog):
    upsert_dictionary(catalog, load_fixture("anilist_tag_collection.json"))
    dungeon = catalog.execute("SELECT id FROM tags WHERE name='Dungeon'").fetchone()["id"]
    assert canonical_tag_id(catalog, "mangaupdates", "Dungeon/s", "trope") == dungeon


def test_dictionary_present(catalog):
    assert not dictionary_present(catalog)
    upsert_dictionary(catalog, load_fixture("anilist_tag_collection.json"))
    assert dictionary_present(catalog)
