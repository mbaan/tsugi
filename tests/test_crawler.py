from app.catalog.normalize import upsert_payload
from app.crawler.jobs import create_job, run_job
from app.sources.dto import RelationRef, SimilarRef
from tests.factory import FakeSource, make_payload


def sim(key, title, votes=100):
    return SimilarRef(source="fake", source_key=key, title=title, votes=votes)


def chain_payloads():
    """s -> a -> b -> c -> d, all good quality."""
    return {
        "s": make_payload(source="fake", source_key="s", title="Seed", similar=(sim("a", "A"),)),
        "a": make_payload(source="fake", source_key="a", title="A", similar=(sim("b", "B"),)),
        "b": make_payload(source="fake", source_key="b", title="B", similar=(sim("c", "C"),)),
        "c": make_payload(source="fake", source_key="c", title="C", similar=(sim("d", "D"),)),
        "d": make_payload(source="fake", source_key="d", title="D"),
    }


async def test_bfs_depth_semantics_match_project_md(catalog):
    src = FakeSource(chain_payloads())
    seed_id = upsert_payload(catalog, src.payloads["s"])
    job_id = create_job(catalog, seed_id, max_depth=3)
    await run_job(catalog, {"fake": src}, job_id)
    # depth 1..3 fetched; depth-3 node C is fetched but NOT expanded -> d never queued
    assert src.fetch_calls == ["a", "b", "c"]
    queued = {r["source_key"] for r in catalog.execute("SELECT source_key FROM crawl_queue")}
    assert "d" not in queued
    job = catalog.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "done"
    assert job["fetched"] == 3


async def test_budget_truncates(catalog):
    src = FakeSource(chain_payloads())
    seed_id = upsert_payload(catalog, src.payloads["s"])
    job_id = create_job(catalog, seed_id, max_depth=3, budget=1)
    await run_job(catalog, {"fake": src}, job_id)
    assert src.fetch_calls == ["a"]
    job = catalog.execute("SELECT status FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "truncated"


async def test_fresh_works_not_refetched(catalog):
    src = FakeSource(chain_payloads())
    seed_id = upsert_payload(catalog, src.payloads["s"])
    upsert_payload(catalog, src.payloads["a"])  # a already fully fetched and fresh
    job_id = create_job(catalog, seed_id, max_depth=3)
    await run_job(catalog, {"fake": src}, job_id)
    assert src.fetch_calls == ["b", "c"]  # a skipped, its stored edges still expanded


async def test_trash_indexed_but_not_expanded_through(catalog):
    payloads = chain_payloads()
    payloads["a"] = make_payload(source="fake", source_key="a", title="A",
                                 score=5.0, similar=(sim("b", "B"),))
    src = FakeSource(payloads)
    seed_id = upsert_payload(catalog, payloads["s"])
    job_id = create_job(catalog, seed_id, max_depth=3)
    await run_job(catalog, {"fake": src}, job_id)
    assert src.fetch_calls == ["a"]  # a indexed; its edges not followed


async def test_franchise_members_not_in_frontier(catalog):
    rel = RelationRef(source="fake", source_key="f", title="Seed: Side Story", rel_type="sequel")
    payloads = {
        "s": make_payload(source="fake", source_key="s", title="Seed",
                          similar=(sim("f", "Seed: Side Story"),), relations=(rel,)),
    }
    src = FakeSource(payloads)
    seed_id = upsert_payload(catalog, payloads["s"])
    job_id = create_job(catalog, seed_id, max_depth=2)
    await run_job(catalog, {"fake": src}, job_id)
    assert src.fetch_calls == []  # only edge is same-franchise


async def test_fetch_error_recorded_job_continues(catalog):
    payloads = chain_payloads()
    payloads["s"] = make_payload(source="fake", source_key="s", title="Seed",
                                 similar=(sim("a", "A"), sim("x", "X")))
    payloads["x"] = RuntimeError("boom")
    src = FakeSource(payloads)
    seed_id = upsert_payload(catalog, payloads["s"])
    job_id = create_job(catalog, seed_id, max_depth=1)
    await run_job(catalog, {"fake": src}, job_id)
    job = catalog.execute("SELECT * FROM crawl_jobs WHERE id=?", (job_id,)).fetchone()
    assert job["errors"] == 1
    assert job["status"] == "done"
    assert "a" in src.fetch_calls
