from app.sources.mangaupdates import parse_search, parse_series
from tests.conftest import load_fixture


def series_fixture() -> dict:
    return load_fixture("mu_series.json")


def test_parse_series_core_fields():
    p = parse_series(series_fixture())
    assert p.source == "mangaupdates"
    assert p.type == "manhwa"
    assert p.year == 2018
    assert "Solo Leveling" in p.titles["english"]
    assert len(p.titles["synonym"]) >= 20  # associated names
    assert p.score == series_fixture()["bayesian_rating"]
    assert p.score_votes == series_fixture()["rating_votes"]


def test_parse_series_categories_become_weighted_tropes():
    p = parse_series(series_fixture())
    tropes = {t.name: t for t in p.tags if t.kind == "trope"}
    assert "Overpowered Protagonist" in tropes
    assert 0.0 < tropes["Overpowered Protagonist"].weight <= 1.0
    assert tropes["Overpowered Protagonist"].votes > 50
    genres = [t for t in p.tags if t.kind == "genre"]
    assert genres  # Action, Adventure, ...


def test_parse_series_uses_category_recommendations_not_plain():
    p = parse_series(series_fixture())
    # plain `recommendations` is sparse junk (weight<=3); category_recommendations
    # has weights in the tens of thousands
    assert max(s.votes for s in p.similar) > 10_000


def test_parse_series_relations():
    p = parse_series(series_fixture())
    rel_types = {r.rel_type for r in p.relations}
    assert "sequel" in rel_types


def test_parse_search_hits():
    hits = parse_search(load_fixture("mu_search.json"))
    assert hits[0].source == "mangaupdates"
    assert hits[0].title


def test_search_hit_has_thumbnail():
    hits = parse_search(load_fixture("mu_search.json"))
    assert hits[0].cover_url == "https://img.example/mu1.jpg"
