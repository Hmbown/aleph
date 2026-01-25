"""Tests for Budget and BudgetStatus."""

from __future__ import annotations

from aleph.types import Budget, BudgetStatus


class TestBudgetExceeds:
    """Tests for BudgetStatus.exceeds() checks."""

    def test_tokens_exceeded(self) -> None:
        budget = Budget(max_tokens=100)
        status = BudgetStatus(tokens_used=150)
        exceeded, reason = status.exceeds(budget)
        assert exceeded is True
        assert reason is not None
        assert "Token" in reason

    def test_tokens_not_exceeded(self) -> None:
        budget = Budget(max_tokens=100)
        status = BudgetStatus(tokens_used=50)
        exceeded, reason = status.exceeds(budget)
        assert exceeded is False
        assert reason is None

    def test_tokens_at_limit(self) -> None:
        budget = Budget(max_tokens=100)
        status = BudgetStatus(tokens_used=100)
        exceeded, reason = status.exceeds(budget)
        # At limit, not exceeded
        assert exceeded is False

    def test_iterations_exceeded(self) -> None:
        budget = Budget(max_iterations=10)
        status = BudgetStatus(iterations_used=15)
        exceeded, reason = status.exceeds(budget)
        assert exceeded is True
        assert reason is not None
        assert "Iteration" in reason

    def test_depth_exceeded(self) -> None:
        budget = Budget(max_depth=2)
        status = BudgetStatus(depth_current=3)
        exceeded, reason = status.exceeds(budget)
        assert exceeded is True
        assert reason is not None
        assert "Depth" in reason

    def test_wall_time_exceeded(self) -> None:
        budget = Budget(max_wall_time_seconds=60.0)
        status = BudgetStatus(wall_time_used=120.0)
        exceeded, reason = status.exceeds(budget)
        assert exceeded is True
        assert reason is not None
        assert "Wall-time" in reason

    def test_sub_queries_exceeded(self) -> None:
        budget = Budget(max_sub_queries=10)
        status = BudgetStatus(sub_queries_used=15)
        exceeded, reason = status.exceeds(budget)
        assert exceeded is True
        assert reason is not None
        assert "Sub-query" in reason

    def test_nothing_exceeded(self) -> None:
        budget = Budget(
            max_tokens=100,
            max_iterations=50,
            max_depth=2,
            max_wall_time_seconds=300.0,
            max_sub_queries=100,
        )
        status = BudgetStatus(
            tokens_used=50,
            iterations_used=10,
            depth_current=1,
            wall_time_used=60.0,
            sub_queries_used=5,
        )
        exceeded, reason = status.exceeds(budget)
        assert exceeded is False
        assert reason is None

    def test_no_limits_set(self) -> None:
        budget = Budget(
            max_tokens=None,
            max_iterations=None,
            max_depth=None,
            max_wall_time_seconds=None,
            max_sub_queries=None,
        )
        status = BudgetStatus(
            tokens_used=999999,
            iterations_used=999999,
            depth_current=999999,
            wall_time_used=999999.0,
            sub_queries_used=999999,
        )
        exceeded, reason = status.exceeds(budget)
        assert exceeded is False


class TestBudgetDefaults:
    """Tests for Budget default values."""

    def test_default_values(self) -> None:
        budget = Budget()
        assert budget.max_tokens is None
        assert budget.max_iterations == 100
        assert budget.max_depth == 2
        assert budget.max_wall_time_seconds == 300.0
        assert budget.max_sub_queries == 100


class TestBudgetStatusDefaults:
    """Tests for BudgetStatus default values."""

    def test_default_values(self) -> None:
        status = BudgetStatus()
        assert status.tokens_used == 0
        assert status.cost_used == 0.0
        assert status.iterations_used == 0
        assert status.depth_current == 0
        assert status.wall_time_used == 0.0
        assert status.sub_queries_used == 0
