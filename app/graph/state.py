"""LangGraph state schema for the RFP pipeline."""
from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


def _merge_dicts(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    """Reducer for dict-valued channels: b overrides a."""
    out = dict(a or {})
    out.update(b or {})
    return out


class RFPState(TypedDict, total=False):
    # Input (immutable after ingest)
    thread_id: str
    title: str
    content: str

    # Extracted structured fields (REQ-RFP-002)
    extracted: Annotated[dict[str, Any], _merge_dicts]

    # Risk assessment (REQ-RFP-003)
    risk: Annotated[dict[str, Any], _merge_dicts]

    # Draft proposal response (REQ-RFP-004)
    draft: str

    # Final decision outcomes recorded by human approvers
    approvals: Annotated[dict[str, Any], _merge_dicts]

    # Terminal outputs (REQ-RFP-005)
    final_output: str
    status: str  # RUNNING | APPROVED | REJECTED | FAILED | COMPLETED

    # Optional free-form messages for debugging / LangSmith replays
    messages: Annotated[list, add_messages]
