from app.catalog.resolve import resolve
from tests.factory import link_source, make_payload, make_work


def test_no_match_means_create(catalog):
    r = resolve(catalog, make_payload(title="Brand New Title"))
    assert r.work_id is None
    assert r.candidates == ()


def test_exact_source_key_attaches(catalog):
    w = make_work(catalog, "Alpha")
    link_source(catalog, w, "anilist", "42")
    r = resolve(catalog, make_payload(source="anilist", source_key="42", title="Renamed"))
    assert r.work_id == w


def test_name_year_type_automerge_across_sources(catalog):
    w = make_work(catalog, "Solo Leveling", type="manhwa", year=2018)
    r = resolve(catalog, make_payload(source="mangaupdates", source_key="99",
                                      title="Solo Leveling", year=2018, type="manhwa"))
    assert r.work_id == w


def test_year_within_one_still_matches(catalog):
    w = make_work(catalog, "Alpha", year=2020)
    r = resolve(catalog, make_payload(source="mangaupdates", source_key="9", year=2021))
    assert r.work_id == w


def test_same_name_different_year_is_near_miss_review(catalog):
    make_work(catalog, "Alpha", year=2010)
    r = resolve(catalog, make_payload(source="mangaupdates", source_key="9", year=2020))
    assert r.work_id is None
    assert len(r.candidates) == 1  # surfaced for manual review


def test_two_candidates_is_review(catalog):
    make_work(catalog, "Alpha", year=2020)
    make_work(catalog, "Alpha", year=2020)
    r = resolve(catalog, make_payload(source="mangaupdates", source_key="9", year=2020))
    assert r.work_id is None
    assert len(r.candidates) == 2


def test_unknown_year_and_type_match_stub(catalog):
    w = make_work(catalog, "Alpha", year=None, type=None, is_stub=1)
    r = resolve(catalog, make_payload(source="mangaupdates", source_key="9"))
    assert r.work_id == w


def test_cjk_only_titles_do_not_match_everything(catalog):
    # CJK-only titles normalize to "" — they must not become match keys
    w = make_work(catalog, "Alpha")
    catalog.execute(
        "INSERT INTO work_titles(work_id, title, norm_title, kind, source)"
        " VALUES(?, '나 혼자만 레벨업', '', 'native', 'test')", (w,))
    catalog.commit()
    r = resolve(catalog, make_payload(source="mangaupdates", source_key="9", title="Beta",
                                      titles={"english": ("Beta",), "native": ("일어나라",)}))
    assert r.work_id is None
    assert r.candidates == ()


def test_merge_replay_wins_over_everything(catalog):
    kept = make_work(catalog, "Alpha")
    link_source(catalog, kept, "anilist", "1")
    catalog.execute(
        "INSERT INTO work_merges(kept_source, kept_source_key, merged_source, merged_source_key)"
        " VALUES('anilist','1','mangaupdates','9')"
    )
    r = resolve(catalog, make_payload(source="mangaupdates", source_key="9",
                                      title="Totally Different"))
    assert r.work_id == kept
