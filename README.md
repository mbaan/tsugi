# Tsugi

Tsugi — a personal recommendation engine for manga / manhwa / manhua. Indexes works from
AniList and MangaUpdates, grades them with crowd-voted tag weights and
recommendation votes, and serves explainable, quality-gated recommendations on
a single-page dashboard.

## Run

    uv sync
    uv run python -m app.main

Open http://localhost:9000 — search a title, index it, ★ it as a seed, crawl
(depth 2–3 from its title view), then shape Discover with trope chips
(✓ require / ↑ boost / ✕ exclude) and Want / Read / Not for me. Your lists
live under Library; theme (light/dark/system) and scoring weights under ⚙.
The trope dictionary downloads from AniList on first networked launch
(rerun manually: `uv run python -m app.catalog.trope_dict`).

Run from the project root: the data directory (`data/`, both SQLite files) is
created relative to the working directory unless `TSUGI_DATA` is set.

During development, run via uvicorn with `--reload` so the server restarts
automatically on code changes (otherwise restart it by hand after every edit);
`--port` sets the port:

    uv run uvicorn app.main:app --reload --port 9000

## Maintenance

    uv run pytest                          # offline suite (fixtures)
    uv run pytest -m live                  # live API smoke tests
    uv run python -m app.catalog.rebuild   # rebuild catalog from raw archive

Data lives in `data/` (catalog.sqlite + append-only archive.sqlite). The
archive is the source of truth; the catalog is derived and rebuildable —
schema or parser changes never require re-crawling.

## Stack

Deliberately boring and dependency-light, which suits a single-user app:

- **FastAPI + httpx** — the work is I/O-bound (crawling AniList / MangaUpdates),
  so everything is async; one rate-limited httpx client is shared across sources.
- **htmx + Jinja2** — server-rendered HTML swapped over the wire, no SPA and no
  JS build step. Routes return small template partials.
- **SQLite, no ORM** — zero-config and embedded; raw SQL is plenty for one user.
  Two files: an append-only `archive` (raw API payloads, the source of truth)
  and a derived, rebuildable `catalog`, so schema or parser changes never need a
  re-crawl.

## Design

The UI is *Inkpress* — a manga-zine / newsprint look rather than a generic
dashboard: two-tone sumi ink on warm paper, a single vermillion (朱) spot
colour, hard-edged panels, halftone screentone, and offset hard shadows (no
blur — print is hard-edged). Display type is Dela Gothic One; CJK falls
through to Noto Sans.
