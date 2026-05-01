"""FastAPI entrypoint + lifespan management for DB / checkpointer / Redis."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import approvals, audit, rfp
from app.config import get_settings
from app.database import close_pool, get_pool, run_migrations
from app.graph.checkpointer import start_checkpointer, stop_checkpointer
from app.redis_client import close_redis, get_redis
from app.services.observability import init_langsmith
from app.utils.logger import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log = get_logger(__name__)
    settings = get_settings()

    init_langsmith()

    # Order matters: DB pool -> migrations -> checkpointer -> redis
    await get_pool()
    await run_migrations()
    await start_checkpointer()
    await get_redis()
    log.info("startup_complete",
             db_host=settings.postgres_host,
             project=settings.langchain_project)

    try:
        yield
    finally:
        log.info("shutdown_begin")
        await stop_checkpointer()
        await close_redis()
        await close_pool()
        log.info("shutdown_complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="LangGraph HITL Agent Pipeline",
        version="0.1.0",
        description="Production-grade Human-in-the-Loop RFP analyzer.",
        lifespan=lifespan,
    )

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(rfp.router)
    app.include_router(approvals.router)
    app.include_router(audit.router)

    return app


app = create_app()
