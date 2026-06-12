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

## Container

Build the image locally and run it — no registry needed. The base images are
multi-arch, so building on an amd64 or arm64 machine produces the right
architecture automatically. Copy the example compose file, then bring it up
(`compose.yaml` isn't committed; `docker-compose.yml` is gitignored so your
personalised one stays local):

    cp docker-compose.yml.example docker-compose.yml
    podman compose up -d --build

Then open http://localhost:9000. `docker compose up -d --build` works
identically. By default data persists in a named `tsugi-data` volume and the
image stays code-only; to instead run against an existing host `data/`,
uncomment the bind-mount lines in your `docker-compose.yml` (see the example's
comments — `userns_mode: keep-id` is podman-rootless only).

Without compose:

    podman build -t tsugi .
    podman run -d -p 9000:9000 -v tsugi-data:/data tsugi

Raw `docker build` needs `-f Containerfile` (Docker only auto-detects
`Dockerfile`); `podman build` finds `Containerfile` on its own.

To carry an existing local `data/` into the named volume before first run:

    podman run --rm -v tsugi-data:/data -v ./data:/src:ro,Z \
      docker.io/library/busybox cp -a /src/. /data/

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
