"""Pydantic API request/response models."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---- Requests ----

class RFPSubmitRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=10, max_length=100_000)


class ApprovalRequest(BaseModel):
    gate: Literal["approve_risk", "approve_final"]
    decision: Literal["approve", "reject", "revise"]
    reviewer: str = Field(..., min_length=1, max_length=100)
    notes: str = ""
    # On "revise" the reviewer can inject patched content:
    patch: dict[str, Any] | None = None


# ---- Responses ----

class RFPSubmitResponse(BaseModel):
    thread_id: str
    status: str


class ThreadState(BaseModel):
    thread_id: str
    status: str
    next: list[str]
    values: dict[str, Any]
    pending_gate: str | None = None


class AuditEventOut(BaseModel):
    id: int
    thread_id: str
    ts: str
    event_type: str
    node: str | None
    requirement: str | None
    actor: str
    payload: dict[str, Any]


class AuditResponse(BaseModel):
    thread_id: str
    events: list[AuditEventOut]
