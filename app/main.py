import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import Config, load_config
from app.crawler.jobs import run_job
from app.crawler.refresh import RefreshState, refresh_loop
from app.sources.anilist import AniListSource
from app.sources.archive import RawArchive
from app.sources.mangaupdates import MangaUpdatesSource


def create_app(config: Config | None = None, sources=None) -> FastAPI:
    cfg = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        app.state.covers_dir = cfg.covers_dir
        app.state.covers_dir.mkdir(parents=True, exist_ok=True)
        catalog = db.connect(cfg.catalog_path)
        db.init_catalog(catalog)
        archive_conn = db.connect(cfg.archive_path)
        db.init_archive(archive_conn)
        app.state.catalog = catalog
        app.state.archive = RawArchive(archive_conn)
        app.state.jobs = {}
        app.state.refresh = RefreshState()
        app.state.refresh_task = None
        app.state.client = None
        if sources is not None:
            app.state.sources = sources
        else:
            app.state.client = httpx.AsyncClient(
                headers={"User-Agent": "tsugi/0.1 (personal project)"}, timeout=30
            )
            app.state.sources = {
                "anilist": AniListSource(app.state.client, app.state.archive),
                "mangaupdates": MangaUpdatesSource(app.state.client, app.state.archive),
            }
        from app.catalog.trope_dict import dictionary_present, import_dictionary_safe

        app.state.dict_task = None
        if app.state.client is not None and not dictionary_present(catalog):
            app.state.dict_task = asyncio.create_task(
                import_dictionary_safe(catalog, app.state.client, app.state.archive)
            )
        if app.state.client is not None:  # real sources only — never in tests
            app.state.refresh_task = asyncio.create_task(refresh_loop(app))
        # resume crawl jobs interrupted by a restart (queue is persisted)
        for r in catalog.execute("SELECT id FROM crawl_jobs WHERE status='running'").fetchall():
            app.state.jobs[r["id"]] = asyncio.create_task(
                run_job(catalog, app.state.sources, r["id"])
            )
        yield
        if app.state.dict_task is not None:
            app.state.dict_task.cancel()
            await asyncio.gather(app.state.dict_task, return_exceptions=True)
        if app.state.refresh_task is not None:
            app.state.refresh_task.cancel()
            await asyncio.gather(app.state.refresh_task, return_exceptions=True)
        # cancel running crawl tasks BEFORE closing their sqlite connection,
        # otherwise a task iteration races a closed database on shutdown
        for task in app.state.jobs.values():
            task.cancel()
        await asyncio.gather(*app.state.jobs.values(), return_exceptions=True)
        if app.state.client:
            await app.state.client.aclose()
        catalog.close()
        archive_conn.close()

    app = FastAPI(lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"),
              name="static")
    from app.web.routes import router

    app.include_router(router)
    from app.web.covers import router as covers_router

    app.include_router(covers_router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", port=9000)
