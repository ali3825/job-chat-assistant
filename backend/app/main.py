"""FastAPI application factory. In production the built React app is served from the same origin."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .core.logging_setup import install_request_logging, setup_logging
from .db import init_db
from .errors import install_error_handlers
from .routers import applications, chat, health, jobs
from .services.job_store import JobStore
from .services.llm import build_provider
from .services.orchestrator import Orchestrator
from .services.sessions import SessionStore

log = logging.getLogger("app")


def create_app(settings: Settings | None = None, store: JobStore | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        yield

    app = FastAPI(title="AI Job Chat Assistant", version="1.0.0", lifespan=lifespan,
                  description="Upload a resume, get matched jobs, and track applications through chat.")
    app.state.settings = settings
    app.state.store = store if store is not None else JobStore.load(settings.jobs_file)
    llm = build_provider(settings)
    app.state.orchestrator = Orchestrator(
        app.state.store, SessionStore(settings.session_ttl_minutes, settings.max_sessions), llm)
    log.info("Job dataset: %d jobs; LLM: %s", len(app.state.store), llm.name if llm else "fallback mode")

    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["GET", "POST", "PATCH"],
                       allow_headers=["Content-Type", "X-Client-Id"])
    install_request_logging(app)
    install_error_handlers(app)
    for r in (health.router, jobs.router, applications.router, chat.router):
        app.include_router(r)

    dist = Path(settings.frontend_dist)
    if (dist / "index.html").exists():
        if (dist / "assets").exists():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "docs", "openapi")):
                from .errors import AppError
                raise AppError(404, "not_found", "Not found.")
            f = (dist / path).resolve()
            if path and f.is_file() and dist.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")
    return app


app = create_app()
