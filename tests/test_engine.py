from app import db
from app.engine.score import recommend
from tests.factory import link_similar, link_tag, make_work


def seed(conn, title="Seed", **kw):
    work_id = make_work(conn, title, **kw)
    conn.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (work_id,))
    conn.commit()
    return work_id


def test_skipped_counts_scoring_works_below_gate(catalog):
    s = seed(catalog)
    good = make_work(catalog, "Good", quality=8.0)
    meh = make_work(catalog, "Meh", quality=6.0)      # scores, but under the 7.5 gate
    make_work(catalog, "Noise", quality=5.0)           # never scores: no edges, no tags
    link_similar(catalog, s, good, 500)
    link_similar(catalog, s, meh, 500)
    results, skipped = recommend(catalog, with_skipped=True)
    assert [r.title for r in results] == ["Good"]
    assert skipped == 1


def test_quality_gate_excludes(catalog):
    s = seed(catalog)
    good = make_work(catalog, "Good", quality=8.0)
    trash = make_work(catalog, "Trash", quality=5.0)
    link_similar(catalog, s, good, 500)
    link_similar(catalog, s, trash, 500)
    titles = [r.title for r in recommend(catalog)]
    assert "Good" in titles and "Trash" not in titles


def test_more_votes_rank_higher(catalog):
    s = seed(catalog)
    big = make_work(catalog, "Big", quality=8.0)
    small = make_work(catalog, "Small", quality=8.0)
    link_similar(catalog, s, big, 1000)
    link_similar(catalog, s, small, 10)
    results = recommend(catalog)
    assert [r.title for r in results[:2]] == ["Big", "Small"]
    assert results[0].score > results[1].score    # support drives MATCH, no log cap


def test_user_list_and_stubs_never_recommended(catalog):
    s = seed(catalog)
    read = make_work(catalog, "AlreadyRead", quality=9.0)
    stub = make_work(catalog, "Stub", quality=9.0, is_stub=1)
    link_similar(catalog, s, read, 500)
    link_similar(catalog, s, stub, 500)
    catalog.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'read')", (read,))
    catalog.commit()
    titles = [r.title for r in recommend(catalog)]
    assert titles == []


def test_required_trope_filters_hard(catalog):
    s = seed(catalog)
    with_trope = make_work(catalog, "HasIt", quality=8.0)
    without = make_work(catalog, "LacksIt", quality=8.0)
    link_similar(catalog, s, with_trope, 100)
    link_similar(catalog, s, without, 100)
    tag_id = link_tag(catalog, with_trope, "Overpowered Main Character", 0.8)
    catalog.execute("INSERT INTO trope_weights(tag_id, mode, weight) VALUES(?, 'require', 1.0)",
                    (tag_id,))
    catalog.commit()
    titles = [r.title for r in recommend(catalog)]
    assert titles == ["HasIt"]


def test_excluded_trope_filters_hard(catalog):
    s = seed(catalog)
    harem = make_work(catalog, "HaremThing", quality=8.0)
    clean = make_work(catalog, "Clean", quality=8.0)
    link_similar(catalog, s, harem, 100)
    link_similar(catalog, s, clean, 100)
    tag_id = link_tag(catalog, harem, "Harem", 0.7)
    catalog.execute("INSERT INTO trope_weights(tag_id, mode, weight) VALUES(?, 'exclude', 1.0)",
                    (tag_id,))
    catalog.commit()
    titles = [r.title for r in recommend(catalog)]
    assert titles == ["Clean"]


def test_discarded_work_acts_as_negative_seed(catalog):
    s = seed(catalog)
    discarded = make_work(catalog, "Hated", quality=8.0)
    catalog.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'discarded')", (discarded,))
    near_hated = make_work(catalog, "NearHated", quality=8.0)
    neutral = make_work(catalog, "Neutral", quality=8.0)
    link_similar(catalog, s, near_hated, 100)
    link_similar(catalog, s, neutral, 100)
    link_similar(catalog, discarded, near_hated, 800)
    catalog.commit()
    results = {r.title: r.score for r in recommend(catalog)}
    assert results["Neutral"] > results["NearHated"]


def test_seed_franchise_excluded_by_default(catalog):
    s = seed(catalog)
    sequel = make_work(catalog, "Seed II", quality=9.0)
    catalog.execute("UPDATE works SET franchise_id=(SELECT franchise_id FROM works WHERE id=?)"
                    " WHERE id=?", (s, sequel))
    link_similar(catalog, s, sequel, 2000)
    catalog.commit()
    assert "Seed II" not in [r.title for r in recommend(catalog)]


def test_explanations_have_receipts(catalog):
    s = seed(catalog, title="Solo Leveling")
    cand = make_work(catalog, "ORV", quality=8.6)
    link_similar(catalog, s, cand, 1367)
    results = recommend(catalog)
    assert any("1,367 votes" in w and "Solo Leveling" in w for w in results[0].why)


def test_adult_hidden_by_default(catalog):
    s = seed(catalog)
    adult = make_work(catalog, "Spicy", quality=9.0, is_adult=1)
    link_similar(catalog, s, adult, 500)
    assert "Spicy" not in [r.title for r in recommend(catalog)]


def test_no_seed_require_browse_is_quality_ranked(catalog):
    a = make_work(catalog, "GoodMatch", quality=9.0)
    b = make_work(catalog, "OkMatch", quality=7.5)
    tag_id = link_tag(catalog, a, "Cultivation", 0.9)
    link_tag(catalog, b, "Cultivation", 0.9)
    catalog.execute("INSERT INTO trope_weights(tag_id, mode, weight) VALUES(?, 'require', 1.0)",
                    (tag_id,))
    catalog.commit()
    titles = [r.title for r in recommend(catalog)]
    assert titles == ["GoodMatch", "OkMatch"]


def _seeded_pair(conn):
    s = make_work(conn, "Seed")
    conn.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (s,))
    a = make_work(conn, "Old Gem", year=2010, quality=9.0)
    b = make_work(conn, "New Decent", year=2024, quality=7.5)
    link_similar(conn, s, a, 100)
    link_similar(conn, s, b, 100)
    conn.commit()
    return a, b


def test_sort_by_quality(catalog):
    a, b = _seeded_pair(catalog)
    titles = [r.title for r in recommend(catalog, sort="quality")]
    assert titles.index("Old Gem") < titles.index("New Decent")


def test_sort_by_year(catalog):
    _seeded_pair(catalog)
    titles = [r.title for r in recommend(catalog, sort="year")]
    assert titles.index("New Decent") < titles.index("Old Gem")


def test_type_filter(catalog):
    s = make_work(catalog, "Seed")
    catalog.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (s,))
    manga = make_work(catalog, "A Manga", type="manga")
    manhwa = make_work(catalog, "A Manhwa", type="manhwa")
    link_similar(catalog, s, manga, 50)
    link_similar(catalog, s, manhwa, 50)
    catalog.commit()
    titles = [r.title for r in recommend(catalog, work_type="manga")]
    assert "A Manga" in titles and "A Manhwa" not in titles


def test_min_quality_overrides_gate(catalog):
    s = make_work(catalog, "Seed")
    catalog.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (s,))
    low = make_work(catalog, "Low", quality=6.0)  # below default gate 7.5
    link_similar(catalog, s, low, 50)
    catalog.commit()
    assert "Low" not in [r.title for r in recommend(catalog)]
    assert "Low" in [r.title for r in recommend(catalog, min_quality=5.0)]


def test_scored_carries_presentation_fields(catalog):
    _seeded_pair(catalog)
    r = recommend(catalog)[0]
    assert r.first_seen_at is not None
    assert hasattr(r, "cover_color")


def test_negative_similarity_votes_do_not_crash(catalog):
    s = make_work(catalog, "Seed")
    catalog.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (s,))
    good = make_work(catalog, "Good", quality=8.0)
    link_similar(catalog, s, good, 100)
    bad = make_work(catalog, "Downvoted", quality=8.0)
    link_similar(catalog, s, bad, -2)
    titles = [r.title for r in recommend(catalog)]
    assert "Good" in titles


def test_rating_affinity_anchors():
    from app.engine.score import rating_affinity
    assert rating_affinity(10) == 1.5    # perfect pulls extra
    assert rating_affinity(9) == 1.25
    assert rating_affinity(8) == 1.0     # 4★ ≡ unrated seed
    assert rating_affinity(7) == 0.5
    assert rating_affinity(6) == 0.0     # 3★ neutral
    assert rating_affinity(5) == -0.25
    assert rating_affinity(4) == -0.5    # 2★ ≡ discard weight
    assert rating_affinity(3) == -0.75   # interior of the floor segment, not clamped
    assert rating_affinity(2) == -1.0
    assert rating_affinity(1) == -1.0    # 0.5★ clamps at the floor


def rate(conn, work_id, overall):
    conn.execute("INSERT INTO ratings(work_id, overall) VALUES(?,?)"
                 " ON CONFLICT(work_id) DO UPDATE SET overall=excluded.overall",
                 (work_id, overall))
    conn.commit()


def test_perfect_rated_seed_pulls_harder_than_unrated(catalog):
    s = seed(catalog)
    cand = make_work(catalog, "Cand", quality=8.0)
    link_similar(catalog, s, cand, 500)
    base = recommend(catalog)[0].score
    rate(catalog, s, 10)
    assert recommend(catalog)[0].score > base


def test_low_rated_seed_is_anti_seed(catalog):
    s = seed(catalog)
    cand = make_work(catalog, "Pushed", quality=8.0)
    link_similar(catalog, s, cand, 500)
    assert [r.title for r in recommend(catalog)] == ["Pushed"]
    rate(catalog, s, 4)  # 2★ → negative affinity → score <= 0 → dropped
    assert recommend(catalog) == []


def test_unrated_seed_keeps_stored_affinity(catalog):
    s = seed(catalog)
    cand = make_work(catalog, "Cand", quality=8.0)
    link_similar(catalog, s, cand, 500)
    rate(catalog, s, 8)  # 4★ ≡ +1.0 ≡ unrated
    rated = recommend(catalog)[0].score
    catalog.execute("DELETE FROM ratings WHERE work_id=?", (s,))
    catalog.commit()
    assert recommend(catalog)[0].score == rated


def test_anti_seed_does_not_dilute_other_seeds(catalog):
    s1 = seed(catalog, "Loved")
    cand = make_work(catalog, "Cand", quality=8.0)
    link_similar(catalog, s1, cand, 500)
    rate(catalog, s1, 10)
    before = recommend(catalog)[0].score
    s2 = seed(catalog, "Hated")  # no similarity edges of its own
    rate(catalog, s2, 4)         # 2★ → anti-seed; must act like a discard
    assert recommend(catalog)[0].score == before


def read_item(conn, title="Read", overall=None, **kw):
    work_id = make_work(conn, title, **kw)
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'read')", (work_id,))
    conn.commit()
    if overall is not None:
        rate(conn, work_id, overall)
    return work_id


def test_seed_all_read_pulls_via_read_items(catalog):
    r = read_item(catalog, "ReadSeed")                 # read, unrated → pulls at 1.0
    cand = make_work(catalog, "Cand", quality=8.0)
    link_similar(catalog, r, cand, 1500)
    db.set_setting(catalog, "seed_all_read", "1")
    cand_row = next(x for x in recommend(catalog) if x.title == "Cand")
    assert any("ReadSeed" in w for w in cand_row.why)   # the read item acted as a seed
    db.set_setting(catalog, "seed_all_read", "0")
    # off: read item is not a seed, so nothing is credited to it
    assert not any("ReadSeed" in w for x in recommend(catalog) for w in x.why)


def test_seed_all_read_rating_sets_pull_direction(catalog):
    db.set_setting(catalog, "seed_all_read", "1")
    loved = read_item(catalog, "Loved", overall=10)     # 5★ → strong pull
    hated = read_item(catalog, "Hated", overall=2)      # 1★ → anti-seed (push)
    near_loved = make_work(catalog, "NearLoved", quality=8.0)
    near_hated = make_work(catalog, "NearHated", quality=8.0)
    link_similar(catalog, loved, near_loved, 500)
    link_similar(catalog, hated, near_hated, 500)
    titles = [x.title for x in recommend(catalog)]
    assert "NearLoved" in titles       # pulled in by the 5★ read
    assert "NearHated" not in titles   # pushed out by the 1★ read (anti-seed)


def test_seed_all_read_ignores_manual_seeds(catalog):
    s = seed(catalog, "ManualSeed")
    cand = make_work(catalog, "Cand", quality=8.0)
    link_similar(catalog, s, cand, 1500)
    db.set_setting(catalog, "seed_all_read", "1")       # mutex on, zero read items
    # manual seed is locked out → it never appears as a receipt
    assert not any("ManualSeed" in w for x in recommend(catalog) for w in x.why)


def test_seed_all_read_excludes_read_items_from_results(catalog):
    db.set_setting(catalog, "seed_all_read", "1")
    a = read_item(catalog, "A", overall=10)
    b = read_item(catalog, "B", overall=10)
    link_similar(catalog, a, b, 500)
    titles = [x.title for x in recommend(catalog)]
    assert "A" not in titles and "B" not in titles


def test_noisy_or_preserves_strong_single_seed(catalog):
    # Two seeds. A title strongly endorsed by ONE must not be diluted by the silent other.
    s1 = seed(catalog, "S1")
    s2 = seed(catalog, "S2")
    both = make_work(catalog, "Both", quality=8.0)
    one = make_work(catalog, "One", quality=8.0)
    link_similar(catalog, s1, both, 300)
    link_similar(catalog, s2, both, 300)
    link_similar(catalog, s1, one, 900)        # one strong endorsement only
    by = {r.title: r.score for r in recommend(catalog, now=2026.5)}
    assert by["Both"] > by["One"]              # breadth still wins at the top
    assert by["One"] > 0.4                     # strong single endorsement not halved away


def test_match_excludes_title_rating(catalog):
    # Equal support, different rating -> equal MATCH (rating is no longer in the score).
    s = seed(catalog)
    hi = make_work(catalog, "HiRated", quality=9.5)
    lo = make_work(catalog, "LoRated", quality=7.6)
    link_similar(catalog, s, hi, 200)
    link_similar(catalog, s, lo, 200)
    by = {r.title: r.score for r in recommend(catalog, now=2026.5)}
    assert abs(by["HiRated"] - by["LoRated"]) < 1e-9


def test_high_affinity_seed_does_not_flatten_match(catalog):
    # A strong seed (affinity 1.5, e.g. a 5-star rating) must not saturate every
    # well-supported candidate to the same MATCH: support/velocity must still order
    # them. Regression: p = min(1, s*affinity) clipped them all to 1.0, which
    # surfaced the rating tiebreak instead of vote support.
    s = make_work(catalog, "S")
    catalog.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.5)", (s,))
    big = make_work(catalog, "Big", quality=7.6)      # more support, lower rating
    small = make_work(catalog, "Small", quality=8.1)  # less support, higher rating
    link_similar(catalog, s, big, 800)
    link_similar(catalog, s, small, 120)
    catalog.commit()
    results = recommend(catalog, now=2026.5)
    by = {r.title: r.score for r in results}
    assert by["Big"] > by["Small"]                    # support wins, not rating
    assert [r.title for r in results[:2]] == ["Big", "Small"]


def test_release_of_combines_year_and_month():
    from app.engine.score import release_of
    assert release_of(2020, None) == 2020.0
    assert release_of(2020, 1) == 2020.0          # month 1 = start of year
    assert abs(release_of(2020, 7) - 2020.5) < 1e-9
    assert release_of(None, 5) is None


def test_exposure_capped_at_seed_age_and_floored():
    from app.engine.score import exposure_years
    # candidate older than seed -> window is the seed's age, not the candidate's
    assert exposure_years(2010.0, 2018.0, 2026.0, 0.5) == 8.0
    # candidate newer than seed -> its own (shorter) window
    assert exposure_years(2024.0, 2018.0, 2026.0, 0.5) == 2.0
    # fresh title floored
    assert exposure_years(2026.0, 2018.0, 2026.0, 0.5) == 0.5


def test_velocity_strength_curve_and_guard():
    from app.engine.score import velocity_strength
    assert velocity_strength(5, 1.0, 6.0, 10) == 0.0        # below min_votes
    assert velocity_strength(60, 10.0, 6.0, 10) == 0.5       # rate 6 == K -> 0.5
    hi = velocity_strength(800, 1.0, 6.0, 10)
    lo = velocity_strength(60, 1.0, 6.0, 10)
    assert hi > lo and 0 < lo < hi < 1


def test_recommend_query_count_is_independent_of_candidate_count(catalog):
    # N+1 guard: recommend() must not issue a work_tags query per candidate.
    # Two trope chips are set so the tag-weight load actually runs (and must
    # stay a single bulk query, not one-per-candidate).
    s = seed(catalog)
    tag = link_tag(catalog, s, "Action", 0.5)
    catalog.execute("INSERT INTO trope_weights(tag_id, mode, weight) VALUES(?, 'boost', 1.0)", (tag,))
    for i in range(100):
        cand = make_work(catalog, f"Cand{i}", quality=8.0)
        link_similar(catalog, s, cand, 100)
        link_tag(catalog, cand, "Action", 0.5)
    catalog.commit()
    count = [0]
    catalog.set_trace_callback(lambda _sql: count.__setitem__(0, count[0] + 1))
    try:
        results = recommend(catalog, limit=200)
    finally:
        catalog.set_trace_callback(None)
    assert len(results) == 100
    assert count[0] < 30, f"recommend issued {count[0]} statements for 100 candidates (N+1?)"


def test_rising_flag_recent_strong_yes_old_no(catalog):
    s = seed(catalog)                                    # factory default year 2020
    fresh = make_work(catalog, "Fresh", year=2026, quality=8.0)
    old = make_work(catalog, "Old", year=2012, quality=8.0)
    link_similar(catalog, s, fresh, 200)
    link_similar(catalog, s, old, 200)
    by = {r.title: r for r in recommend(catalog, now=2026.5)}
    assert by["Fresh"].rising is True       # short window + high velocity
    assert by["Old"].rising is False        # 8yr window -> not rising


def test_rising_requires_enough_votes(catalog):
    s = seed(catalog)
    thin = make_work(catalog, "Thin", year=2026, quality=8.0)
    link_similar(catalog, s, thin, 5)       # below min_votes=10 -> no signal at all
    assert [r for r in recommend(catalog, now=2026.5) if r.title == "Thin"] == []
