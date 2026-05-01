"""Unit tests for the state machine — no Postgres/Redis needed.

We patch `insert_audit_event`, `publish_event`, and `set_thread_status` to no-ops
so the nodes run purely in-memory against MemorySaver.
"""
from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _patch_io(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop(*args: Any, **kwargs: Any) -> None:  # pragma: no cover
        return None

    # Nodes import these names directly from app.database / app.redis_client
    from app.graph import nodes as nodes_mod

    monkeypatch.setattr(nodes_mod, "insert_audit_event", noop)
    monkeypatch.setattr(nodes_mod, "publish_event", noop)
    monkeypatch.setattr(nodes_mod, "set_thread_status", noop)


def _cfg(tid: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": tid}}


@pytest.mark.asyncio
async def test_graph_pauses_at_approve_risk(in_memory_graph, fake_llm, thread_id):
    initial = {
        "thread_id": thread_id,
        "title": "ACME RFP",
        "content": "Migrate 40 services to AWS. Budget 2M. Deadline Q4.",
        "status": "RUNNING",
    }
    await in_memory_graph.ainvoke(initial, config=_cfg(thread_id))

    snap = await in_memory_graph.aget_state(_cfg(thread_id))
    # After analyze, should pause BEFORE approve_risk
    assert "approve_risk" in list(snap.next)
    assert snap.values["extracted"]["summary"]
    assert snap.values["risk"]["overall_score"] == 6.5


@pytest.mark.asyncio
async def test_full_happy_path(in_memory_graph, fake_llm, thread_id):
    initial = {
        "thread_id": thread_id,
        "title": "ACME RFP",
        "content": "X" * 100,
        "status": "RUNNING",
    }
    await in_memory_graph.ainvoke(initial, config=_cfg(thread_id))

    # Approve risk
    await in_memory_graph.aupdate_state(
        _cfg(thread_id),
        {"approvals": {"approve_risk": {"decision": "approve",
                                        "reviewer": "tester", "notes": "ok"}}},
        as_node="approve_risk",
    )
    await in_memory_graph.ainvoke(None, config=_cfg(thread_id))

    snap = await in_memory_graph.aget_state(_cfg(thread_id))
    assert "approve_final" in list(snap.next)
    assert snap.values["draft"].startswith("Dear ACME")

    # Approve final
    await in_memory_graph.aupdate_state(
        _cfg(thread_id),
        {"approvals": {"approve_final": {"decision": "approve",
                                         "reviewer": "tester", "notes": "ship"}}},
        as_node="approve_final",
    )
    await in_memory_graph.ainvoke(None, config=_cfg(thread_id))

    snap = await in_memory_graph.aget_state(_cfg(thread_id))
    assert not list(snap.next)  # reached END
    assert snap.values["status"] == "COMPLETED"
    assert snap.values["final_output"].startswith("Dear ACME")


@pytest.mark.asyncio
async def test_reject_at_risk_gate_goes_to_rejected(in_memory_graph, fake_llm, thread_id):
    initial = {
        "thread_id": thread_id,
        "title": "ACME RFP",
        "content": "X" * 100,
        "status": "RUNNING",
    }
    await in_memory_graph.ainvoke(initial, config=_cfg(thread_id))

    await in_memory_graph.aupdate_state(
        _cfg(thread_id),
        {"approvals": {"approve_risk": {"decision": "reject",
                                        "reviewer": "tester", "notes": "too risky"}}},
        as_node="approve_risk",
    )
    await in_memory_graph.ainvoke(None, config=_cfg(thread_id))

    snap = await in_memory_graph.aget_state(_cfg(thread_id))
    assert snap.values["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_revise_at_risk_gate_loops_to_analyze(in_memory_graph, fake_llm, thread_id):
    initial = {
        "thread_id": thread_id,
        "title": "ACME RFP",
        "content": "X" * 100,
        "status": "RUNNING",
    }
    await in_memory_graph.ainvoke(initial, config=_cfg(thread_id))

    await in_memory_graph.aupdate_state(
        _cfg(thread_id),
        {"approvals": {"approve_risk": {"decision": "revise",
                                        "reviewer": "tester",
                                        "notes": "reassess with new assumptions"}}},
        as_node="approve_risk",
    )
    await in_memory_graph.ainvoke(None, config=_cfg(thread_id))

    # Back to approve_risk pending after re-analysis
    snap = await in_memory_graph.aget_state(_cfg(thread_id))
    assert "approve_risk" in list(snap.next)
    # fake_llm saw multiple analyze calls
    analyze_calls = [c for c in fake_llm.calls if "[analyze]" in c["system"]]
    assert len(analyze_calls) >= 2
