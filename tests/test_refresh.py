import asyncio

from app.crawler.refresh import RefreshState, backlog, refresh_step, status
from tests.factory import link_similar, link_source, make_payload, make_work


class FakeRefreshSource:
    """fetch() like factory.FakeSource, plus ranked browse_top feeds."""

    name = "anilist"

    def __init__(self, payloads=None, tops=None):
        self.payloads = payloads or {}
        self.tops = tops or {"score": [], "popularity": []}
        self.fetch_calls: list[str] = []
        self.browse_calls: list[str] = []

    async def browse_top(self, kind, pages=10, per_page=50):
        self.browse_calls.append(kind)
        return self.tops[kind]

    async def fetch(self, key):
        self.fetch_calls.append(key)
        value = self.payloads[key]
        if isinstance(value, Exception):
            raise value
        return value


def step(conn, src, state=None, clock=lambda: 1000.0):
    state = state or RefreshState()
    return asyncio.run(refresh_step(conn, {"anilist": src}, state, clock=clock)), state


def test_empty_base_list_triggers_pull(catalog):
    src = FakeRefreshSource(tops={"score": [("11", "A")], "popularity": [("22", "B"), ("11", "A")]})
    did, _ = step(catalog, src)
    assert did == "pulled"
    assert src.browse_calls == ["score", "popularity"]
    rows = catalog.execute(
        "SELECT source_key, rank_kind, rank FROM base_list ORDER BY rank_kind, rank").fetchall()
    assert [(r["source_key"], r["rank_kind"], r["rank"]) for r in rows] == [
        ("22", "popularity", 1), ("11", "popularity", 2), ("11", "score", 1)]


def test_acquires_base_entries_by_rank_before_refreshing(catalog):
    stale = make_work(catalog, "Stale")
    link_source(catalog, stale, "anilist", "99", fetched=True)
    catalog.execute("UPDATE work_sources SET last_fetched_at=datetime('now','-30 days')")
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','11','score',1), ('anilist','12','score',2)")
    catalog.commit()
    src = FakeRefreshSource(payloads={
        "11": make_payload(source="anilist", source_key="11", title="First"),
        "12": make_payload(source="anilist", source_key="12", title="Second")})
    did, _ = step(catalog, src)
    assert did == "acquired" and src.fetch_calls == ["11"]
    did, _ = step(catalog, src)
    assert did == "acquired" and src.fetch_calls == ["11", "12"]


def test_refreshes_stalest_then_goes_idle(catalog):
    older = make_work(catalog, "Older")
    newer = make_work(catalog, "Newer")
    fresh = make_work(catalog, "Fresh")
    link_source(catalog, older, "anilist", "1", fetched=True)
    link_source(catalog, newer, "anilist", "2", fetched=True)
    link_source(catalog, fresh, "anilist", "3", fetched=True)
    catalog.execute("UPDATE work_sources SET last_fetched_at=datetime('now','-40 days')"
                    " WHERE source_key='1'")
    catalog.execute("UPDATE work_sources SET last_fetched_at=datetime('now','-20 days')"
                    " WHERE source_key='2'")
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','1','score',1)")  # non-empty fresh list: no pull
    catalog.commit()
    src = FakeRefreshSource(payloads={
        "1": make_payload(source="anilist", source_key="1", title="Older"),
        "2": make_payload(source="anilist", source_key="2", title="Newer")})
    assert step(catalog, src)[0] == "refreshed"
    assert src.fetch_calls == ["1"]
    assert step(catalog, src)[0] == "refreshed"
    assert src.fetch_calls == ["1", "2"]
    assert step(catalog, src)[0] == "idle"  # '3' is fresh; base entry '1' has a fresh row now


def test_plain_stubs_are_never_picked(catalog):
    stub = make_work(catalog, "Stub", is_stub=1)
    link_source(catalog, stub, "anilist", "7", fetched=False)
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','x','score',1)")
    catalog.commit()
    src = FakeRefreshSource(payloads={"x": make_payload(source="anilist", source_key="x")})
    step(catalog, src)            # acquires 'x'
    assert step(catalog, src)[0] == "idle"
    assert "7" not in src.fetch_calls


def test_expands_high_voted_stubs_after_priorities(catalog):
    indexed = make_work(catalog, "Indexed")
    strong = make_work(catalog, "StrongStub", is_stub=1)
    weak = make_work(catalog, "WeakStub", is_stub=1)
    link_source(catalog, strong, "anilist", "70", fetched=False)
    link_source(catalog, weak, "anilist", "71", fetched=False)
    link_similar(catalog, indexed, strong, 50)
    link_similar(catalog, indexed, weak, 3)   # below EXPAND_MIN_VOTES → never fetched
    link_source(catalog, indexed, "anilist", "1", fetched=True)
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','1','score',1)")  # fresh list, no acquisitions
    catalog.commit()
    src = FakeRefreshSource(payloads={
        "70": make_payload(source="anilist", source_key="70", title="StrongStub")})
    assert step(catalog, src)[0] == "expanded"
    assert src.fetch_calls == ["70"]
    assert step(catalog, src)[0] == "idle"   # weak stub stays a stub
    assert "71" not in src.fetch_calls


def test_sprint_mode_skips_expansion(catalog):
    indexed = make_work(catalog, "Indexed")
    strong = make_work(catalog, "StrongStub", is_stub=1)
    link_source(catalog, strong, "anilist", "70", fetched=False)
    link_source(catalog, indexed, "anilist", "1", fetched=True)
    link_similar(catalog, indexed, strong, 50)
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','1','score',1)")
    catalog.commit()
    src = FakeRefreshSource()
    state = RefreshState(sprint=True)
    did = asyncio.run(refresh_step(catalog, {"anilist": src}, state,
                                   clock=lambda: 1000.0, expand=False))
    assert did == "idle" and src.fetch_calls == []


def _log_rows(conn):
    return [(r["action"], r["label"]) for r in
            conn.execute("SELECT action, label FROM refresh_log ORDER BY id")]


def test_step_logs_each_action(catalog):
    # acquire
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','11','score',1)")
    catalog.commit()
    src = FakeRefreshSource(payloads={
        "11": make_payload(source="anilist", source_key="11", title="Acquired One")})
    step(catalog, src)
    assert _log_rows(catalog) == [("acquired", "Acquired One")]


def test_step_logs_pull(catalog):
    src = FakeRefreshSource(tops={"score": [("1", "A"), ("2", "B")], "popularity": [("3", "C")]})
    step(catalog, src)  # empty base_list → pull
    assert _log_rows(catalog) == [("pulled", "3 ranked titles")]


def test_step_logs_error_with_key_and_message(catalog):
    w = make_work(catalog, "Doomed")  # non-stub, already in catalog
    link_source(catalog, w, "anilist", "9", fetched=True)
    catalog.execute("UPDATE work_sources SET last_fetched_at=datetime('now','-30 days')")
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','9','score',1)")  # already satisfied → no acquire, no pull
    catalog.commit()
    src = FakeRefreshSource(payloads={"9": RuntimeError("boom")})
    assert step(catalog, src)[0] == "error"
    rows = _log_rows(catalog)
    assert rows[-1][0] == "error" and "9: boom" in rows[-1][1]


def test_idle_logs_nothing(catalog):
    fresh = make_work(catalog, "Fresh")
    link_source(catalog, fresh, "anilist", "1", fetched=True)  # fresh, not stale
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','1','score',1)")  # satisfied by the fresh work
    stub = make_work(catalog, "OnlyStub", is_stub=1)  # plain stub, no edge → never picked
    link_source(catalog, stub, "anilist", "70", fetched=False)
    catalog.commit()
    src = FakeRefreshSource()
    assert step(catalog, src)[0] == "idle"
    assert _log_rows(catalog) == []


def test_status_breaks_backlog_into_tiers_and_loop_state(catalog):
    stale = make_work(catalog, "Stale")
    link_source(catalog, stale, "anilist", "9", fetched=True)
    catalog.execute("UPDATE work_sources SET last_fetched_at=datetime('now','-30 days')")
    stub = make_work(catalog, "Stub", is_stub=1)
    link_source(catalog, stub, "anilist", "70", fetched=False)
    link_similar(catalog, stale, stub, 50)  # high-voted uncatalogued → expansion candidate
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','11','score',1)")  # one base acquisition pending
    catalog.commit()
    state = RefreshState(sprint=True, last_fetch_at=900.0, circuit_open_until=1300.0)
    s = status(catalog, state, now=1000.0)
    assert s["acquire"] == 1 and s["refresh"] == 1 and s["expand"] == 1
    assert s["sprint"] is True and s["enabled"] is True
    assert s["last_fetch_secs"] == 100.0 and s["circuit_secs"] == 300.0
    assert s["base_count"] == 1 and s["base_pulled_at"]
    idle = status(catalog, RefreshState(), now=1000.0)
    assert idle["last_fetch_secs"] is None and idle["circuit_secs"] is None


def test_status_phase_names_the_tier_being_worked(catalog):
    # phase reports the concrete background activity, in _pick's priority order, so
    # the dashboard names the tier it's working.
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','11','score',1)")  # an acquisition is pending
    catalog.commit()
    assert status(catalog, RefreshState(), now=1000.0)["phase"] == "acquiring"

    catalog.execute("DELETE FROM base_list")  # acquisitions cleared → stale refresh wins
    stale = make_work(catalog, "Stale")
    link_source(catalog, stale, "anilist", "9", fetched=True)
    catalog.execute("UPDATE work_sources SET last_fetched_at=datetime('now','-30 days')")
    catalog.commit()
    assert status(catalog, RefreshState(), now=1000.0)["phase"] == "refreshing"


def test_status_phase_is_expanding_when_only_stubs_remain(catalog):
    seed = make_work(catalog, "Seed")
    link_source(catalog, seed, "anilist", "1", fetched=True)  # fresh: not stale
    stub = make_work(catalog, "Stub", is_stub=1)
    link_source(catalog, stub, "anilist", "70", fetched=False)
    link_similar(catalog, seed, stub, 50)  # high-voted uncatalogued → expansion candidate
    catalog.commit()
    s = status(catalog, RefreshState(), now=1000.0)
    assert s["acquire"] == 0 and s["refresh"] == 0 and s["expand"] == 1
    assert s["phase"] == "expanding"


def test_status_phase_is_idle_only_when_every_tier_is_empty(catalog):
    s = status(catalog, RefreshState(), now=1000.0)
    assert s["acquire"] == 0 and s["refresh"] == 0 and s["expand"] == 0
    assert s["phase"] == "idle"


def test_status_phase_crawling_overrides_background_work(catalog):
    seed = make_work(catalog, "Seed")
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','11','score',1)")  # background acquisition pending
    catalog.execute("INSERT INTO crawl_jobs(seed_work_id, max_depth, budget)"
                    " VALUES(?, 2, 300)", (seed,))  # on-demand depth crawl running
    catalog.commit()
    assert status(catalog, RefreshState(), now=1000.0)["phase"] == "crawling"


def test_status_phase_paused_and_off_take_precedence_over_a_backlog(catalog):
    from app import db
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','11','score',1)")  # backlog present
    catalog.commit()
    paused = status(catalog, RefreshState(circuit_open_until=1300.0), now=1000.0)
    assert paused["phase"] == "paused"
    db.set_setting(catalog, "background_refresh", "0")
    assert status(catalog, RefreshState(), now=1000.0)["phase"] == "off"


def test_backlog_counts_acquisitions_and_stale(catalog):
    w = make_work(catalog, "Stale")
    link_source(catalog, w, "anilist", "9", fetched=True)
    catalog.execute("UPDATE work_sources SET last_fetched_at=datetime('now','-30 days')")
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','11','score',1)")
    catalog.commit()
    assert backlog(catalog) == 2


def test_error_cooldown_skips_item_and_circuit_opens(catalog):
    for key in ("1", "2"):
        w = make_work(catalog, f"W{key}")
        link_source(catalog, w, "anilist", key, fetched=True)
    catalog.execute("UPDATE work_sources SET last_fetched_at=datetime('now','-30 days')")
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','1','score',1)")  # fresh list: no pull step
    catalog.commit()
    src = FakeRefreshSource(payloads={"1": RuntimeError("boom"), "2": RuntimeError("boom"),
                                      "x": RuntimeError("boom")})
    state = RefreshState()
    assert step(catalog, src, state)[0] == "error"   # '1' fails → cooled
    assert state.consecutive_errors == 1
    assert step(catalog, src, state)[0] == "error"   # '2' fails (1 is cooled, skipped)
    assert src.fetch_calls == ["1", "2"]
    assert step(catalog, src, state)[0] == "idle"    # everything cooled → idle, no fetch
    assert src.fetch_calls == ["1", "2"]


def test_third_consecutive_error_opens_circuit(catalog):
    for key in ("1", "2", "3"):
        w = make_work(catalog, f"W{key}")
        link_source(catalog, w, "anilist", key, fetched=True)
    catalog.execute("UPDATE work_sources SET last_fetched_at=datetime('now','-30 days')")
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','1','score',1)")
    catalog.commit()
    src = FakeRefreshSource(payloads={k: RuntimeError("boom") for k in ("1", "2", "3")})
    state = RefreshState()
    for _ in range(3):
        step(catalog, src, state)
    assert state.circuit_open_until == 1000.0 + 3600
    assert state.consecutive_errors == 0


def test_success_resets_error_counter(catalog):
    for key in ("1", "2"):
        w = make_work(catalog, f"W{key}")
        link_source(catalog, w, "anilist", key, fetched=True)
    catalog.execute("UPDATE work_sources SET last_fetched_at=datetime('now','-30 days')")
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','1','score',1)")
    catalog.commit()
    src = FakeRefreshSource(payloads={
        "1": RuntimeError("boom"),
        "2": make_payload(source="anilist", source_key="2", title="Fine")})
    state = RefreshState()
    assert step(catalog, src, state)[0] == "error"
    assert step(catalog, src, state)[0] == "refreshed"
    assert state.consecutive_errors == 0


def test_never_fetched_rows_refresh_before_old_ones(catalog):
    never = make_work(catalog, "Never")
    old = make_work(catalog, "Old")
    link_source(catalog, never, "anilist", "1", fetched=False)  # last_fetched_at NULL
    link_source(catalog, old, "anilist", "2", fetched=True)
    catalog.execute("UPDATE work_sources SET last_fetched_at=datetime('now','-40 days')"
                    " WHERE source_key='2'")
    catalog.execute("INSERT INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist','1','score',1)")
    catalog.commit()
    src = FakeRefreshSource(payloads={
        "1": make_payload(source="anilist", source_key="1", title="Never"),
        "2": make_payload(source="anilist", source_key="2", title="Old")})
    step(catalog, src)  # acquire tier may claim '1' first — that's fine either way:
    assert src.fetch_calls[0] == "1"  # the NULL row is always served first
