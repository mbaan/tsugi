import asyncio
import random
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.templating import Jinja2Templates

from app import db
from app.catalog.normalize import upsert_payload
from app.catalog.resolve import merge_works, normalize_title
from app.crawler.frontier import MIN_VOTES
from app.crawler.jobs import create_job, run_job
from app.engine.score import rating_affinity, recommend
from app.engine.suggest import suggest_seeds

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def changed(extra: str = "") -> Response:
    """Mutations don't render; regions listening for data-changed re-fetch themselves."""
    events = "data-changed" + (f", {extra}" if extra else "")
    return Response(status_code=204, headers={"HX-Trigger": events})


def ago(secs: float | None) -> str:
    """Coarse relative time for dashboard rows; None → em dash."""
    if secs is None:
        return "—"
    secs = int(secs)
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def in_(secs: float | None) -> str:
    """Coarse forward duration ('in 3d'); non-positive → 'due'."""
    if secs is None or secs <= 0:
        return "due"
    secs = int(secs)
    if secs < 3600:
        return f"in {secs // 60 or 1}m"
    if secs < 86400:
        return f"in {secs // 3600}h"
    return f"in {secs // 86400}d"


templates.env.globals.update(ago=ago, in_=in_)


def _main_context(conn) -> dict:
    seeds = []
    for r in conn.execute(
        "SELECT w.id, w.canonical_title, s.affinity, rt.overall FROM seeds s"
        " JOIN works w ON w.id=s.work_id LEFT JOIN ratings rt ON rt.work_id=s.work_id"
    ):
        seeds.append({
            "id": r["id"], "canonical_title": r["canonical_title"], "overall": r["overall"],
            "pull": rating_affinity(r["overall"]) if r["overall"] is not None else None,
        })
    tropes = conn.execute(
        "SELECT tw.tag_id, tw.mode, t.name FROM trope_weights tw JOIN tags t ON t.id=tw.tag_id"
    ).fetchall()
    return {"seeds": seeds, "tropes": tropes, "suggestions": suggest_seeds(conn)}


@router.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"view": "discover"})


@router.get("/tuning")
async def tuning(request: Request):
    conn = request.app.state.catalog
    return templates.TemplateResponse(request, "partials/_tuning.html", _main_context(conn))


@router.get("/banner")
async def banner(request: Request):
    """Randomized cover collage: own seeds+library first, random catalog fill to 24."""
    conn = request.app.state.catalog
    show_adult = db.get_setting(conn, "show_adult") == "1"
    # own seeds/library items skip the adult gate on purpose — the user added them
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM works WHERE cover_url IS NOT NULL AND ("
        " id IN (SELECT work_id FROM seeds) OR id IN (SELECT work_id FROM user_list))"
    )]
    fill = conn.execute(
        "SELECT id FROM works WHERE cover_url IS NOT NULL AND is_stub=0 AND (? OR is_adult=0)"
        " AND id NOT IN (SELECT work_id FROM seeds)"
        " AND id NOT IN (SELECT work_id FROM user_list)"
        " ORDER BY RANDOM() LIMIT ?",
        (int(show_adult), max(0, 24 - len(ids))),
    ).fetchall()
    ids += [r["id"] for r in fill]
    if ids:
        ids = (ids * (24 // len(ids) + 1))[:24]
        random.shuffle(ids)
    return templates.TemplateResponse(request, "partials/_banner.html", {"ids": ids})


@router.post("/refresh/sprint")
async def refresh_sprint(request: Request):
    request.app.state.refresh.sprint = True
    return changed("refresh-started")


@router.get("/recommendations")
async def recommendations(request: Request, sort: str = "match", type: str = "",
                    min_quality: str = "", chips: str = "1"):
    conn = request.app.state.catalog
    try:
        mq = float(min_quality) if min_quality else None
    except ValueError:
        mq = None
    results, skipped = recommend(conn, sort=sort, work_type=type or None, min_quality=mq,
                                 with_skipped=True)
    links: dict[int, list] = {}
    if results:
        ids = ",".join("?" * len(results))
        for row in conn.execute(
            f"SELECT work_id, site, url FROM work_links WHERE work_id IN ({ids})",
            [r.work_id for r in results],
        ):
            links.setdefault(row["work_id"], []).append(row)
    return templates.TemplateResponse(
        request, "partials/_grid.html",
        {"results": results, "links": links, "chips": chips != "0", "skipped": skipped,
         "gate": mq if mq is not None else db.get_float(conn, "quality_gate")}
    )


@router.get("/search")
async def search(request: Request, q: str = ""):
    conn = request.app.state.catalog
    local, remote, errors = [], [], []
    if len(q) >= 2:
        local = conn.execute(
            "SELECT DISTINCT w.id, w.canonical_title FROM works w"
            " JOIN work_titles wt ON wt.work_id=w.id"
            " WHERE wt.norm_title LIKE ? AND w.is_stub=0 LIMIT 10",
            (f"%{normalize_title(q)}%",),
        ).fetchall()
        known = {r["id"] for r in local}
        for name, source in request.app.state.sources.items():
            try:
                hits = await source.search(q)
            except Exception:
                errors.append(name)
                continue
            for h in hits:
                row = conn.execute(
                    "SELECT work_id FROM work_sources WHERE source=? AND source_key=?",
                    (h.source, h.source_key),
                ).fetchone()
                if not row or row["work_id"] not in known:
                    remote.append(h)
    return templates.TemplateResponse(
        request, "partials/_search.html",
        {"local": local, "remote": remote, "errors": errors}
    )


@router.post("/index")
async def index_title(request: Request, source: str = Form(), source_key: str = Form()):
    src = request.app.state.sources[source]
    payload = await src.fetch(source_key)
    upsert_payload(request.app.state.catalog, payload)
    return changed()


@router.post("/works/{work_id}/crawl")
async def crawl(request: Request, work_id: int, depth: int = Form(2)):
    conn = request.app.state.catalog
    job_id = create_job(conn, work_id, max_depth=min(depth, 4))
    task = asyncio.create_task(run_job(conn, request.app.state.sources, job_id))
    request.app.state.jobs[job_id] = task

    def _reap(t, jobs=request.app.state.jobs, jid=job_id):
        jobs.pop(jid, None)
        if not t.cancelled() and t.exception():
            print(f"crawl job {jid} crashed: {t.exception()!r}")

    task.add_done_callback(_reap)
    return changed("crawl-queued")


@router.get("/nav/count")
async def nav_count(request: Request):
    n = request.app.state.catalog.execute("SELECT COUNT(*) c FROM user_list").fetchone()["c"]
    return PlainTextResponse(str(n))


@router.get("/reviews/badge")
async def review_badge(request: Request):
    n = request.app.state.catalog.execute(
        "SELECT COUNT(*) c FROM match_reviews WHERE status='pending'"
    ).fetchone()["c"]
    if not n:
        return PlainTextResponse("")
    return PlainTextResponse(
        f'<button class="link review-badge" hx-get="/settings" hx-target="#overlay">'
        f"{n} to review</button>",
        media_type="text/html",
    )


@router.get("/jobs/active")
async def jobs_active(request: Request):
    """Compact nav-bar status pill — same resolved phase as the dashboard chip, so the
    two always agree."""
    from app.crawler.refresh import status as refresh_status

    conn = request.app.state.catalog
    st = refresh_status(conn, request.app.state.refresh)
    phase = st["phase"]
    job = conn.execute(
        "SELECT COALESCE(SUM(fetched),0) f, COALESCE(SUM(budget),0) b"
        " FROM crawl_jobs WHERE status='running'"
    ).fetchone()
    last = None
    if phase in ("idle", "off"):  # show the post-crawl flash only when nothing's active
        last = conn.execute(
            "SELECT fetched, errors, status FROM crawl_jobs"
            " WHERE finished_at >= datetime('now', '-15 seconds')"
            " ORDER BY id DESC LIMIT 1"
        ).fetchone()
    todo = {"acquiring": st["acquire"], "refreshing": st["refresh"],
            "expanding": st["expand"]}.get(phase, 0)
    return templates.TemplateResponse(request, "partials/_jobs_active.html",
        {"phase": phase, "fetched": job["f"], "budget": job["b"], "todo": todo, "last": last})


@router.get("/crawler")
async def crawler(request: Request):
    """Full crawler dashboard: background maintenance state + backlog by type,
    the activity stream, and depth-crawl job history."""
    from app.crawler.refresh import status as refresh_status

    conn = request.app.state.catalog
    st = refresh_status(conn, request.app.state.refresh)
    ttl_days = db.get_float(conn, "ttl_days")
    base_age = conn.execute(
        "SELECT CAST((julianday('now') - julianday(MAX(pulled_at))) * 86400 AS INTEGER) s"
        " FROM base_list"
    ).fetchone()["s"]
    st["base_age_secs"] = base_age
    st["base_next_secs"] = (ttl_days * 86400 - base_age) if base_age is not None else None
    st["indexed"] = conn.execute("SELECT COUNT(*) c FROM works WHERE is_stub=0").fetchone()["c"]
    st["stubs"] = conn.execute("SELECT COUNT(*) c FROM works WHERE is_stub=1").fetchone()["c"]
    recent = conn.execute(
        "SELECT action, label,"
        " CAST((julianday('now') - julianday(at)) * 86400 AS INTEGER) AS age"
        " FROM refresh_log ORDER BY id DESC LIMIT 30"
    ).fetchall()
    st["log_total"] = conn.execute("SELECT COUNT(*) c FROM refresh_log").fetchone()["c"]
    jobs = conn.execute(
        "SELECT j.*, w.canonical_title AS title,"
        " CAST((julianday(COALESCE(j.finished_at,'now')) - julianday(j.started_at)) * 86400"
        "  AS INTEGER) AS dur,"
        " (SELECT COUNT(*) FROM crawl_queue q WHERE q.job_id=j.id AND q.state='pending') AS todo"
        " FROM crawl_jobs j JOIN works w ON w.id=j.seed_work_id ORDER BY j.id DESC LIMIT 12"
    ).fetchall()
    return templates.TemplateResponse(request, "partials/_crawler.html",
        {"s": st, "recent": recent, "jobs": jobs})


@router.post("/seeds/{work_id}")
async def toggle_seed(request: Request, work_id: int):
    conn = request.app.state.catalog
    if conn.execute("SELECT 1 FROM seeds WHERE work_id=?", (work_id,)).fetchone():
        conn.execute("DELETE FROM seeds WHERE work_id=?", (work_id,))
    else:
        conn.execute("INSERT INTO seeds(work_id, affinity) VALUES(?, 1.0)", (work_id,))
    conn.commit()
    return changed()


@router.post("/ratings/{work_id}")
async def set_rating(request: Request, work_id: int,
               field: Annotated[str, Form()], value: Annotated[int, Form()]):
    conn = request.app.state.catalog
    if field not in ("overall", "art", "story"):
        raise HTTPException(status_code=422)
    if not conn.execute("SELECT 1 FROM works WHERE id=? AND is_stub=0", (work_id,)).fetchone():
        raise HTTPException(status_code=404)
    value = max(0, min(10, value))
    if field == "overall":
        if value == 0:
            conn.execute("DELETE FROM ratings WHERE work_id=?", (work_id,))
        else:
            conn.execute(
                "INSERT INTO ratings(work_id, overall) VALUES(?,?)"
                " ON CONFLICT(work_id) DO UPDATE SET overall=excluded.overall,"
                " rated_at=datetime('now')",
                (work_id, value),
            )
    else:
        if not conn.execute("SELECT 1 FROM ratings WHERE work_id=?", (work_id,)).fetchone():
            raise HTTPException(status_code=422)  # sub-scores need an overall first
        conn.execute(
            f"UPDATE ratings SET {field}=?, rated_at=datetime('now') WHERE work_id=?",
            (value or None, work_id),
        )
    events = ""
    if field == "overall" and value:
        row = conn.execute("SELECT status FROM user_list WHERE work_id=?", (work_id,)).fetchone()
        if row is None or row["status"] == "want":  # rating implies read; discards stay
            conn.execute(
                "INSERT INTO user_list(work_id, status) VALUES(?, 'read')"
                " ON CONFLICT(work_id) DO UPDATE SET status='read', updated_at=datetime('now')",
                (work_id,),
            )
            events = "marked-read"
    conn.commit()
    return changed(events)


CYCLE = {None: "require", "require": "boost", "boost": "exclude", "exclude": None}


@router.get("/tropes/picker")
async def trope_picker(request: Request, q: str = ""):
    conn = request.app.state.catalog
    show_adult = db.get_setting(conn, "show_adult") == "1"
    rows = conn.execute(
        "SELECT t.id, t.name, t.category, t.description, tw.mode FROM tags t"
        " LEFT JOIN trope_weights tw ON tw.tag_id=t.id"
        " WHERE t.kind IN ('trope','genre') AND (? OR t.is_adult=0)"
        " AND (?='' OR t.name LIKE ?)"
        " ORDER BY CASE WHEN t.category IS NULL THEN 1 ELSE 0 END, t.category, t.name",
        (int(show_adult), q, f"%{q}%"),
    ).fetchall()
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["category"] or "Other", []).append(r)
    return templates.TemplateResponse(request, "partials/_picker.html", {"groups": groups})


@router.post("/tropes/{tag_id}")
async def cycle_trope(request: Request, tag_id: int):
    conn = request.app.state.catalog
    row = conn.execute("SELECT mode FROM trope_weights WHERE tag_id=?", (tag_id,)).fetchone()
    nxt = CYCLE[row["mode"] if row else None]
    if nxt is None:
        conn.execute("DELETE FROM trope_weights WHERE tag_id=?", (tag_id,))
    else:
        conn.execute(
            "INSERT INTO trope_weights(tag_id, mode, weight) VALUES(?,?,1.0)"
            " ON CONFLICT(tag_id) DO UPDATE SET mode=excluded.mode",
            (tag_id, nxt),
        )
    conn.commit()
    return changed()


LIB_SORTS = {"added": "ul.created_at DESC", "title": "w.canonical_title COLLATE NOCASE",
             "quality": "w.quality DESC"}


@router.get("/library")
async def library(request: Request, status: str = "read"):
    if status not in ("want", "read", "discarded"):
        status = "read"
    conn = request.app.state.catalog
    counts = {r["status"]: r["c"] for r in conn.execute(
        "SELECT status, COUNT(*) c FROM user_list GROUP BY status")}
    return templates.TemplateResponse(request, "library.html",
        {"status": status, "counts": counts, "view": "library"})


@router.get("/library/grid")
async def library_grid(request: Request, status: str = "read", sort: str = "added", q: str = ""):
    if status not in ("want", "read", "discarded"):
        status = "read"
    conn = request.app.state.catalog
    order = LIB_SORTS.get(sort, LIB_SORTS["added"])
    rows = conn.execute(
        f"SELECT w.*, ul.status AS list_status, ul.note, rt.overall AS rating_overall,"
        f" rt.art AS rating_art, rt.story AS rating_story,"
        f" EXISTS(SELECT 1 FROM seeds s WHERE s.work_id=w.id) AS seeded"
        f" FROM user_list ul JOIN works w ON w.id=ul.work_id"
        f" LEFT JOIN ratings rt ON rt.work_id=ul.work_id WHERE ul.status=?"
        f" AND (?='' OR w.canonical_title LIKE ?) ORDER BY {order}",  # noqa: S608 — whitelisted
        (status, q, f"%{q}%"),
    ).fetchall()
    reasons: dict[int, list[str]] = {}
    if status == "discarded" and rows:
        ids = ",".join("?" * len(rows))
        for r in conn.execute(
            f"SELECT df.work_id, t.name FROM discard_feedback df JOIN tags t ON t.id=df.tag_id"
            f" WHERE df.work_id IN ({ids})", [w["id"] for w in rows]):
            reasons.setdefault(r["work_id"], []).append(r["name"])
    return templates.TemplateResponse(request, "partials/_cards.html",
        {"rows": rows, "reasons": reasons, "list_status": status})


@router.get("/works/{work_id}")
async def work_detail(request: Request, work_id: int):
    conn = request.app.state.catalog
    work = conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
    if work is None:
        raise HTTPException(status_code=404)
    alt_titles = [r["title"] for r in conn.execute(
        "SELECT DISTINCT title FROM work_titles WHERE work_id=? AND title<>? LIMIT 4",
        (work_id, work["canonical_title"]))]
    links = conn.execute("SELECT site, url FROM work_links WHERE work_id=?", (work_id,)).fetchall()
    tags = conn.execute(
        "SELECT t.id, t.name, AVG(wt.weight) AS weight, tw.mode FROM work_tags wt"
        " JOIN tags t ON t.id=wt.tag_id LEFT JOIN trope_weights tw ON tw.tag_id=t.id"
        " WHERE wt.work_id=? GROUP BY t.id ORDER BY weight DESC LIMIT 20",
        (work_id,)).fetchall()
    # the strip applies the crawler's per-source noise floor: edges it would
    # never crawl shouldn't be offered for indexing either
    noise = MIN_VOTES.get("anilist", 0)
    similar = conn.execute(
        "SELECT s2.*, ws.source, ws.source_key FROM ("
        " SELECT id, canonical_title, is_stub, cover_color, MAX(votes) AS votes FROM ("
        "  SELECT w2.id, w2.canonical_title, w2.is_stub, w2.cover_color, s.votes"
        "  FROM similarities s JOIN works w2 ON w2.id=s.to_work_id WHERE s.from_work_id=?"
        "  AND (s.source <> 'anilist' OR s.votes >= ?)"
        "  UNION ALL"
        "  SELECT w2.id, w2.canonical_title, w2.is_stub, w2.cover_color, s.votes"
        "  FROM similarities s JOIN works w2 ON w2.id=s.from_work_id WHERE s.to_work_id=?"
        "  AND (s.source <> 'anilist' OR s.votes >= ?))"
        " GROUP BY id) s2"
        " LEFT JOIN work_sources ws ON ws.work_id=s2.id AND ws.rowid="
        "  (SELECT MIN(rowid) FROM work_sources WHERE work_id=s2.id)"
        " ORDER BY s2.votes DESC LIMIT 8",
        (work_id, noise, work_id, noise)).fetchall()
    relations = conn.execute(
        "SELECT w2.id, w2.canonical_title, w2.is_stub, r.rel_type, ws.source, ws.source_key"
        " FROM work_relations r JOIN works w2 ON w2.id=r.related_work_id"
        " LEFT JOIN work_sources ws ON ws.work_id=w2.id AND ws.rowid="
        "  (SELECT MIN(rowid) FROM work_sources WHERE work_id=w2.id)"
        " WHERE r.work_id=? LIMIT 10",
        (work_id,)).fetchall()
    context = {
        "work": work, "alt_titles": alt_titles, "links": links, "tags": tags,
        "similar": similar, "relations": relations,
        "is_seed": bool(conn.execute("SELECT 1 FROM seeds WHERE work_id=?", (work_id,)).fetchone()),
        "rating": conn.execute("SELECT * FROM ratings WHERE work_id=?", (work_id,)).fetchone(),
        "list_status": (row["status"] if (row := conn.execute(
            "SELECT status FROM user_list WHERE work_id=?", (work_id,)).fetchone()) else None),
        "view": "",
    }
    template = "partials/_work.html" if request.headers.get("HX-Request") else "work.html"
    return templates.TemplateResponse(request, template, context)


@router.get("/works/{work_id}/discard")
async def discard_modal(request: Request, work_id: int):
    conn = request.app.state.catalog
    work = conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
    tags = conn.execute(
        "SELECT DISTINCT t.id, t.name FROM work_tags wt JOIN tags t ON t.id=wt.tag_id"
        " WHERE wt.work_id=? ORDER BY wt.weight DESC LIMIT 15",
        (work_id,),
    ).fetchall()
    return templates.TemplateResponse(
        request, "partials/_discard.html", {"work": work, "tags": tags}
    )


@router.post("/list/{work_id}")
async def set_list(request: Request, work_id: int, status: str = "",
                   tag_ids: Annotated[list[int], Form()] = [],
                   note: Annotated[str, Form()] = ""):
    conn = request.app.state.catalog
    if not status:  # library cards post it as a form field instead
        form = await request.form()
        status = str(form.get("status", ""))
    if status == "restore":
        conn.execute("DELETE FROM user_list WHERE work_id=?", (work_id,))
        conn.execute("DELETE FROM discard_feedback WHERE work_id=?", (work_id,))
        conn.commit()
        return changed()
    if status not in ("want", "read", "discarded"):
        return changed()
    conn.execute(
        "INSERT INTO user_list(work_id, status, note) VALUES(?,?,?)"
        " ON CONFLICT(work_id) DO UPDATE SET status=excluded.status, note=excluded.note,"
        " updated_at=datetime('now')",
        (work_id, status, note or None),
    )
    if status == "discarded":
        conn.execute("DELETE FROM discard_feedback WHERE work_id=?", (work_id,))
        for tag_id in tag_ids:
            conn.execute(
                "INSERT INTO discard_feedback(work_id, tag_id, note) VALUES(?,?,?)",
                (work_id, tag_id, note or None),
            )
    conn.commit()
    return changed("close-overlay")


@router.get("/reviews")
async def reviews(request: Request):
    conn = request.app.state.catalog
    rows = conn.execute(
        "SELECT mr.id, mr.reason, wn.canonical_title AS new_title,"
        " wc.canonical_title AS cand_title FROM match_reviews mr"
        " JOIN works wn ON wn.id=mr.work_id JOIN works wc ON wc.id=mr.candidate_work_id"
        " WHERE mr.status='pending'"
    ).fetchall()
    return templates.TemplateResponse(request, "partials/_reviews.html", {"reviews": rows})


@router.post("/reviews/{review_id}")
async def resolve_review(request: Request, review_id: int,
                   action: Annotated[str, Form()] = "keep"):
    conn = request.app.state.catalog
    r = conn.execute("SELECT * FROM match_reviews WHERE id=?", (review_id,)).fetchone()
    if r is None:  # stale tab or cascade-deleted mirror review — nothing to do
        return changed()
    if action == "merge":
        merge_works(conn, r["candidate_work_id"], r["work_id"])
    conn.execute(
        "UPDATE match_reviews SET status=? WHERE id=?",
        ("resolved" if action == "merge" else "dismissed", review_id),
    )
    conn.commit()
    return changed()


@router.get("/settings")
async def settings_form(request: Request):
    from app.web.auth import current_user

    conn = request.app.state.catalog
    s = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    return templates.TemplateResponse(
        request, "partials/_settings.html", {"s": s, "user": current_user(request)})


@router.post("/settings")
async def settings_save(request: Request):
    conn = request.app.state.catalog
    form = await request.form()
    for key in ("quality_gate", "w_similarity", "w_tropes", "w_quality"):
        if form.get(key):
            try:  # a stored non-float would 500 every render via db.get_float
                db.set_setting(conn, key, str(float(form[key])))
            except (TypeError, ValueError):
                pass  # ignore malformed input, keep prior value
    for key in ("show_adult", "exclude_seed_franchise", "background_refresh"):
        db.set_setting(conn, key, "1" if form.get(key) == "1" else "0")
    return changed()
