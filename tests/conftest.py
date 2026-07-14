"""Test fixtures.

- Unit tests use an in-memory checkpointer and a fake LLM.
- Integration tests are marked `@pytest.mark.integration` and require Postgres+Redis.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

# Ensure tests never accidentally hit a real OpenAI key
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

from app.services import llm as llm_module  # noqa: E402
from app.services.llm import FakeLLMService  # noqa: E402


@pytest.fixture
def fake_llm() -> FakeLLMService:
    canned = {
        "[extract]": {
            "summary": "Cloud migration RFP",
            "scope": ["lift-and-shift", "40 services"],
            "budget_usd": 2_000_000,
            "deadline": "Q4",
            "key_requirements": ["AWS", "zero downtime"],
        },
        "[analyze]": {
            "overall_score": 6.5,
            "risks": [
                {"category": "schedule", "severity": "medium",
                 "description": "Aggressive Q4 deadline", "mitigation": "Phased rollout"},
                {"category": "scope",    "severity": "high",
                 "description": "40 services is large", "mitigation": "Split into waves"},
            ],
        },
        "[compose]": {"draft": "Dear ACME, we propose a phased AWS migration ..."},
    }
    svc = FakeLLMService(canned=canned)
    llm_module.set_llm(svc)
    return svc


@pytest.fixture
def thread_id() -> str:
    return f"test-{uuid.uuid4()}"


@pytest_asyncio.fixture
async def in_memory_graph():
    """Graph compiled against MemorySaver — unit-test friendly."""
    from langgraph.checkpoint.memory import MemorySaver

    from app.graph.builder import build_graph

    saver = MemorySaver()
    return build_graph(saver)
