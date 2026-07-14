"""Approval endpoint — the only way the human unblocks an interrupted graph."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.rfp import record_interrupt
from app.database import insert_audit_event
from app.graph.builder import build_graph
from app.graph.checkpointer import current_checkpointer
from app.models.audit import EventType, Requirement
from app.models.schemas import ApprovalRequest, ThreadState

router = APIRouter(tags=["approvals"])


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


@router.post("/approvals/{thread_id}", response_model=ThreadState)
async def submit_approval(thread_id: str, req: ApprovalRequest) -> ThreadState:
    graph = build_graph(current_checkpointer())
    snapshot = await graph.aget_state(_config(thread_id))
    if snapshot is None or not snapshot.values:
        raise HTTPException(status_code=404, detail="thread not found")

    next_nodes = list(snapshot.next or [])
    if req.gate not in next_nodes:
        raise HTTPException(
            status_code=409,
            detail=f"gate {req.gate!r} is not pending (next={next_nodes})",
        )

    # Write the human decision into state — the graph reducer merges it.
    existing = snapshot.values.get("approvals") or {}
    existing = dict(existing)
    existing[req.gate] = {
        "decision": req.decision,
        "reviewer": req.reviewer,
        "notes": req.notes,
        "patch": req.patch,
    }

    patch_state: dict[str, Any] = {"approvals": existing}
    # Let the reviewer inject a content patch on "revise"
    if req.patch:
        patch_state.update(req.patch)

    await graph.aupdate_state(_config(thread_id), patch_state, as_node=req.gate)

    requirement = (
        Requirement.APPROVE_RISK if req.gate == "approve_risk" else Requirement.APPROVE_FINAL
    )
    await insert_audit_event(
        thread_id,
        event_type=EventType.APPROVAL,
        node=req.gate,
        requirement=requirement,
        actor=req.reviewer,
        payload={"decision": req.decision, "notes": req.notes, "patched": bool(req.patch)},
    )

    # Resume the graph until the next interrupt or END.
    await graph.ainvoke(None, config=_config(thread_id))

    new_snapshot = await graph.aget_state(_config(thread_id))
    values = new_snapshot.values or {}
    next_list = list(new_snapshot.next or [])
    pending = next((n for n in next_list if n in ("approve_risk", "approve_final")), None)
    if pending:
        await record_interrupt(thread_id, pending)
    return ThreadState(
        thread_id=thread_id,
        status=values.get("status", "UNKNOWN"),
        next=next_list,
        values=values,
        pending_gate=pending,
    )
