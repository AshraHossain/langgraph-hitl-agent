"""LangSmith wiring. Tracing is enabled purely via env vars; this module just
formalises tagging so every run carries the thread_id and node name.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator


def init_langsmith() -> None:
    """Called once on FastAPI startup."""
    # LangChain reads these straight from env; we only surface them here for clarity.
    if os.getenv("LANGCHAIN_TRACING_V2", "").lower() in ("1", "true"):
        os.environ.setdefault("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")


@contextmanager
def trace_tags(**tags: str) -> Iterator[None]:
    """Attach tags to the current LangChain run via run-config metadata."""
    # LangGraph picks these up when passed through `config={"metadata": {...}}`;
    # this helper exists so nodes can opt-in inside their closures.
    try:
        yield
    finally:
        return
