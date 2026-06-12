from app.engine.suggest import suggest_seeds
from tests.factory import link_tag, make_work


def rate(conn, work_id, overall):
    conn.execute("INSERT INTO ratings(work_id, overall) VALUES(?,?)", (work_id, overall))
    conn.commit()


def seed(conn, title, **kw):
    work_id = make_work(conn, title, **kw)
    conn.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (work_id,))
    conn.commit()
    return work_id


def test_buckets_by_rating_and_excludes_seeded(catalog):
    great = make_work(catalog, "Great")
    awful = make_work(catalog, "Awful")
    meh = make_work(catalog, "Meh")
    seeded = seed(catalog, "AlreadySeeded")
    rate(catalog, great, 9)
    rate(catalog, awful, 2)
    rate(catalog, meh, 6)       # mid ratings are suggested in neither bucket
    rate(catalog, seeded, 10)   # seeded works never suggested
    s = suggest_seeds(catalog)
    assert [c["canonical_title"] for c in s["seed"]] == ["Great"]
    assert [c["canonical_title"] for c in s["anti"]] == ["Awful"]


def test_seed_bucket_ranked_by_tag_overlap_with_seed_centroid(catalog):
    s1 = seed(catalog, "SeedA")
    link_tag(catalog, s1, "Regression", 0.9)
    on_brand = make_work(catalog, "OnBrand")
    link_tag(catalog, on_brand, "Regression", 0.8)
    off_brand = make_work(catalog, "OffBrand")
    link_tag(catalog, off_brand, "Cooking", 0.8)
    rate(catalog, on_brand, 8)
    rate(catalog, off_brand, 10)  # higher rating, zero overlap → ranked below
    s = suggest_seeds(catalog)
    assert [c["canonical_title"] for c in s["seed"]] == ["OnBrand", "OffBrand"]


def test_cold_start_ranks_by_rating(catalog):
    a = make_work(catalog, "Nine")
    b = make_work(catalog, "Ten")
    rate(catalog, a, 9)
    rate(catalog, b, 10)
    s = suggest_seeds(catalog)  # no seeds at all → centroid empty
    assert [c["canonical_title"] for c in s["seed"]] == ["Ten", "Nine"]


def test_anti_bucket_ranked_by_badness_and_capped(catalog):
    for i, overall in enumerate((4, 1, 2, 3)):
        rate(catalog, make_work(catalog, f"Bad{overall}"), overall)
    s = suggest_seeds(catalog)
    assert [c["canonical_title"] for c in s["anti"]] == ["Bad1", "Bad2", "Bad3"]  # capped at 3
