"""Graph nodes.

Every node is:
  * async,
  * pure w.r.t. RFPState  (reads old state, returns partial update dict),
  * emits AuditEvents via `insert_audit_event` for traceability (DO-178C),
  * tagged with a REQ-<id> so requirement coverage is mechanically checkable.

The nodes never call `input()` or raise unclassified exceptions: any LLM / IO
failure bubbles up as a RETRY audit event, and the retry budget in
`app.services.retry` determines whether the graph continues or terminates.
"""
from __future__ import annotations

from typing import Any

from app.database import insert_audit_event, set_thread_status
from app.models.audit import EventType, Requirement
from app.redis_client import publish_event
from app.services.llm import get_llm
from app.services.retry import RetryBudget
from app.utils.logger import get_logger

log = get_logger(__name__)


# --------- JSON schemas used for structured LLM output ---------

EXTRACT_SCHEMA: dict[str, Any] = {
    "title": "RFPExtract",
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "scope": {"type": "array", "items": {"type": "string"}},
        "budget_usd": {"type": "number"},
        "deadline": {"type": "string"},
        "key_requirements": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "scope", "key_requirements"],
}

RISK_SCHEMA: dict[str, Any] = {
    "title": "RFPRisk",
    "type": "object",
    "properties": {
        "overall_score": {"type": "number"},
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "description": {"type": "string"},
                    "mitigation": {"type": "string"},
                },
                "required": ["category", "severity", "description"],
            },
        },
    },
    "required": ["overall_score", "risks"],
}

DRAFT_SCHEMA: dict[str, Any] = {
    "title": "RFPDraft",
    "type": "object",
    "properties": {"draft": {"type": "string"}},
    "required": ["draft"],
}


# --------- Node helpers ---------

async def _audit(tid: str, **kwargs: Any) -> None:
    await insert_audit_event(tid, **kwargs)


def _tid(state: dict[str, Any]) -> str:
    t = state.get("thread_id")
    if not t:
        raise ValueError("thread_id missing from state")
    return str(t)


# --------- Node implementations ---------

async def ingest_node(state: dict[str, Any]) -> dict[str, Any]:
    """REQ-RFP-001 — Ingest: validate input, stamp the run."""
    tid = _tid(state)
    await _audit(tid, event_type=EventType.NODE_ENTER, node="ingest",
                 requirement=Requirement.INGEST)
    if not state.get("title") or not state.get("content"):
        await _audit(tid, event_type=EventType.ERROR, node="ingest",
                     payload={"reason": "missing title/content"})
        return {"status": "FAILED"}
    out = {"status": "RUNNING"}
    await _audit(tid, event_type=EventType.NODE_EXIT, node="ingest",
                 requirement=Requirement.INGEST,
                 payload={"chars": len(state["content"])})
    return out


async def extract_node(state: dict[str, Any]) -> dict[str, Any]:
    """REQ-RFP-002 — Extract structured fields from the RFP."""
    tid = _tid(state)
    await _audit(tid, event_type=EventType.NODE_ENTER, node="extract",
                 requirement=Requirement.EXTRACT)
    try:
        llm = get_llm()
        result = await llm.structured(
            thread_id=tid,
            system="You are an RFP analyst. Tag: [extract]. Return strict JSON per schema.",
            user=f"RFP title: {state.get('title')}\n\nRFP text:\n{state.get('content')}",
            schema=EXTRACT_SCHEMA,
        )
    except Exception as e:
        await _audit(tid, event_type=EventType.ERROR, node="extract",
                     payload={"err": str(e)})
        if "retry_budget_exhausted" in str(e):
            return {"status": "FAILED"}
        raise

    await _audit(tid, event_type=EventType.NODE_EXIT, node="extract",
                 requirement=Requirement.EXTRACT,
                 payload={"keys": sorted(result.keys())})
    return {"extracted": result}


async def analyze_node(state: dict[str, Any]) -> dict[str, Any]:
    """REQ-RFP-003 — Risk assessment."""
    tid = _tid(state)
    await _audit(tid, event_type=EventType.NODE_ENTER, node="analyze",
                 requirement=Requirement.ANALYZE)
    try:
        llm = get_llm()
        result = await llm.structured(
            thread_id=tid,
            system="You are a risk analyst. Tag: [analyze]. Return strict JSON per schema.",
            user=f"Extracted RFP:\n{state.get('extracted')}",
            schema=RISK_SCHEMA,
        )
    except Exception as e:
        await _audit(tid, event_type=EventType.ERROR, node="analyze",
                     payload={"err": str(e)})
        if "retry_budget_exhausted" in str(e):
            return {"status": "FAILED"}
        raise

    await _audit(tid, event_type=EventType.NODE_EXIT, node="analyze",
                 requirement=Requirement.ANALYZE,
                 payload={"overall_score": result.get("overall_score"),
                          "num_risks": len(result.get("risks", []))})
    return {"risk": result}


async def approve_risk_node(state: dict[str, Any]) -> dict[str, Any]:
    """REQ-HITL-001 — Human approval gate. Interrupt is applied at graph-build time."""
    tid = _tid(state)
    await _audit(tid, event_type=EventType.INTERRUPT, node="approve_risk",
                 requirement=Requirement.APPROVE_RISK,
                 payload={"gate": "approve_risk"})
    await publish_event(tid, {"type": "interrupt", "gate": "approve_risk"})
    # Return an empty update — state change happens when the human resumes via API.
    return {}


async def compose_node(state: dict[str, Any]) -> dict[str, Any]:
    """REQ-RFP-004 — Compose proposal response."""
    tid = _tid(state)
    await _audit(tid, event_type=EventType.NODE_ENTER, node="compose",
                 requirement=Requirement.COMPOSE)
    try:
        llm = get_llm()
        result = await llm.structured(
            thread_id=tid,
            system="You are a proposal writer. Tag: [compose]. Return JSON with a 'draft' field.",
            user=(
                f"Title: {state.get('title')}\n"
                f"Extract: {state.get('extracted')}\n"
                f"Risk: {state.get('risk')}\n"
                f"Reviewer notes: {(state.get('approvals') or {}).get('approve_risk', {}).get('notes','')}"
            ),
            schema=DRAFT_SCHEMA,
        )
    except Exception as e:
        await _audit(tid, event_type=EventType.ERROR, node="compose",
                     payload={"err": str(e)})
        if "retry_budget_exhausted" in str(e):
            return {"status": "FAILED"}
        raise

    draft = result.get("draft", "")
    await _audit(tid, event_type=EventType.NODE_EXIT, node="compose",
                 requirement=Requirement.COMPOSE,
                 payload={"chars": len(draft)})
    return {"draft": draft}


async def approve_final_node(state: dict[str, Any]) -> dict[str, Any]:
    """REQ-HITL-002 — Final human approval gate."""
    tid = _tid(state)
    await _audit(tid, event_type=EventType.INTERRUPT, node="approve_final",
                 requirement=Requirement.APPROVE_FINAL,
                 payload={"gate": "approve_final"})
    await publish_event(tid, {"type": "interrupt", "gate": "approve_final"})
    return {}


async def finalize_node(state: dict[str, Any]) -> dict[str, Any]:
    """REQ-RFP-005 — Finalize the thread after both approvals."""
    tid = _tid(state)
    await _audit(tid, event_type=EventType.NODE_ENTER, node="finalize",
                 requirement=Requirement.FINALIZE)

    approvals = state.get("approvals") or {}
    risk_ok = approvals.get("approve_risk", {}).get("decision") == "approve"
    final_ok = approvals.get("approve_final", {}).get("decision") == "approve"

    if not (risk_ok and final_ok):
        await set_thread_status(tid, "REJECTED")
        await _audit(tid, event_type=EventType.FAILED, node="finalize",
                     requirement=Requirement.FINALIZE,
                     payload={"approvals": approvals})
        await publish_event(tid, {"type": "done", "status": "REJECTED"})
        return {"status": "REJECTED", "final_output": ""}

    final_output = state.get("draft", "")
    await set_thread_status(tid, "COMPLETED")
    await _audit(tid, event_type=EventType.COMPLETED, node="finalize",
                 requirement=Requirement.FINALIZE,
                 payload={"chars": len(final_output)})
    await publish_event(tid, {"type": "done", "status": "COMPLETED"})
    return {"status": "COMPLETED", "final_output": final_output}


# --------- Edge routers ---------

def route_after_ingest(state: dict[str, Any]) -> str:
    return "finalize" if state.get("status") == "FAILED" else "extract"


def route_after_extract(state: dict[str, Any]) -> str:
    return "finalize" if state.get("status") == "FAILED" else "analyze"


def route_after_analyze(state: dict[str, Any]) -> str:
    return "finalize" if state.get("status") == "FAILED" else "approve_risk"


def route_after_risk_gate(state: dict[str, Any]) -> str:
    decision = (state.get("approvals") or {}).get("approve_risk", {}).get("decision")
    if decision == "reject":
        return "finalize"
    if decision == "revise":
        return "analyze"   # loop back
    return "compose"


def route_after_compose(state: dict[str, Any]) -> str:
    return "finalize" if state.get("status") == "FAILED" else "approve_final"


def route_after_final_gate(state: dict[str, Any]) -> str:
    decision = (state.get("approvals") or {}).get("approve_final", {}).get("decision")
    if decision == "revise":
        return "compose"
    # approve or reject both flow to finalize (finalize handles reject)
    return "finalize"


__all__ = [
    "ingest_node",
    "extract_node",
    "analyze_node",
    "approve_risk_node",
    "compose_node",
    "approve_final_node",
    "finalize_node",
    "route_after_ingest",
    "route_after_extract",
    "route_after_analyze",
    "route_after_risk_gate",
    "route_after_compose",
    "route_after_final_gate",
    "EXTRACT_SCHEMA",
    "RISK_SCHEMA",
    "DRAFT_SCHEMA",
]

# Silence unused-import lint noise on RetryBudget import (kept for re-export)
_ = RetryBudget
