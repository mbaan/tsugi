from app.catalog.normalize import upsert_payload
from app.sources.anilist import parse_media
from app.sources.mangaupdates import parse_series
from tests.conftest import load_fixture
from tests.factory import make_payload


def al_payload():
    return parse_media(load_fixture("anilist_media.json")["data"]["Media"])


def test_upsert_anilist_fixture_creates_full_work(catalog):
    work_id = upsert_payload(catalog, al_payload())
    w = catalog.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
    assert w["is_stub"] == 0
    assert w["type"] == "manhwa"
    assert w["canonical_title"] == "Solo Leveling"  # english preferred over romaji
    assert 7.5 < w["quality"] < 9.0
    n_tags = catalog.execute("SELECT COUNT(*) c FROM work_tags WHERE work_id=?", (work_id,)).fetchone()["c"]
    assert n_tags > 10
    n_sim = catalog.execute("SELECT COUNT(*) c FROM similarities WHERE from_work_id=?", (work_id,)).fetchone()["c"]
    assert n_sim > 3
    n_links = catalog.execute("SELECT COUNT(*) c FROM work_links WHERE work_id=?", (work_id,)).fetchone()["c"]
    assert n_links > 3  # KakaoPage, Tapas, ...


def test_similar_targets_become_stubs(catalog):
    upsert_payload(catalog, al_payload())
    stubs = catalog.execute("SELECT COUNT(*) c FROM works WHERE is_stub=1").fetchone()["c"]
    assert stubs > 3


def test_relations_union_franchise(catalog):
    work_id = upsert_payload(catalog, al_payload())
    w = catalog.execute("SELECT franchise_id FROM works WHERE id=?", (work_id,)).fetchone()
    siblings = catalog.execute(
        "SELECT COUNT(*) c FROM works WHERE franchise_id=? AND id!=?",
        (w["franchise_id"], work_id),
    ).fetchone()["c"]
    assert siblings >= 1  # sequel stub joined the franchise


def test_mu_payload_merges_into_same_work(catalog):
    a = upsert_payload(catalog, al_payload())
    b = upsert_payload(catalog, parse_series(load_fixture("mu_series.json")))
    assert a == b
    n_sources = catalog.execute("SELECT COUNT(*) c FROM work_sources WHERE work_id=?", (a,)).fetchone()["c"]
    assert n_sources == 2
    w = catalog.execute("SELECT type FROM works WHERE id=?", (a,)).fetchone()
    assert w["type"] == "manhwa"  # MU agrees/wins


def test_low_vote_anilist_score_shrinks_toward_prior(catalog):
    work_id = upsert_payload(catalog, make_payload(source="anilist", source_key="7",
                                                   title="Tiny", score=9.5, score_votes=10))
    q = catalog.execute("SELECT quality FROM works WHERE id=?", (work_id,)).fetchone()["quality"]
    assert q < 7.0  # (9.5*10 + 6.5*200) / 210 ~= 6.64


def test_mu_bayesian_used_unshrunk(catalog):
    work_id = upsert_payload(catalog, make_payload(source="mangaupdates", source_key="7",
                                                   title="Tiny", score=8.0, score_votes=10))
    q = catalog.execute("SELECT quality FROM works WHERE id=?", (work_id,)).fetchone()["quality"]
    assert q == 8.0


def test_cjk_only_titles_not_stored_as_match_keys(catalog):
    work_id = upsert_payload(catalog, make_payload(
        titles={"english": ("Alpha",), "native": ("나 혼자만 레벨업",)}))
    rows = catalog.execute("SELECT norm_title FROM work_titles WHERE work_id=?", (work_id,)).fetchall()
    assert rows  # english title stored
    assert all(r["norm_title"] for r in rows)  # no empty match keys


def test_upsert_same_payload_twice_is_idempotent(catalog):
    a = upsert_payload(catalog, al_payload())
    counts_sql = ("SELECT (SELECT COUNT(*) FROM works), (SELECT COUNT(*) FROM work_titles),"
                  " (SELECT COUNT(*) FROM work_tags), (SELECT COUNT(*) FROM similarities),"
                  " (SELECT COUNT(*) FROM match_reviews)")
    before = tuple(catalog.execute(counts_sql).fetchone())
    b = upsert_payload(catalog, al_payload())
    assert a == b
    assert tuple(catalog.execute(counts_sql).fetchone()) == before


def test_upsert_persists_banner_and_color(catalog):
    upsert_payload(catalog, make_payload(
        banner_url="https://img.example/b.jpg", cover_color="#abc123"))
    row = catalog.execute("SELECT banner_url, cover_color FROM works WHERE is_stub=0").fetchone()
    assert row["banner_url"] == "https://img.example/b.jpg"
    assert row["cover_color"] == "#abc123"


def test_upsert_persists_release_month(catalog):
    from app.catalog.normalize import upsert_payload
    from tests.factory import make_payload
    wid = upsert_payload(catalog, make_payload(source_key="42", release_month=7))
    row = catalog.execute("SELECT release_month FROM works WHERE id=?", (wid,)).fetchone()
    assert row["release_month"] == 7
