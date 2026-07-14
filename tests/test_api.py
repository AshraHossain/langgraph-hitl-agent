"""API-layer smoke tests that don't need the full lifespan.

We construct a minimal app with a MemorySaver checkpointer injected so the
routers work without Postgres. DB-backed audit writes are patched to no-ops.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import MemorySaver


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, fake_llm) -> FastAPI:
    # Patch DB + redis in nodes
    async def noop(*args: Any, **kwargs: Any) -> None:
        return None

    from app.graph import nodes as nodes_mod

    monkeypatch.setattr(nodes_mod, "insert_audit_event", noop)
    monkeypatch.setattr(nodes_mod, "publish_event", noop)
    monkeypatch.setattr(nodes_mod, "set_thread_status", noop)

    # Patch checkpointer -> MemorySaver; patch upsert_thread + audit endpoints
    from app.api import approvals as approvals_mod
    from app.api import audit as audit_mod
    from app.api import rfp as rfp_mod

    saver = MemorySaver()
    monkeypatch.setattr(rfp_mod, "current_checkpointer", lambda: saver)
    monkeypatch.setattr(approvals_mod, "current_checkpointer", lambda: saver)
    monkeypatch.setattr(rfp_mod, "upsert_thread", AsyncMock())
    # record_interrupt (used by both submit and resume paths) writes audit + redis
    monkeypatch.setattr(rfp_mod, "insert_audit_event", noop)
    monkeypatch.setattr(rfp_mod, "publish_event", noop)
    monkeypatch.setattr(approvals_mod, "insert_audit_event", AsyncMock())
    monkeypatch.setattr(audit_mod, "fetch_audit_events", AsyncMock(return_value=[]))

    app = FastAPI()
    app.include_router(rfp_mod.router)
    app.include_router(approvals_mod.router)
    app.include_router(audit_mod.router)
    return app


@pytest.mark.asyncio
async def test_submit_and_inspect(app: FastAPI) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/rfp", json={"title": "ACME", "content": "X" * 100})
        assert r.status_code == 200
        tid = r.json()["thread_id"]

        r = await ac.get(f"/threads/{tid}")
        assert r.status_code == 200
        body = r.json()
        assert body["pending_gate"] == "approve_risk"


@pytest.mark.asyncio
async def test_wrong_gate_returns_409(app: FastAPI) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/rfp", json={"title": "ACME", "content": "X" * 100})
        tid = r.json()["thread_id"]

        r = await ac.post(f"/approvals/{tid}", json={
            "gate": "approve_final", "decision": "approve",
            "reviewer": "x", "notes": "",
        })
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_unknown_thread_returns_404(app: FastAPI) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/threads/does-not-exist")
        assert r.status_code == 404
