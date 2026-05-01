"""RFP submission + thread state endpoints."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from app.database import upsert_thread
from app.graph.builder import build_graph
from app.graph.checkpointer import current_checkpointer
from app.models.schemas import RFPSubmitRequest, RFPSubmitResponse, ThreadState

router = APIRouter(tags=["rfp"])


def _config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _pending_gate(next_nodes: tuple[str, ...] | list[str]) -> str | None:
    for n in next_nodes or ():
        if n in ("approve_risk", "approve_final"):
            return n
    return None


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
