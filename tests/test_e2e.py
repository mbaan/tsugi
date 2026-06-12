"""Full user journey against fake sources: index -> crawl -> weight -> recommend."""

import time

from app.sources.dto import SimilarRef
from tests.factory import make_payload


def sim(key, title, votes):
    return SimilarRef(source="fake", source_key=key, title=title, votes=votes)


def test_full_journey(client):
    src = client.app_ref.state.sources["fake"]
    src.payloads.update({
        "solo": make_payload(source="fake", source_key="solo", title="Solo Leveling",
                             similar=(sim("orv", "Omniscient Reader", 1367),
                                      sim("weak", "Weak Story", 40))),
        "orv": make_payload(source="fake", source_key="orv", title="Omniscient Reader",
                            score=8.6),
        "weak": make_payload(source="fake", source_key="weak", title="Weak Story", score=5.5),
    })
    conn = client.app_ref.state.catalog

    # 1. index the seed and add it as a seed
    client.post("/index", data={"source": "fake", "source_key": "solo"})
    solo = conn.execute("SELECT work_id FROM work_sources WHERE source_key='solo'").fetchone()[0]
    client.post(f"/seeds/{solo}")

    # 2. crawl depth 2
    client.post(f"/works/{solo}/crawl", data={"depth": "2"})
    job_id = conn.execute("SELECT id FROM crawl_jobs ORDER BY id DESC").fetchone()[0]
    for _ in range(60):
        if conn.execute("SELECT status FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()[0] != "running":
            break
        time.sleep(0.05)

    # 3. recommendations: good similar title present with receipts, trash gated out
    page = client.get("/recommendations").text
    assert "Omniscient Reader" in page
    assert "1,367 votes" in page
    assert "Weak Story" not in page

    # 4. discarding removes it from future grids
    orv = conn.execute("SELECT work_id FROM work_sources WHERE source_key='orv'").fetchone()[0]
    client.post(f"/list/{orv}?status=discarded", data={})
    assert "Omniscient Reader" not in client.get("/recommendations").text

    # 5. library reflects the discard with restore available
    body = client.get("/library/grid", params={"status": "discarded"}).text
    assert "Omniscient Reader" in body and "Restore" in body

    # 6. restoring brings it back to recommendations
    client.post(f"/list/{orv}?status=restore")
    assert "Omniscient Reader" in client.get("/recommendations").text

    # 7. title modal renders with tuning chips
    assert "Omniscient Reader" in client.get(
        f"/works/{orv}", headers={"HX-Request": "true"}).text


def test_ratings_journey(client):
    """Rate → suggested → one-click seed → recommendations shift; 1★ → anti-seed."""
    from tests.factory import link_similar, make_work
    conn = client.app_ref.state.catalog
    loved = make_work(conn, "Loved Read", quality=8.5)
    hated = make_work(conn, "Hated Read", quality=8.0)
    via_loved = make_work(conn, "Pulled In", quality=8.2)
    via_hated = make_work(conn, "Pushed Out", quality=8.2)
    link_similar(conn, loved, via_loved, 800)
    link_similar(conn, hated, via_hated, 800)

    # rating auto-marks read and surfaces both as suggestions
    client.post(f"/ratings/{loved}", data={"field": "overall", "value": 10})
    client.post(f"/ratings/{hated}", data={"field": "overall", "value": 2})
    tuning = client.get("/tuning").text
    assert "Loved Read" in tuning and "Hated Read" in tuning
    assert client.get("/library/grid", params={"status": "read"}).text.count("wcard") > 0

    # one-click both; rating decides direction at scoring time
    client.post(f"/seeds/{loved}")
    client.post(f"/seeds/{hated}")
    page = client.get("/recommendations").text
    assert "Pulled In" in page
    assert "Pushed Out" not in page  # anti-seed pushes its neighborhood out

    # seeded works leave the suggestions strip
    tuning = client.get("/tuning").text
    assert 'class="chip pick' not in tuning
