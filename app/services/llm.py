"""LLM wrapper with retry-budget enforcement.

Node code depends on an abstract LLMService so tests can stub it without
network. In production we hand back a LangChain ChatOpenAI bound to structured
output; in tests we hand back FakeLLMService.
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.services.retry import RetryBudget
from app.utils.logger import get_logger

log = get_logger(__name__)


class LLMService(Protocol):
    async def structured(
        self,
        *,
        thread_id: str,
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]: ...


class OpenAILLMService:
    """ChatOpenAI-backed LLMService. Lazy-imported so unit tests don't need the key."""

    def __init__(self) -> None:
        from langchain_openai import ChatOpenAI  # local import

        settings = get_settings()
        self._llm = ChatOpenAI(
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            api_key=settings.openai_api_key,
        )

    async def structured(
        self,
        *,
        thread_id: str,
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        budget = RetryBudget(thread_id)
        llm_struct = self._llm.with_structured_output(schema)

        last_exc: Exception | None = None
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            reraise=True,
        ):
            with attempt:
                if not await budget.try_consume():
                    raise RuntimeError("retry_budget_exhausted")
                try:
                    result = await llm_struct.ainvoke(
                        [SystemMessage(content=system), HumanMessage(content=user)]
                    )
                    return result if isinstance(result, dict) else json.loads(result.json())
                except Exception as e:
                    last_exc = e
                    log.warning("llm_attempt_failed", err=str(e))
                    raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("unreachable")


class FakeLLMService:
    """Deterministic stub for tests. Returns canned outputs keyed by a phase tag in `system`."""

    def __init__(self, canned: dict[str, dict[str, Any]] | None = None) -> None:
        self.canned = canned or {}
        self.calls: list[dict[str, Any]] = []

    async def structured(
        self,
        *,
        thread_id: str,
        system: str,
        user: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append({"thread_id": thread_id, "system": system, "user": user})
        for key, payload in self.canned.items():
            if key in system:
                return payload
        # Safe fallback: echo back a minimal shape respecting the schema keys
        props = (schema or {}).get("properties", {})
        return {k: "" for k in props}


_singleton: LLMService | None = None


def get_llm() -> LLMService:
    global _singleton
    if _singleton is None:
        _singleton = OpenAILLMService()
    return _singleton


def set_llm(llm: LLMService) -> None:
    """Test hook — swap the singleton."""
    global _singleton
    _singleton = llm
