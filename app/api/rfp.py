"""RFP submission + thread state endpoints."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from app.database import insert_audit_event, upsert_thread
from app.graph.builder import build_graph
from app.graph.checkpointer import current_checkpointer
from app.models.audit import EventType, Requirement
from app.models.schemas import RFPSubmitRequest, RFPSubmitResponse, ThreadState
from app.redis_client import publish_event

router = APIRouter(tags=["rfp"])


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _pending_gate(next_nodes: tuple[str, ...] | list[str]) -> str | None:
    for n in next_nodes or ():
        if n in ("approve_risk", "approve_final"):
            return n
    return None


async def record_interrupt(thread_id: str, gate: str) -> None:
    """Audit + publish the INTERRUPT at the moment the graph pauses.

    The gate nodes themselves never execute: `interrupt_before` pauses ahead
    of them and the approvals API resumes with `as_node=<gate>`, which marks
    them as already run — so this cannot live inside the node bodies.
    """
    requirement = (
        Requirement.APPROVE_RISK if gate == "approve_risk" else Requirement.APPROVE_FINAL
    )
    await insert_audit_event(
        thread_id,
        event_type=EventType.INTERRUPT,
        node=gate,
        requirement=requirement,
        payload={"gate": gate},
    )
    await publish_event(thread_id, {"type": "interrupt", "gate": gate})


@router.post("/rfp", response_model=RFPSubmitResponse)
async def submit_rfp(req: RFPSubmitRequest) -> RFPSubmitResponse:
    thread_id = str(uuid.uuid4())
    await upsert_thread(thread_id, req.title, req.content)

    graph = build_graph(current_checkpointer())
    initial = {
        "thread_id": thread_id,
        "title": req.title,
        "content": req.content,
        "status": "RUNNING",
    }
    # Run until the first interrupt (approve_risk).
    await graph.ainvoke(initial, config=_config(thread_id))

    snapshot = await graph.aget_state(_config(thread_id))
    gate = _pending_gate(snapshot.next)
    if gate:
        await record_interrupt(thread_id, gate)

    return RFPSubmitResponse(thread_id=thread_id, status="AWAITING_APPROVAL")


@router.get("/threads/{thread_id}", response_model=ThreadState)
async def get_thread(thread_id: str) -> ThreadState:
    graph = build_graph(current_checkpointer())
    snapshot = await graph.aget_state(_config(thread_id))
    if snapshot is None or snapshot.values == {}:
        raise HTTPException(status_code=404, detail="thread not found")

    values = snapshot.values or {}
    status = values.get("status", "UNKNOWN")
    next_nodes = list(snapshot.next or [])
    return ThreadState(
        thread_id=thread_id,
        status=status,
        next=next_nodes,
        values=values,
        pending_gate=_pending_gate(snapshot.next),
    )
