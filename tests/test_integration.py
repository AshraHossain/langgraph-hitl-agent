"""End-to-end integration test. Requires Postgres + Redis + env for FakeLLM.

Run with:  pytest -m integration
"""
from __future__ import annotations

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


@pytest.fixture
def client_env(monkeypatch):
    # Force fake LLM and local services for the lifespan app
    monkeypatch.setenv("POSTGRES_HOST", os.getenv("POSTGRES_HOST", "localhost"))
    monkeypatch.setenv("REDIS_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    # Clear cached settings from previous tests
    from app.config import get_settings
    get_settings.cache_clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_full_api_happy_path(client_env):
    from app.main import create_app
    from app.services import llm as llm_module
    from app.services.llm import FakeLLMService

    # Swap in the fake LLM before the app is exercised
    llm_module.set_llm(
        FakeLLMService(
            canned={
                "[extract]": {
                    "summary": "RFP",
                    "scope": ["A"],
                    "budget_usd": 1,
                    "deadline": "Q4",
                    "key_requirements": ["x"],
                },
                "[analyze]": {
                    "overall_score": 3,
                    "risks": [
                        {"category": "scope", "severity": "low",
                         "description": "trivial", "mitigation": "none"}
                    ],
                },
                "[compose]": {"draft": "Here is our proposal."},
            }
        )
    )

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # health
        r = await ac.get("/healthz")
        assert r.status_code == 200

        # submit
        r = await ac.post("/rfp", json={"title": f"t-{uuid.uuid4()}",
                                         "content": "X" * 50})
        assert r.status_code == 200
        tid = r.json()["thread_id"]

        # after submit, should be awaiting approve_risk
        r = await ac.get(f"/threads/{tid}")
        assert r.status_code == 200
        assert r.json()["pending_gate"] == "approve_risk"

        # approve risk
        r = await ac.post(f"/approvals/{tid}", json={
            "gate": "approve_risk", "decision": "approve",
            "reviewer": "tester", "notes": "ok",
        })
        assert r.status_code == 200
        assert r.json()["pending_gate"] == "approve_final"

        # approve final
        r = await ac.post(f"/approvals/{tid}", json={
            "gate": "approve_final", "decision": "approve",
            "reviewer": "tester", "notes": "ship",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "COMPLETED"
        assert r.json()["values"]["final_output"] == "Here is our proposal."

        # audit trail has node enters/exits + approvals + completion
        r = await ac.get(f"/audit/{tid}")
        assert r.status_code == 200
        types = {e["event_type"] for e in r.json()["events"]}
        assert {"NODE_ENTER", "NODE_EXIT", "INTERRUPT", "APPROVAL", "COMPLETED"} <= types
