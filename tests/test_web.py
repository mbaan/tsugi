import time

from app import db
from app.sources.dto import SimilarRef
from tests.factory import link_similar, link_source, link_tag, make_payload, make_work


def test_dashboard_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Tsugi" in r.text


def test_empty_recommendations_message(client):
    r = client.get("/recommendations")
    assert "No recommendations yet" in r.text


def test_grid_shows_scored_work_with_receipts(client):
    conn = client.app_ref.state.catalog
    s = make_work(conn, "Solo Leveling")
    conn.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (s,))
    cand = make_work(conn, "ORV", quality=8.6)
    link_similar(conn, s, cand, 1367)
    r = client.get("/recommendations")
    assert "ORV" in r.text
    assert "1,367 votes" in r.text


def test_rising_badge_renders(client):
    conn = client.app_ref.state.catalog
    s = make_work(conn, "RBSeed")
    conn.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (s,))
    fresh = make_work(conn, "RBFresh", year=2026, quality=8.0)
    link_similar(conn, s, fresh, 300)
    r = client.get("/recommendations")
    assert "新星" in r.text


def test_search_returns_local_hits(client):
    conn = client.app_ref.state.catalog
    make_work(conn, "Solo Leveling")
    r = client.get("/search", params={"q": "solo"})
    assert "Solo Leveling" in r.text  # local hit; FakeSource returns no remote hits


def test_index_endpoint_fetches_and_stores(client):
    src = client.app_ref.state.sources["fake"]
    src.payloads["s1"] = make_payload(source="fake", source_key="s1", title="Indexed Title")
    r = client.post("/index", data={"source": "fake", "source_key": "s1"})
    assert r.status_code == 204
    assert "data-changed" in r.headers.get("HX-Trigger", "")
    conn = client.app_ref.state.catalog
    row = conn.execute("SELECT canonical_title FROM works WHERE is_stub=0").fetchone()
    assert row["canonical_title"] == "Indexed Title"


def test_crawl_endpoint_runs_job_to_completion(client):
    src = client.app_ref.state.sources["fake"]
    src.payloads["s1"] = make_payload(
        source="fake", source_key="s1", title="Seed",
        similar=(SimilarRef(source="fake", source_key="n1", title="N1", votes=50),))
    src.payloads["n1"] = make_payload(source="fake", source_key="n1", title="N1")
    client.post("/index", data={"source": "fake", "source_key": "s1"})
    conn = client.app_ref.state.catalog
    work_id = conn.execute("SELECT work_id FROM work_sources WHERE source_key='s1'").fetchone()[0]

    r = client.post(f"/works/{work_id}/crawl", data={"depth": "2"})
    assert r.status_code == 204
    job_id = conn.execute("SELECT id FROM crawl_jobs ORDER BY id DESC").fetchone()[0]
    for _ in range(40):  # poll until background task finishes
        status = conn.execute("SELECT status FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()[0]
        if status != "running":
            break
        time.sleep(0.05)
    assert status == "done"
    assert "n1" in src.fetch_calls
    assert "done" in client.get("/crawler").text


def test_search_matches_accented_query(client):
    conn = client.app_ref.state.catalog
    make_work(conn, "Eclair The 2nd")
    r = client.get("/search", params={"q": "Éclair:"})
    assert "Eclair The 2nd" in r.text


def test_seed_toggle(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Alpha")
    client.post(f"/seeds/{w}")
    assert conn.execute("SELECT COUNT(*) c FROM seeds").fetchone()["c"] == 1
    client.post(f"/seeds/{w}")
    assert conn.execute("SELECT COUNT(*) c FROM seeds").fetchone()["c"] == 0


def test_seed_all_toggle_flips_setting(client):
    conn = client.app_ref.state.catalog
    assert db.get_setting(conn, "seed_all_read") in (None, "0")
    r = client.post("/tuning/seed-all")
    assert r.status_code == 204
    assert "data-changed" in r.headers.get("HX-Trigger", "")
    assert db.get_setting(conn, "seed_all_read") == "1"
    client.post("/tuning/seed-all")
    assert db.get_setting(conn, "seed_all_read") == "0"


def test_trope_chip_cycles_modes(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Alpha")
    tag_id = link_tag(conn, w, "Cultivation", 0.9)
    for expected in ("require", "boost", "exclude"):
        client.post(f"/tropes/{tag_id}")
        row = conn.execute("SELECT mode FROM trope_weights WHERE tag_id=?", (tag_id,)).fetchone()
        assert row["mode"] == expected
    client.post(f"/tropes/{tag_id}")  # fourth click removes the chip
    assert conn.execute("SELECT COUNT(*) c FROM trope_weights").fetchone()["c"] == 0


def test_discard_with_reasons_records_feedback(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Disliked")
    tag_id = link_tag(conn, w, "Harem", 0.8)
    modal = client.get(f"/works/{w}/discard")
    assert "Harem" in modal.text
    client.post(f"/list/{w}?status=discarded",
                data={"tag_ids": [str(tag_id)], "note": "not for me"})
    assert conn.execute("SELECT status FROM user_list WHERE work_id=?", (w,)).fetchone()[0] == "discarded"
    fb = conn.execute("SELECT * FROM discard_feedback WHERE work_id=?", (w,)).fetchone()
    assert fb["tag_id"] == tag_id


def test_review_merge_combines_works(client):
    conn = client.app_ref.state.catalog
    kept = make_work(conn, "Alpha")
    dup = make_work(conn, "Alpha?")
    link_source(conn, kept, "anilist", "1")
    link_source(conn, dup, "mangaupdates", "9")
    conn.execute("INSERT INTO match_reviews(work_id, candidate_work_id, reason)"
                 " VALUES(?,?, 'test')", (dup, kept))
    conn.commit()
    review_id = conn.execute("SELECT id FROM match_reviews").fetchone()[0]
    client.post(f"/reviews/{review_id}", data={"action": "merge"})
    assert conn.execute("SELECT COUNT(*) c FROM works WHERE id=?", (dup,)).fetchone()["c"] == 0


def test_settings_update(client):
    conn = client.app_ref.state.catalog
    client.post("/settings", data={"quality_gate": "7.5", "show_adult": "1"})
    assert db.get_float(conn, "quality_gate") == 7.5
    assert db.get_setting(conn, "show_adult") == "1"


def test_settings_rejects_malformed_float(client):
    conn = client.app_ref.state.catalog
    r = client.post("/settings", data={"quality_gate": "abc"})
    assert r.status_code == 204
    assert db.get_float(conn, "quality_gate") == 7.5  # unchanged


def test_resolve_stale_review_is_noop(client):
    r = client.post("/reviews/9999", data={"action": "merge"})
    assert r.status_code == 204


class _FakeImageClient:
    def __init__(self, content=b"fakejpg", content_type="image/jpeg"):
        self.content, self.content_type, self.calls = content, content_type, 0

    async def get(self, url):
        self.calls += 1
        client = self

        class R:
            content = client.content
            headers = {"content-type": client.content_type}

            def raise_for_status(self):
                pass

        return R()


def test_cover_redirects_when_no_http_client(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Alpha")
    conn.execute("UPDATE works SET cover_url='https://cdn.example/a.jpg' WHERE id=?", (w,))
    conn.commit()
    r = client.get(f"/covers/{w}", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "https://cdn.example/a.jpg"


def test_cover_fetches_once_then_serves_from_disk(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Alpha")
    conn.execute("UPDATE works SET cover_url='https://cdn.example/a.jpg' WHERE id=?", (w,))
    conn.commit()
    fake = _FakeImageClient()
    client.app_ref.state.client = fake
    assert client.get(f"/covers/{w}").status_code == 200
    client.app_ref.state.client = None  # second hit must not need the network
    r = client.get(f"/covers/{w}", follow_redirects=False)
    assert r.status_code == 200
    assert fake.calls == 1


def test_cover_placeholder_when_work_has_no_url(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "NoCover")
    r = client.get(f"/covers/{w}", follow_redirects=False)
    assert r.status_code == 200
    assert "image/svg" in r.headers["content-type"]
    assert ">N<" in r.text


def test_shell_has_theme_and_assets(client):
    r = client.get("/")
    assert 'data-theme' in r.text
    assert "/static/app.css" in r.text
    assert "/static/app.js" in r.text
    assert "Tsugi" in r.text
    assert "Discover" in r.text and "Library" in r.text


def test_nav_count_totals_user_list(client):
    conn = client.app_ref.state.catalog
    a, b = make_work(conn, "A"), make_work(conn, "B")
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'want')", (a,))
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'read')", (b,))
    conn.commit()
    assert client.get("/nav/count").text == "2"


def test_jobs_active_empty_when_idle(client):
    assert "crawling" not in client.get("/jobs/active").text


def test_jobs_active_shows_running_totals(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Seed")
    conn.execute("INSERT INTO crawl_jobs(seed_work_id, max_depth, budget, fetched)"
                 " VALUES(?, 2, 300, 34)", (w,))
    conn.commit()
    body = client.get("/jobs/active").text
    assert "crawling" in body and "34" in body


def test_crawl_does_not_duplicate_a_running_job(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Seed")
    conn.execute("INSERT INTO crawl_jobs(seed_work_id, max_depth, budget, status)"
                 " VALUES(?, 2, 300, 'running')", (w,))
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM crawl_jobs WHERE seed_work_id=?", (w,)).fetchone()[0]
    r = client.post(f"/works/{w}/crawl", data={"depth": "2"})
    assert r.status_code == 204
    after = conn.execute("SELECT COUNT(*) FROM crawl_jobs WHERE seed_work_id=?", (w,)).fetchone()[0]
    assert after == before  # already crawling — no second job


def test_review_badge(client):
    assert client.get("/reviews/badge").text.strip() == ""
    conn = client.app_ref.state.catalog
    a, b = make_work(conn, "A"), make_work(conn, "A?")
    conn.execute("INSERT INTO match_reviews(work_id, candidate_work_id, reason)"
                 " VALUES(?,?, 'test')", (a, b))
    conn.commit()
    assert "1 to review" in client.get("/reviews/badge").text


def test_work_detail_partial_for_htmx(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Dungeon Reset")
    link_tag(conn, w, "Dungeon", 0.95)
    r = client.get(f"/works/{w}", headers={"HX-Request": "true"})
    assert "Dungeon Reset" in r.text
    assert "Dungeon" in r.text
    assert "<html" not in r.text  # partial, not a full page


def test_work_detail_full_page_without_htmx(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Dungeon Reset")
    r = client.get(f"/works/{w}")
    assert "<html" in r.text and "Dungeon Reset" in r.text


def test_work_detail_shows_similar_and_relations(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Dungeon Reset")
    other = make_work(conn, "Solo Leveling")
    link_similar(conn, w, other, 2144)
    conn.execute("INSERT INTO work_relations(work_id, related_work_id, rel_type)"
                 " VALUES(?,?, 'side story')", (w, other))
    conn.commit()
    body = client.get(f"/works/{w}", headers={"HX-Request": "true"}).text
    assert "Solo Leveling" in body and "2,144" in body and "side story" in body


def test_work_detail_404_for_unknown(client):
    assert client.get("/works/99999").status_code == 404


def test_picker_groups_by_category_and_marks_active(client):
    conn = client.app_ref.state.catalog
    from app.catalog.trope_dict import upsert_dictionary
    from tests.conftest import load_fixture
    upsert_dictionary(conn, load_fixture("anilist_tag_collection.json"))
    dungeon = conn.execute("SELECT id FROM tags WHERE name='Dungeon'").fetchone()["id"]
    conn.execute("INSERT INTO trope_weights(tag_id, mode, weight) VALUES(?, 'require', 1.0)",
                 (dungeon,))
    conn.commit()
    body = client.get("/tropes/picker").text
    # category "Setting-Scene" renders split into a styled group + subgroup
    assert 'cat-group">Setting<' in body and 'cat-sub">Scene<' in body
    assert "Dungeon" in body
    assert "require" in body  # active mode marked
    assert "Bondage" not in body  # adult hidden by default


def test_picker_shows_adult_when_enabled(client):
    conn = client.app_ref.state.catalog
    from app.catalog.trope_dict import upsert_dictionary
    from tests.conftest import load_fixture
    upsert_dictionary(conn, load_fixture("anilist_tag_collection.json"))
    db.set_setting(conn, "show_adult", "1")
    assert "Bondage" in client.get("/tropes/picker").text


def test_picker_filters_by_query(client):
    conn = client.app_ref.state.catalog
    from app.catalog.trope_dict import upsert_dictionary
    from tests.conftest import load_fixture
    upsert_dictionary(conn, load_fixture("anilist_tag_collection.json"))
    body = client.get("/tropes/picker", params={"q": "vamp"}).text
    assert "Vampire" in body and "Dungeon" not in body


def _libfill(conn):
    a = make_work(conn, "Want Me", quality=8.0)
    b = make_work(conn, "Read Me", quality=9.0)
    c = make_work(conn, "Rejected", quality=6.0)
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'want')", (a,))
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'read')", (b,))
    conn.execute("INSERT INTO user_list(work_id, status, note) VALUES(?, 'discarded', 'meh')", (c,))
    conn.commit()
    return a, b, c


def test_library_page_tabs_and_counts(client):
    _libfill(client.app_ref.state.catalog)
    body = client.get("/library").text
    assert "Want · 1" in body and "Read · 1" in body and "Not for me · 1" in body


def test_library_grid_filters_by_status(client):
    _libfill(client.app_ref.state.catalog)
    body = client.get("/library/grid", params={"status": "want"}).text
    assert "Want Me" in body and "Read Me" not in body


def test_library_grid_discarded_shows_reasons_and_restore(client):
    conn = client.app_ref.state.catalog
    _, _, c = _libfill(conn)
    tag_id = link_tag(conn, c, "Harem", 0.8)
    conn.execute("INSERT INTO discard_feedback(work_id, tag_id) VALUES(?,?)", (c, tag_id))
    conn.commit()
    body = client.get("/library/grid", params={"status": "discarded"}).text
    assert "Rejected" in body and "Harem" in body and "Restore" in body and "meh" in body


def test_library_grid_text_filter(client):
    _libfill(client.app_ref.state.catalog)
    conn = client.app_ref.state.catalog
    d = make_work(conn, "Want Too")
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'want')", (d,))
    conn.commit()
    body = client.get("/library/grid", params={"status": "want", "q": "too"}).text
    assert "Want Too" in body and "Want Me" not in body


def test_tuning_bar_renders_seeds_and_tropes(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Solo Leveling")
    conn.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (w,))
    tag_id = link_tag(conn, w, "Dungeon", 0.9)
    conn.execute("INSERT INTO trope_weights(tag_id, mode, weight) VALUES(?, 'require', 1.0)",
                 (tag_id,))
    conn.commit()
    body = client.get("/tuning").text
    assert "Solo Leveling" in body and "Dungeon" in body
    assert "Reset all" in body   # reset affordance shows once anything is active


def test_tuning_seed_all_locks_out_manual_seeds(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Solo Leveling")
    conn.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (w,))
    r = make_work(conn, "ReadOne")
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'read')", (r,))
    conn.commit()
    off = client.get("/tuning").text
    assert "Solo Leveling" in off and "all read" in off   # chip + toggle affordance
    db.set_setting(conn, "seed_all_read", "1")
    on = client.get("/tuning").text
    assert "Solo Leveling" not in on                       # manual chip locked out
    assert "All 1 Read title" in on                        # status pill with count


def test_recommendations_respects_sort_param(client):
    conn = client.app_ref.state.catalog
    s = make_work(conn, "Seed")
    conn.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (s,))
    old = make_work(conn, "Old Gem", year=2010, quality=9.0)
    new = make_work(conn, "New Decent", year=2024, quality=7.5)
    link_similar(conn, s, old, 100)
    link_similar(conn, s, new, 100)
    body = client.get("/recommendations", params={"sort": "year"}).text
    assert body.index("New Decent") < body.index("Old Gem")


def test_search_grouped_with_index_button(client):
    conn = client.app_ref.state.catalog
    make_work(conn, "Solo Leveling")

    async def fake_search(q):
        from app.sources.dto import SourceHit
        return [SourceHit(source="fake", source_key="r1", title="Remote Hit", year=2021,
                          type="manhwa", score=8.0, cover_url="https://img.example/r1.jpg")]

    client.app_ref.state.sources["fake"].search = fake_search
    body = client.get("/search", params={"q": "solo"}).text
    assert "In your catalog" in body and "Solo Leveling" in body
    assert "From sources" in body and "Remote Hit" in body and "Index" in body


def test_search_reports_failing_source(client):
    async def boom(q):
        raise RuntimeError("down")

    client.app_ref.state.sources["fake"].search = boom
    body = client.get("/search", params={"q": "anything"}).text
    assert "fake unavailable" in body


def test_actions_return_204_with_trigger(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Alpha")
    for response in (client.post(f"/seeds/{w}"),
                     client.post(f"/list/{w}?status=want"),
                     client.post("/settings", data={"quality_gate": "7.0"})):
        assert response.status_code == 204
        assert "data-changed" in response.headers.get("HX-Trigger", "")


def test_restore_clears_status_and_feedback(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Rejected")
    tag_id = link_tag(conn, w, "Harem", 0.8)
    client.post(f"/list/{w}?status=discarded", data={"tag_ids": [str(tag_id)]})
    client.post(f"/list/{w}?status=restore")
    assert conn.execute("SELECT COUNT(*) c FROM user_list WHERE work_id=?", (w,)).fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM discard_feedback WHERE work_id=?", (w,)).fetchone()["c"] == 0


def test_list_status_via_form_body(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Alpha")
    r = client.post(f"/list/{w}", data={"status": "read"})
    assert r.status_code == 204
    assert conn.execute("SELECT status FROM user_list WHERE work_id=?", (w,)).fetchone()[0] == "read"


def test_settings_panel_includes_reviews_and_theme(client):
    body = client.get("/settings").text
    assert "Merge reviews" in body and 'name="theme"' in body


def test_discard_dialog_uses_not_for_me_wording(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Disliked")
    body = client.get(f"/works/{w}/discard").text
    assert "for you" in body and "Discard" not in body


def test_work_detail_similar_deduped(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Dungeon Reset")
    other = make_work(conn, "Solo Leveling")
    link_similar(conn, w, other, 100)
    link_similar(conn, other, w, 80)
    link_similar(conn, w, other, 90, source="mangaupdates")
    body = client.get(f"/works/{w}", headers={"HX-Request": "true"}).text
    assert body.count("Solo Leveling") == 1


def test_library_defaults_to_read_tab(client):
    body = client.get("/library").text
    assert '<input type="hidden" name="status" value="read">' in body
    assert body.index("Read ·") < body.index("Want ·")


def test_work_detail_stub_similar_offers_index(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Dungeon Reset")
    stub = make_work(conn, "Stubby", is_stub=1)
    link_source(conn, stub, "fake", "k9")
    link_similar(conn, w, stub, 42)
    body = client.get(f"/works/{w}", headers={"HX-Request": "true"}).text
    assert 'value="k9"' in body and "+ index" in body


def test_jobs_active_shows_recent_finish(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Seed")
    conn.execute("INSERT INTO crawl_jobs(seed_work_id, max_depth, budget, status, fetched,"
                 " finished_at) VALUES(?, 2, 300, 'done', 7, datetime('now'))", (w,))
    conn.commit()
    body = client.get("/jobs/active").text
    assert "7 fetched" in body


def test_crawl_fires_queued_event(client):
    src = client.app_ref.state.sources["fake"]
    src.payloads["s1"] = make_payload(source="fake", source_key="s1", title="Seed")
    client.post("/index", data={"source": "fake", "source_key": "s1"})
    conn = client.app_ref.state.catalog
    work_id = conn.execute("SELECT work_id FROM work_sources WHERE source_key='s1'").fetchone()[0]
    r = client.post(f"/works/{work_id}/crawl", data={"depth": "1"})
    assert "crawl-queued" in r.headers.get("HX-Trigger", "")


def test_work_modal_has_add_to_dropdown(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Alpha")
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'read')", (w,))
    conn.commit()
    body = client.get(f"/works/{w}", headers={"HX-Request": "true"}).text
    assert 'name="status"' in body and "Not for me…" in body
    assert '<option value="read" selected>Read</option>' in body


def test_rating_upsert_and_clear(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Rated")
    r = client.post(f"/ratings/{w}", data={"field": "overall", "value": 9})
    assert r.status_code == 204
    assert "data-changed" in r.headers["HX-Trigger"]
    assert conn.execute("SELECT overall FROM ratings WHERE work_id=?", (w,)).fetchone()[0] == 9
    client.post(f"/ratings/{w}", data={"field": "art", "value": 7})
    assert conn.execute("SELECT art FROM ratings WHERE work_id=?", (w,)).fetchone()[0] == 7
    client.post(f"/ratings/{w}", data={"field": "overall", "value": 0})
    assert conn.execute("SELECT COUNT(*) c FROM ratings").fetchone()["c"] == 0


def test_rating_auto_marks_read(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Fresh")
    r = client.post(f"/ratings/{w}", data={"field": "overall", "value": 8})
    assert "marked-read" in r.headers["HX-Trigger"]
    assert conn.execute("SELECT status FROM user_list WHERE work_id=?", (w,)).fetchone()[0] == "read"
    w2 = make_work(conn, "Wanted")
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'want')", (w2,))
    conn.commit()
    client.post(f"/ratings/{w2}", data={"field": "overall", "value": 8})
    assert conn.execute("SELECT status FROM user_list WHERE work_id=?", (w2,)).fetchone()[0] == "read"
    w3 = make_work(conn, "Binned")
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'discarded')", (w3,))
    conn.commit()
    client.post(f"/ratings/{w3}", data={"field": "overall", "value": 8})
    assert conn.execute("SELECT status FROM user_list WHERE work_id=?", (w3,)).fetchone()[0] == "discarded"


def test_rerating_read_work_emits_no_marked_read(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "AlreadyRead")
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'read')", (w,))
    conn.commit()
    r = client.post(f"/ratings/{w}", data={"field": "overall", "value": 9})
    assert "marked-read" not in r.headers["HX-Trigger"]


def test_rating_error_cases(client):
    conn = client.app_ref.state.catalog
    assert client.post("/ratings/999", data={"field": "overall", "value": 8}).status_code == 404
    stub = make_work(conn, "StubWork", is_stub=1)
    assert client.post(f"/ratings/{stub}", data={"field": "overall", "value": 8}).status_code == 404
    w = make_work(conn, "NoOverallYet")
    assert client.post(f"/ratings/{w}", data={"field": "art", "value": 7}).status_code == 422
    assert client.post(f"/ratings/{w}", data={"field": "vibes", "value": 7}).status_code == 422


def test_title_view_renders_stars(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Starry")
    body = client.get(f"/works/{w}", headers={"HX-Request": "true"}).text
    assert f'hx-post="/ratings/{w}"' in body
    assert 'name="field" value="overall"' in body
    assert 'value="art"' not in body  # sub-rows hidden until an overall exists
    conn.execute("INSERT INTO ratings(work_id, overall, art) VALUES(?, 9, 7)", (w,))
    conn.commit()
    body = client.get(f"/works/{w}", headers={"HX-Request": "true"}).text
    assert 'value="art"' in body and 'value="story"' in body
    assert "★ 4.5" in body


def test_library_cards_render_overall_stars(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "CardStar")
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'read')", (w,))
    conn.execute("INSERT INTO ratings(work_id, overall) VALUES(?, 8)", (w,))
    conn.commit()
    body = client.get("/library/grid", params={"status": "read"}).text
    assert f'hx-post="/ratings/{w}"' in body
    assert "stars compact" in body


def test_modal_star_ids_do_not_collide_with_cards(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Dup")
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'read')", (w,))
    conn.execute("INSERT INTO ratings(work_id, overall) VALUES(?, 8)", (w,))
    conn.commit()
    modal = client.get(f"/works/{w}", headers={"HX-Request": "true"}).text
    cards = client.get("/library/grid", params={"status": "read"}).text
    assert f'id="modal-st-{w}-overall-8"' in modal
    assert f'id="st-{w}-overall-8"' in cards
    assert f'id="st-{w}-overall-8"' not in modal.replace(f'id="modal-st-{w}-overall-8"', "")


def test_tuning_shows_suggestions_and_pull_badges(client):
    conn = client.app_ref.state.catalog
    s = make_work(conn, "MySeed")
    conn.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (s,))
    conn.execute("INSERT INTO ratings(work_id, overall) VALUES(?, 9)", (s,))
    loved = make_work(conn, "LovedRead")
    conn.execute("INSERT INTO ratings(work_id, overall) VALUES(?, 10)", (loved,))
    hated = make_work(conn, "HatedRead")
    conn.execute("INSERT INTO ratings(work_id, overall) VALUES(?, 2)", (hated,))
    conn.commit()
    body = client.get("/tuning").text
    assert "+1.2" in body                        # rated seed shows derived pull
    assert "LovedRead" in body and f'hx-post="/seeds/{loved}"' in body
    assert "HatedRead" in body and "chip pick anti" in body
    assert body.count('<small class="pull">') == 1  # only the rated seed gets a badge


def test_banner_mixes_library_and_fill_and_repeats(client):
    conn = client.app_ref.state.catalog
    mine = make_work(conn, "Mine")
    conn.execute("UPDATE works SET cover_url='http://x/c.jpg' WHERE id=?", (mine,))
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'read')", (mine,))
    other = make_work(conn, "Other")
    conn.execute("UPDATE works SET cover_url='http://x/d.jpg' WHERE id=?", (other,))
    naughty = make_work(conn, "Naughty", is_adult=1)
    conn.execute("UPDATE works SET cover_url='http://x/e.jpg' WHERE id=?", (naughty,))
    bare = make_work(conn, "NoCover")  # no cover_url → never in banner
    conn.commit()
    body = client.get("/banner").text
    assert body.count("<img") == 24  # repeated to fill
    assert f"/covers/{mine}" in body and f"/covers/{other}" in body
    assert f"/covers/{naughty}" not in body  # show_adult defaults off
    assert f"/covers/{bare}" not in body


def test_banner_empty_catalog_renders_nothing(client):
    assert "<img" not in client.get("/banner").text


def test_banner_mounts_on_discover_and_library_only(client):
    assert 'hx-get="/banner"' in client.get("/").text
    assert 'hx-get="/banner"' in client.get("/library").text
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Solo")
    assert 'hx-get="/banner"' not in client.get(f"/works/{w}").text


def test_banner_includes_adult_fill_when_enabled(client):
    conn = client.app_ref.state.catalog
    naughty = make_work(conn, "Naughty", is_adult=1)
    conn.execute("UPDATE works SET cover_url='http://x/e.jpg' WHERE id=?", (naughty,))
    conn.commit()
    db.set_setting(conn, "show_adult", "1")
    assert f"/covers/{naughty}" in client.get("/banner").text


def test_library_card_offers_remove_and_it_removes(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Removable")
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'read')", (w,))
    conn.commit()
    body = client.get("/library/grid", params={"status": "read"}).text
    assert "Remove from library" in body  # lives in the card ⋯ menu now
    assert f'hx-post="/list/{w}?status=restore"' in body
    r = client.post(f"/list/{w}", data={"status": "restore"})
    assert r.status_code == 204
    assert conn.execute("SELECT COUNT(*) c FROM user_list").fetchone()["c"] == 0


def test_similar_strip_hides_noise_edges(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Main")
    strong = make_work(conn, "StrongSim")
    weak = make_work(conn, "WeakSim")
    link_similar(conn, w, strong, 5)   # at the anilist noise floor → shown
    link_similar(conn, w, weak, 2)     # below it → hidden, same rule as the crawler
    body = client.get(f"/works/{w}", headers={"HX-Request": "true"}).text
    assert "StrongSim" in body
    assert "WeakSim" not in body


def test_placeholder_cover_is_never_long_cached(client):
    conn = client.app_ref.state.catalog
    w = make_work(conn, "NoArtYet")  # factory works carry no cover_url
    r = client.get(f"/covers/{w}")
    assert r.headers["content-type"].startswith("image/svg")
    assert r.headers["cache-control"] == "no-store"


def test_grid_notes_results_hidden_by_quality_gate(client):
    conn = client.app_ref.state.catalog
    s = make_work(conn, "SeedW")
    conn.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (s,))
    conn.commit()
    good = make_work(conn, "GoodWork", quality=8.5)
    meh = make_work(conn, "MehWork", quality=6.0)
    link_similar(conn, s, good, 500)
    link_similar(conn, s, meh, 500)
    body = client.get("/recommendations").text
    assert "GoodWork" in body and "MehWork" not in body
    assert "1 more hidden below rating 7.5" in body
    body = client.get("/recommendations", params={"min_quality": "5"}).text
    assert "MehWork" in body and "hidden below rating" not in body


def test_library_card_shows_overall_only_art_story_in_dossier(client):
    """Cards stay uniform: overall stars only, never the art/story foldout —
    detail rating now lives in one consistent place, the title dossier."""
    conn = client.app_ref.state.catalog
    w = make_work(conn, "Nagged")
    conn.execute("INSERT INTO user_list(work_id, status) VALUES(?, 'read')", (w,))
    conn.commit()
    grid = lambda: client.get("/library/grid", params={"status": "read"}).text
    assert "substars" not in grid() and "rate art" not in grid()  # unrated card is clean
    assert f'hx-post="/ratings/{w}"' in grid()  # overall stars present
    conn.execute("INSERT INTO ratings(work_id, overall) VALUES(?, 8)", (w,))
    conn.commit()
    body = grid()  # rated: still just overall, no foldout sprouting on the card
    assert "substars" not in body and "rate art" not in body
    assert f'id="st-{w}-overall-8"' in body
    modal = client.get(f"/works/{w}", headers={"HX-Request": "true"}).text
    assert 'value="art"' in modal and 'value="story"' in modal  # detail rating in dossier


def test_all_routes_are_async(client):
    """Sync-def routes run in anyio worker threads, which shares the single
    sqlite connection across threads → statement corruption (InterfaceError /
    phantom empty reads) once the refresh loop writes concurrently."""
    import inspect

    from starlette.routing import Route

    offenders = [r.path for r in client.app_ref.routes
                 if isinstance(r, Route) and r.endpoint.__module__.startswith("app.")
                 and not inspect.iscoroutinefunction(r.endpoint)]
    assert offenders == []


def test_crawler_dashboard_shows_state_backlog_and_activity(client):
    from app.crawler.refresh import RefreshState
    conn = client.app_ref.state.catalog
    client.app_ref.state.refresh = RefreshState(sprint=True)
    fetched = make_work(conn, "JustAcquired")
    conn.execute("INSERT INTO refresh_log(action, work_id, label) VALUES('acquired',?,'JustAcquired')",
                 (fetched,))
    conn.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                 " VALUES('anilist','99','score',1)")  # one pending acquisition
    conn.commit()
    body = client.get("/crawler").text
    assert 'class="cr-status cr-work">acquiring' in body  # names the active tier
    assert "to acquire" in body and "to expand" in body  # backlog tiers labelled
    assert "JustAcquired" in body and "a-acquired" in body  # typed activity log
    assert 'hx-post="/refresh/sprint"' in body  # sprint button lives here now
    assert 'hx-get="/crawler"' in body  # self-polls while open


def test_crawler_status_names_the_activity_not_idle_when_backlogged(client):
    # Enabled (default), not sprinting, no recent fetch, but work is queued — the bar
    # must name the activity ("acquiring"), never the misleading "idle".
    from app.crawler.refresh import RefreshState
    conn = client.app_ref.state.catalog
    client.app_ref.state.refresh = RefreshState()
    conn.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                 " VALUES('anilist','77','score',1)")  # one queued acquisition
    conn.commit()
    body = client.get("/crawler").text
    assert 'class="cr-status cr-work">acquiring' in body
    assert "cr-idle" not in body


def test_refresh_sprint_endpoint_sets_flag(client):
    from app.crawler.refresh import RefreshState
    client.app_ref.state.refresh = RefreshState()
    r = client.post("/refresh/sprint")
    assert r.status_code == 204
    assert "refresh-started" in r.headers["HX-Trigger"]
    assert client.app_ref.state.refresh.sprint is True


def test_jobs_active_names_background_activity_and_count(client):
    from app.crawler.refresh import RefreshState
    conn = client.app_ref.state.catalog
    w = make_work(conn, "StaleOne")
    link_source(conn, w, "anilist", "5", fetched=True)
    conn.execute("UPDATE work_sources SET last_fetched_at=datetime('now','-30 days')")
    conn.commit()
    client.app_ref.state.refresh = RefreshState()  # enabled by default — has stale work
    body = client.get("/jobs/active").text
    assert "refreshing" in body and "1 to go" in body  # the actual tier + its real count
    db.set_setting(conn, "background_refresh", "0")  # disabled → status is "refresh off"
    off = client.get("/jobs/active").text
    assert "refreshing" not in off and "refresh off" in off


def test_settings_save_persists_background_refresh(client):
    conn = client.app_ref.state.catalog
    client.post("/settings", data={"quality_gate": "7.0"})
    assert db.get_setting(conn, "background_refresh") == "0"
    client.post("/settings", data={"quality_gate": "7.0", "background_refresh": "1"})
    assert db.get_setting(conn, "background_refresh") == "1"
    body = client.get("/settings").text
    assert 'name="background_refresh"' in body
    assert 'hx-get="/crawler"' in body  # sprint button lives in the crawler dashboard
