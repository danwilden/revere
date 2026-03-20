"""Tests for Phase 5F robustness battery job runner.

Test gate logic and helper functions. Uses minimal fixtures for determinism.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.jobs.robustness_battery import _apply_gates
from backend.schemas.requests import (
    CostStressResult,
    CostStressVariant,
    HoldoutResult,
    ParamSensitivityResult,
    ParamSensitivityStep,
    WalkForwardResult,
    WalkForwardWindow,
)


# ============================================================================
# Fixture builders for gate testing
# ============================================================================

def _make_holdout(
    net_return_pct: float | None = 5.0,
    sharpe_ratio: float | None = 0.8,
    max_drawdown_pct: float | None = -5.0,
    trade_count: int = 20,
    passed: bool = True,
    block_reason: str | None = None,
) -> HoldoutResult:
    """Build a HoldoutResult fixture."""
    base_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return HoldoutResult(
        backtest_run_id="hold-run-1",
        test_start=base_dt + timedelta(days=80),
        test_end=base_dt + timedelta(days=100),
        net_return_pct=net_return_pct,
        sharpe_ratio=sharpe_ratio,
        max_drawdown_pct=max_drawdown_pct,
        trade_count=trade_count,
        passed=passed,
        block_reason=block_reason,
    )


def _make_walk_forward(
    windows_passed: int = 3,
    windows_total: int = 5,
    passed: bool = True,
    block_reason: str | None = None,
) -> WalkForwardResult:
    """Build a WalkForwardResult fixture with windows."""
    windows = []
    base_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(windows_total):
        windows.append(WalkForwardWindow(
            window_index=i,
            train_start=base_dt + timedelta(days=i*10),
            train_end=base_dt + timedelta(days=i*10+6),
            test_start=base_dt + timedelta(days=i*10+6),
            test_end=base_dt + timedelta(days=i*10+9),
            backtest_run_id=f"wf-run-{i}",
            net_return_pct=2.0 if i < windows_passed else -0.5,
            sharpe_ratio=0.5 if i < windows_passed else None,
            trade_count=15 if i < windows_passed else 0,
            passed=i < windows_passed,
        ))
    return WalkForwardResult(
        windows=windows,
        windows_passed=windows_passed,
        windows_total=windows_total,
        passed=passed,
        block_reason=block_reason,
    )


def _make_cost_stress(
    passed: bool = True,
    block_reason: str | None = None,
) -> CostStressResult:
    """Build a CostStressResult fixture."""
    variants = [
        CostStressVariant(
            multiplier=2.0,
            backtest_run_id="cost-2x",
            net_return_pct=2.0 if passed else -0.5,
            sharpe_ratio=0.5 if passed else None,
            passed=passed,
        ),
        CostStressVariant(
            multiplier=3.0,
            backtest_run_id="cost-3x",
            net_return_pct=1.0 if passed else -1.0,
            sharpe_ratio=0.3 if passed else None,
            passed=passed,
        ),
    ]
    return CostStressResult(
        variants=variants,
        passed=passed,
        block_reason=block_reason,
    )


def _make_param_sensitivity(
    return_range_pct: float = 10.0,
    base_net_return_pct: float = 50.0,
    passed: bool = True,
    block_reason: str | None = None,
) -> ParamSensitivityResult:
    """Build a ParamSensitivityResult fixture."""
    steps = []
    for mult in [0.8, 0.9, 1.0, 1.1, 1.2]:
        if mult == 1.0:
            ret = base_net_return_pct
        elif mult < 1.0:
            ret = base_net_return_pct - (return_range_pct / 2)
        else:
            ret = base_net_return_pct + (return_range_pct / 2)
        steps.append(ParamSensitivityStep(
            param_name="combined_atr",
            param_value=mult,
            backtest_run_id=f"param-{mult}",
            net_return_pct=ret if passed or mult == 1.0 else None,
            sharpe_ratio=0.5 if passed or mult == 1.0 else None,
        ))
    return ParamSensitivityResult(
        steps=steps,
        return_range_pct=return_range_pct,
        base_net_return_pct=base_net_return_pct,
        passed=passed,
        block_reason=block_reason,
    )


# ============================================================================
# Tests: _apply_gates
# ============================================================================

class TestApplyGates:
    def test_all_pass_promoted_true(self):
        """All gates passing → promoted=True, no block_reasons."""
        holdout = _make_holdout(passed=True)
        walk_forward = _make_walk_forward(passed=True)
        cost_stress = _make_cost_stress(passed=True)
        param_sensitivity = _make_param_sensitivity(passed=True)

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is True
        assert block_reasons == []

    def test_holdout_fails_blocks(self):
        """Holdout gate failing → promoted=False, block_reason added."""
        holdout = _make_holdout(passed=False, block_reason="holdout_negative_return")
        walk_forward = _make_walk_forward(passed=True)
        cost_stress = _make_cost_stress(passed=True)
        param_sensitivity = _make_param_sensitivity(passed=True)

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is False
        assert "holdout_negative_return" in block_reasons

    def test_walk_forward_fails_blocks(self):
        """Walk-forward gate failing → promoted=False."""
        holdout = _make_holdout(passed=True)
        walk_forward = _make_walk_forward(passed=False, block_reason="walk_forward_majority_failed")
        cost_stress = _make_cost_stress(passed=True)
        param_sensitivity = _make_param_sensitivity(passed=True)

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is False
        assert "walk_forward_majority_failed" in block_reasons

    def test_cost_stress_fails_blocks(self):
        """Cost stress gate failing → promoted=False."""
        holdout = _make_holdout(passed=True)
        walk_forward = _make_walk_forward(passed=True)
        cost_stress = _make_cost_stress(passed=False, block_reason="cost_stress_negative_return")
        param_sensitivity = _make_param_sensitivity(passed=True)

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is False
        assert "cost_stress_negative_return" in block_reasons

    def test_param_sensitivity_fails_blocks(self):
        """Parameter sensitivity gate failing → promoted=False."""
        holdout = _make_holdout(passed=True)
        walk_forward = _make_walk_forward(passed=True)
        cost_stress = _make_cost_stress(passed=True)
        param_sensitivity = _make_param_sensitivity(passed=False, block_reason="param_sensitivity_too_wide")

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is False
        assert "param_sensitivity_too_wide" in block_reasons

    def test_multiple_gates_fail_collects_all_reasons(self):
        """Multiple failing gates → all block_reasons collected."""
        holdout = _make_holdout(passed=False, block_reason="holdout_zero_trades")
        walk_forward = _make_walk_forward(passed=False, block_reason="walk_forward_majority_failed")
        cost_stress = _make_cost_stress(passed=False, block_reason="cost_stress_negative_return")
        param_sensitivity = _make_param_sensitivity(passed=True)

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is False
        assert len(block_reasons) == 3
        assert "holdout_zero_trades" in block_reasons
        assert "walk_forward_majority_failed" in block_reasons
        assert "cost_stress_negative_return" in block_reasons

    def test_holdout_net_return_negative_blocks(self):
        """Holdout with negative net_return_pct fails."""
        holdout = _make_holdout(
            net_return_pct=-0.01,
            passed=False,
            block_reason="holdout_negative_return"
        )
        walk_forward = _make_walk_forward(passed=True)
        cost_stress = _make_cost_stress(passed=True)
        param_sensitivity = _make_param_sensitivity(passed=True)

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is False
        assert "holdout_negative_return" in block_reasons

    def test_walk_forward_not_majority_blocks(self):
        """Walk-forward with 2/5 windows passing (not majority) fails."""
        walk_forward = _make_walk_forward(
            windows_passed=2,
            windows_total=5,
            passed=False,
            block_reason="walk_forward_majority_failed"
        )
        holdout = _make_holdout(passed=True)
        cost_stress = _make_cost_stress(passed=True)
        param_sensitivity = _make_param_sensitivity(passed=True)

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is False
        assert "walk_forward_majority_failed" in block_reasons

    def test_walk_forward_exactly_majority_passes(self):
        """Walk-forward with exactly 3/5 windows passing meets majority threshold."""
        walk_forward = _make_walk_forward(
            windows_passed=3,
            windows_total=5,
            passed=True,
            block_reason=None
        )
        holdout = _make_holdout(passed=True)
        cost_stress = _make_cost_stress(passed=True)
        param_sensitivity = _make_param_sensitivity(passed=True)

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is True

    def test_cost_stress_2x_negative_blocks(self):
        """Cost stress with 2× multiplier returning negative fails."""
        cost_stress = _make_cost_stress(
            passed=False,
            block_reason="cost_stress_negative_return"
        )
        holdout = _make_holdout(passed=True)
        walk_forward = _make_walk_forward(passed=True)
        param_sensitivity = _make_param_sensitivity(passed=True)

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is False
        assert "cost_stress_negative_return" in block_reasons

    def test_param_sensitivity_range_too_wide_blocks(self):
        """Parameter sensitivity with 60% range (> 50% threshold) blocks."""
        param_sensitivity = _make_param_sensitivity(
            return_range_pct=60.0,
            base_net_return_pct=100.0,
            passed=False,
            block_reason="param_sensitivity_too_wide"
        )
        holdout = _make_holdout(passed=True)
        walk_forward = _make_walk_forward(passed=True)
        cost_stress = _make_cost_stress(passed=True)

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is False
        assert "param_sensitivity_too_wide" in block_reasons

    def test_param_sensitivity_range_acceptable(self):
        """Parameter sensitivity with 49% range (< 50% threshold) passes."""
        param_sensitivity = _make_param_sensitivity(
            return_range_pct=49.0,
            base_net_return_pct=100.0,
            passed=True,
            block_reason=None
        )
        holdout = _make_holdout(passed=True)
        walk_forward = _make_walk_forward(passed=True)
        cost_stress = _make_cost_stress(passed=True)

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is True

    def test_param_sensitivity_base_return_zero_blocks(self):
        """Parameter sensitivity with base_return_pct=0.0 blocks."""
        param_sensitivity = _make_param_sensitivity(
            base_net_return_pct=0.0,
            passed=False,
            block_reason="base_return_zero_or_missing"
        )
        holdout = _make_holdout(passed=True)
        walk_forward = _make_walk_forward(passed=True)
        cost_stress = _make_cost_stress(passed=True)

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is False
        assert "base_return_zero_or_missing" in block_reasons

    def test_all_four_gates_fail_collects_four_reasons(self):
        """All four gates fail → all block_reasons collected."""
        holdout = _make_holdout(passed=False, block_reason="holdout_reason")
        walk_forward = _make_walk_forward(passed=False, block_reason="walk_reason")
        cost_stress = _make_cost_stress(passed=False, block_reason="cost_reason")
        param_sensitivity = _make_param_sensitivity(passed=False, block_reason="param_reason")

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)
        assert promoted is False
        assert len(block_reasons) == 4
        assert set(block_reasons) == {"holdout_reason", "walk_reason", "cost_reason", "param_reason"}
