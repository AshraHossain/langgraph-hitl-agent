"""Build the LangGraph StateGraph.

Topology:
  START -> ingest -> extract -> analyze -> approve_risk
      approve_risk --(approve)--> compose
      approve_risk --(revise)---> analyze
      approve_risk --(reject)---> finalize
  compose -> approve_final
      approve_final --(approve/reject)--> finalize
      approve_final --(revise)----------> compose
  finalize -> END

Interrupts are placed **before** the approval nodes so their bodies run only
when a human resumes via POST /approvals/{thread_id}.
"""
from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    analyze_node,
    approve_final_node,
    approve_risk_node,
    compose_node,
    extract_node,
    finalize_node,
    ingest_node,
    route_after_analyze,
    route_after_compose,
    route_after_extract,
    route_after_final_gate,
    route_after_ingest,
    route_after_risk_gate,
)
from app.graph.state import RFPState


def build_graph(checkpointer: BaseCheckpointSaver) -> Any:
    g = StateGraph(RFPState)

    g.add_node("ingest", ingest_node)
    g.add_node("extract", extract_node)
    g.add_node("analyze", analyze_node)
    g.add_node("approve_risk", approve_risk_node)
    g.add_node("compose", compose_node)
    g.add_node("approve_final", approve_final_node)
    g.add_node("finalize", finalize_node)

    g.add_edge(START, "ingest")
    g.add_conditional_edges("ingest", route_after_ingest,
                            {"extract": "extract", "finalize": "finalize"})
    g.add_conditional_edges("extract", route_after_extract,
                            {"analyze": "analyze", "finalize": "finalize"})
    g.add_conditional_edges("analyze", route_after_analyze,
                            {"approve_risk": "approve_risk", "finalize": "finalize"})
    g.add_conditional_edges("approve_risk", route_after_risk_gate,
                            {"compose": "compose", "analyze": "analyze",
                             "finalize": "finalize"})
    g.add_conditional_edges("compose", route_after_compose,
                            {"approve_final": "approve_final", "finalize": "finalize"})
    g.add_conditional_edges("approve_final", route_after_final_gate,
                            {"finalize": "finalize", "compose": "compose"})
    g.add_edge("finalize", END)

    return g.compile(
        checkpointer=checkpointer,
        interrupt_before=["approve_risk", "approve_final"],
    )
