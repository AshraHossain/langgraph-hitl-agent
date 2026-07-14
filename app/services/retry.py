"""Per-thread retry budget (token bucket).

Semantics (REQ-RB-001):
  * Each thread starts with N tokens (Settings.retry_budget_per_thread).
  * Every retryable failure consumes one token.
  * Tokens refill linearly over `retry_budget_window_seconds` to the configured max.
  * `try_consume()` returns False when the bucket is empty — the caller must
    propagate that as a terminal failure (no further retries) to keep the
    graph deterministic and bounded.

The bucket is persisted in `retry_budget` so bounds survive a crash/restart.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import get_settings
from app.database import get_pool


@dataclass
class BudgetState:
    tokens: int
    last_refill_at: datetime


class RetryBudget:
    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        settings = get_settings()
        self.max_tokens = settings.retry_budget_per_thread
        self.window_seconds = settings.retry_budget_window_seconds

    async def _load(self) -> BudgetState:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT tokens, last_refill_at FROM retry_budget WHERE thread_id=$1",
                self.thread_id,
            )
            if row is None:
                now = datetime.now(UTC)
                await conn.execute(
                    """
                    INSERT INTO retry_budget(thread_id, tokens, last_refill_at)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (thread_id) DO NOTHING
                    """,
                    self.thread_id,
                    self.max_tokens,
                    now,
                )
                return BudgetState(tokens=self.max_tokens, last_refill_at=now)
            return BudgetState(tokens=row["tokens"], last_refill_at=row["last_refill_at"])

    def _refill(self, state: BudgetState) -> BudgetState:
        now = datetime.now(UTC)
        elapsed = (now - state.last_refill_at).total_seconds()
        if elapsed <= 0 or self.window_seconds <= 0:
            return state
        # Linear refill: full window -> full bucket
        gained = int(elapsed * self.max_tokens / self.window_seconds)
        if gained <= 0:
            return state
        return BudgetState(
            tokens=min(self.max_tokens, state.tokens + gained),
            last_refill_at=now,
        )

    async def try_consume(self) -> bool:
        """Atomically attempt to consume one token. Returns True on success."""
        pool = await get_pool()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT tokens, last_refill_at FROM retry_budget "
                "WHERE thread_id=$1 FOR UPDATE",
                self.thread_id,
            )
            if row is None:
                now = datetime.now(UTC)
                await conn.execute(
                    "INSERT INTO retry_budget(thread_id, tokens, last_refill_at) "
                    "VALUES ($1, $2, $3)",
                    self.thread_id,
                    self.max_tokens - 1,
                    now,
                )
                return True

            state = self._refill(BudgetState(row["tokens"], row["last_refill_at"]))
            if state.tokens <= 0:
                # Persist refill timestamp even on failure
                await conn.execute(
                    "UPDATE retry_budget SET last_refill_at=$1 WHERE thread_id=$2",
                    state.last_refill_at,
                    self.thread_id,
                )
                return False
            state.tokens -= 1
            await conn.execute(
                "UPDATE retry_budget SET tokens=$1, last_refill_at=$2 "
                "WHERE thread_id=$3",
                state.tokens,
                state.last_refill_at,
                self.thread_id,
            )
            return True

    async def current(self) -> int:
        state = self._refill(await self._load())
        return state.tokens
