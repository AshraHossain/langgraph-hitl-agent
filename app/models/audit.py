"""Audit event constants (REQ-traceable)."""
from __future__ import annotations


class EventType:
    NODE_ENTER = "NODE_ENTER"
    NODE_EXIT = "NODE_EXIT"
    INTERRUPT = "INTERRUPT"
    APPROVAL = "APPROVAL"
    ERROR = "ERROR"
    RETRY = "RETRY"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Requirement:
    INGEST = "REQ-RFP-001 | Ingest RFP text and store canonical record"
    EXTRACT = "REQ-RFP-002 | Extract structured RFP fields (scope, budget, deadlines)"
    ANALYZE = "REQ-RFP-003 | Produce risk assessment"
    APPROVE_RISK = "REQ-HITL-001 | Human approval of risk assessment before drafting"
    COMPOSE = "REQ-RFP-004 | Draft proposal response"
    APPROVE_FINAL = "REQ-HITL-002 | Final human approval before finalisation"
    FINALIZE = "REQ-RFP-005 | Persist approved response and close thread"
    RETRY_BUDGET = "REQ-RB-001 | Per-thread bounded retries"
