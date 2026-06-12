from app.catalog.resolve import normalize_title
from app.catalog.tags import canonical_tag_id, fold_tag_name


def test_normalize_title_strips_case_punctuation_accents():
    assert normalize_title("Solo Leveling!") == "solo leveling"
    assert normalize_title("  SOLO   LEVELING ") == "solo leveling"
    assert normalize_title("Éclair: The 2nd") == "eclair the 2nd"


def test_same_alias_returns_same_tag(catalog):
    a = canonical_tag_id(catalog, "anilist", "Dungeon", "tag")
    b = canonical_tag_id(catalog, "anilist", "Dungeon", "tag")
    assert a == b


def test_curated_alias_maps_sources_together(catalog):
    a = canonical_tag_id(catalog, "anilist", "Dungeon", "tag")
    b = canonical_tag_id(catalog, "mangaupdates", "Dungeon/s", "trope")
    assert a == b


def test_unmapped_tags_autocreate_distinct(catalog):
    a = canonical_tag_id(catalog, "anilist", "Necromancy", "tag")
    b = canonical_tag_id(catalog, "mangaupdates", "Level System", "trope")
    assert a != b


def test_curated_kind_is_arrival_order_independent(catalog):
    canonical_tag_id(catalog, "anilist", "Video Games", "tag")
    row = catalog.execute("SELECT kind FROM tags WHERE name='Game Elements'").fetchone()
    assert row["kind"] == "trope"


def test_fold_tag_name():
    assert fold_tag_name("Dungeon/s") == "dungeon"
    assert fold_tag_name("Dungeons") == "dungeon"
    assert fold_tag_name("Dungeon/s Exploring") == "dungeon exploring"
    assert fold_tag_name("Game Elements") == "game element"
    assert fold_tag_name("Class") == "class"  # 'ss' ending survives


def test_normalized_match_attaches_to_existing_tag(catalog):
    dungeon = catalog.execute(
        "INSERT INTO tags(name, kind, norm_name) VALUES('Dungeon', 'trope', 'dungeon')"
    ).lastrowid
    catalog.commit()
    assert canonical_tag_id(catalog, "mangaupdates", "Dungeons", "trope") == dungeon


def test_curated_map_beats_normalized_match(catalog):
    tag_id = canonical_tag_id(catalog, "mangaupdates", "Overpowered Male Lead", "trope")
    name = catalog.execute("SELECT name FROM tags WHERE id=?", (tag_id,)).fetchone()["name"]
    assert name == "Overpowered Main Character"


def test_unmatched_tag_autocreates_with_norm_name(catalog):
    tag_id = canonical_tag_id(catalog, "mangaupdates", "Puppets", "trope")
    row = catalog.execute("SELECT norm_name FROM tags WHERE id=?", (tag_id,)).fetchone()
    assert row["norm_name"] == "puppet"
