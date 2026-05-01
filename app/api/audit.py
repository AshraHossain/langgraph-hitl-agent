"""Audit trail endpoint."""
from __future__ import annotations

from fastapi import APIRouter

from app.database import fetch_audit_events
from app.models.schemas import AuditEventOut, AuditResponse

router = APIRouter(tags=["audit"])


@router.get("/audit/{thread_id}", response_model=AuditResponse)
async def get_audit(thread_id: str) -> AuditResponse:
    rows = await fetch_audit_events(thread_id)
    return AuditResponse(
        thread_id=thread_id,
        events=[AuditEventOut(**r) for r in rows],
    )
