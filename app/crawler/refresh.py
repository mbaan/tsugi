"""Background catalog maintenance: acquire the opinionated base list, then keep
indexed works fresh. One mechanism — never-fetched base entries are simply the
stalest items there are. Politeness is inherited (shared limiter, retrying,
archive) and reinforced here (drip pacing, circuit breaker, item cooldowns)."""

import asyncio
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from app import db
from app.catalog.normalize import upsert_payload

DRIP_SECONDS = 60
COOLDOWN_SECONDS = 24 * 3600
CIRCUIT_THRESHOLD = 3
CIRCUIT_SECONDS = 3600
EXPAND_MIN_VOTES = 10  # only strongly-recommended uncataloged stubs are worth fetching

_ACQUIRE_SQL = (
    "SELECT bl.source, bl.source_key FROM base_list bl"
    " WHERE NOT EXISTS (SELECT 1 FROM work_sources ws JOIN works w ON w.id=ws.work_id"
    "  WHERE ws.source=bl.source AND ws.source_key=bl.source_key AND w.is_stub=0)"
    " ORDER BY bl.rank_kind, bl.rank"
)
_REFRESH_SQL = (
    "SELECT ws.source, ws.source_key FROM work_sources ws JOIN works w ON w.id=ws.work_id"
    " WHERE w.is_stub=0 AND (ws.last_fetched_at IS NULL OR ws.last_fetched_at < datetime('now', ?))"
    " ORDER BY ws.last_fetched_at"
)
_EXPAND_SQL = (
    "SELECT ws.source, ws.source_key, MAX(s.votes) AS v"
    " FROM works w JOIN work_sources ws ON ws.work_id=w.id"
    " JOIN similarities s ON s.source='anilist' AND (s.to_work_id=w.id OR s.from_work_id=w.id)"
    " WHERE w.is_stub=1 AND s.votes >= ?"
    " GROUP BY ws.source, ws.source_key ORDER BY v DESC"
)


@dataclass
class RefreshState:
    sprint: bool = False
    consecutive_errors: int = 0
    circuit_open_until: float = 0.0
    last_fetch_at: float = 0.0
    cooldown: dict[tuple[str, str], float] = field(default_factory=dict)


def _ttl(conn: sqlite3.Connection) -> str:
    return f"-{int(db.get_float(conn, 'ttl_days'))} days"


def needs_pull(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM base_list WHERE pulled_at >= datetime('now', ?) LIMIT 1",
        (_ttl(conn),),
    ).fetchone() is None


def backlog(conn: sqlite3.Connection) -> int:
    a = conn.execute(f"SELECT COUNT(*) c FROM ({_ACQUIRE_SQL})").fetchone()["c"]
    r = conn.execute(f"SELECT COUNT(*) c FROM ({_REFRESH_SQL})", (_ttl(conn),)).fetchone()["c"]
    return a + r


def status(conn: sqlite3.Connection, state: "RefreshState", now: float | None = None) -> dict:
    """Snapshot for the crawler dashboard: per-tier backlog + live loop state.
    Times are monotonic-relative seconds (None when never set / not paused)."""
    now = time.monotonic() if now is None else now
    base = conn.execute("SELECT COUNT(*) c, MAX(pulled_at) p FROM base_list").fetchone()
    acquire = conn.execute(f"SELECT COUNT(*) c FROM ({_ACQUIRE_SQL})").fetchone()["c"]
    refresh = conn.execute(f"SELECT COUNT(*) c FROM ({_REFRESH_SQL})", (_ttl(conn),)).fetchone()["c"]
    expand = conn.execute(f"SELECT COUNT(*) c FROM ({_EXPAND_SQL})", (EXPAND_MIN_VOTES,)).fetchone()["c"]
    circuit_secs = max(0.0, state.circuit_open_until - now) or None
    last_fetch_secs = (now - state.last_fetch_at) if state.last_fetch_at else None
    enabled = db.get_setting(conn, "background_refresh") == "1"
    crawling = conn.execute(
        "SELECT 1 FROM crawl_jobs WHERE status='running' LIMIT 1").fetchone() is not None
    return {
        "acquire": acquire,
        "refresh": refresh,
        "expand": expand,
        "sprint": state.sprint,
        "enabled": enabled,
        "circuit_secs": circuit_secs,
        "last_fetch_secs": last_fetch_secs,
        "base_count": base["c"],
        "base_pulled_at": base["p"],
        # What the crawler is doing right now, named after the work. A queued tier
        # means it's on that tier (paced/cooling, not idle); idle only when every
        # tier is empty.
        "phase": _phase(crawling, circuit_secs, enabled, state.sprint,
                        acquire, refresh, expand),
    }


def _phase(crawling, circuit_secs, enabled, sprint, acquire, refresh, expand) -> str:
    """Resolve the single status word. Tier order mirrors _pick; an on-demand depth
    crawl is the headline activity, and circuit-break / disabled outrank backlog."""
    if crawling:
        return "crawling"
    if circuit_secs:
        return "paused"
    if not enabled and not sprint:
        return "off"
    if acquire:
        return "acquiring"
    if refresh:
        return "refreshing"
    if expand and not sprint:  # sprint skips expansion, so don't claim to be expanding
        return "expanding"
    return "idle"


def _pick(conn: sqlite3.Connection, state: RefreshState, now: float, expand: bool):
    """Next (source, source_key, kind); cooled-down items are invisible."""
    tiers = [(_ACQUIRE_SQL, (), "acquired"),
             (_REFRESH_SQL, (_ttl(conn),), "refreshed")]
    if expand:  # drip only: the expansion frontier grows as it walks
        tiers.append((_EXPAND_SQL, (EXPAND_MIN_VOTES,), "expanded"))
    for sql, params, kind in tiers:
        for r in conn.execute(sql + " LIMIT 50", params):
            if state.cooldown.get((r["source"], r["source_key"]), 0.0) <= now:
                return r["source"], r["source_key"], kind
    return None, None, "idle"


def _log(conn: sqlite3.Connection, action: str, work_id: int | None = None,
         label: str | None = None) -> None:
    """Persist one crawler action so the dashboard has real history. Unbounded by
    design — sqlite handles sub-million rows fine and the dashboard reads
    ORDER BY id DESC LIMIT N (PK-ordered, O(N) regardless of table size)."""
    conn.execute("INSERT INTO refresh_log(action, work_id, label) VALUES(?,?,?)",
                 (action, work_id, label))


async def refresh_step(conn: sqlite3.Connection, sources: Mapping, state: RefreshState,
                       clock=time.monotonic, expand: bool = True) -> str:
    """One maintenance action: pull lists, acquire, refresh, expand, or idle."""
    now = clock()
    anilist = sources.get("anilist")
    if anilist is not None and needs_pull(conn):
        ranked = [(kind, await anilist.browse_top(kind))
                  for kind in ("score", "popularity")]
        with conn:
            conn.execute("DELETE FROM base_list")
            for kind, entries in ranked:
                conn.executemany(
                    "INSERT OR IGNORE INTO base_list(source, source_key, rank_kind, rank)"
                    " VALUES('anilist',?,?,?)",
                    [(key, kind, i + 1) for i, (key, _title) in enumerate(entries)])
            total = sum(len(e) for _k, e in ranked)
            _log(conn, "pulled", label=f"{total} ranked titles")
        return "pulled"

    source_name, source_key, kind = _pick(conn, state, now, expand)
    if source_name is None:
        return "idle"
    source = sources.get(source_name)
    if source is None:  # connector not configured; don't spin on it
        state.cooldown[(source_name, source_key)] = now + COOLDOWN_SECONDS
        return "idle"
    try:
        payload = await source.fetch(source_key)
    except Exception as exc:
        state.cooldown[(source_name, source_key)] = now + COOLDOWN_SECONDS
        state.consecutive_errors += 1
        if state.consecutive_errors >= CIRCUIT_THRESHOLD:
            state.circuit_open_until = now + CIRCUIT_SECONDS
            state.consecutive_errors = 0
        with conn:
            _log(conn, "error", label=f"{source_key}: {str(exc)[:140] or type(exc).__name__}")
        return "error"
    work_id = upsert_payload(conn, payload)
    title = conn.execute("SELECT canonical_title t FROM works WHERE id=?", (work_id,)).fetchone()["t"]
    with conn:
        _log(conn, kind, work_id=work_id, label=title)
    state.consecutive_errors = 0
    state.last_fetch_at = now
    return kind


async def refresh_loop(app) -> None:
    """Lifespan task: drip refresh_step while enabled; sprint runs limiter-paced."""
    conn = app.state.catalog
    state: RefreshState = app.state.refresh
    while True:
        try:
            now = time.monotonic()
            if state.circuit_open_until > now:
                state.sprint = False
                await asyncio.sleep(min(DRIP_SECONDS, state.circuit_open_until - now))
                continue
            if db.get_setting(conn, "background_refresh") != "1" and not state.sprint:
                await asyncio.sleep(DRIP_SECONDS)
                continue
            did = await refresh_step(conn, app.state.sources, state,
                                     expand=not state.sprint)
            if did == "idle":
                state.sprint = False
            if did == "idle" or not state.sprint:
                await asyncio.sleep(DRIP_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:  # never let the maintenance loop die
            await asyncio.sleep(DRIP_SECONDS)
