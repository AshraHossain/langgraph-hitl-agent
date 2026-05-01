"""Pure logic tests for RetryBudget refill math — no DB required."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.retry import BudgetState, RetryBudget


def _make(window_s: int = 300, max_tokens: int = 5) -> RetryBudget:
    import os

    os.environ["RETRY_BUDGET_PER_THREAD"] = str(max_tokens)
    os.environ["RETRY_BUDGET_WINDOW_SECONDS"] = str(window_s)
    from app.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    return RetryBudget("unit-test-thread")


def test_refill_noop_on_same_instant() -> None:
    rb = _make(window_s=300, max_tokens=5)
    now = datetime.now(timezone.utc)
    s = rb._refill(BudgetState(tokens=3, last_refill_at=now))
    assert s.tokens == 3
    assert s.last_refill_at == now


def test_full_window_refills_to_max() -> None:
    rb = _make(window_s=60, max_tokens=5)
    past = datetime.now(timezone.utc) - timedelta(seconds=120)
    s = rb._refill(BudgetState(tokens=0, last_refill_at=past))
    assert s.tokens == 5  # capped at max


def test_partial_refill_is_linear() -> None:
    rb = _make(window_s=100, max_tokens=10)
    past = datetime.now(timezone.utc) - timedelta(seconds=30)
    s = rb._refill(BudgetState(tokens=2, last_refill_at=past))
    # Gained floor(30 * 10 / 100) = 3 tokens
    assert s.tokens == 5


def test_refill_cannot_exceed_max() -> None:
    rb = _make(window_s=100, max_tokens=10)
    past = datetime.now(timezone.utc) - timedelta(seconds=99999)
    s = rb._refill(BudgetState(tokens=10, last_refill_at=past))
    assert s.tokens == 10
