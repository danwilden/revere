"""Robustness battery job runner (Phase 5F).

Orchestrates a battery of child backtests to validate a strategy before
promotion:
  1. Holdout backtest (80/20 bar-count split)
  2. Walk-forward windows (up to 5, in-sample only)
  3. Cost stress tests (2x and 3x multipliers)
  4. Parameter sensitivity grid (stop/take-profit ATR multipliers)

Gate logic is hard-coded Python -- no LLM involvement.  A single variant
failure never aborts the battery; it records passed=False and continues.
"""
from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.data.repositories import ArtifactRepository, MarketDataRepository, MetadataRepository
from backend.jobs.backtest import run_backtest_job
from backend.jobs.status import JobManager
from backend.schemas.enums import JobType, Timeframe
from backend.schemas.requests import (
    CostStressResult,
    CostStressVariant,
    HoldoutResult,
    ParamSensitivityResult,
    ParamSensitivityStep,
    RobustnessResult,
    WalkForwardResult,
    WalkForwardWindow,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal context dataclass
# ---------------------------------------------------------------------------

@dataclass
class _ExperimentContext:
    experiment: Any  # ExperimentRecord from lab.experiment_registry
    strategy_id: str
    strategy_record: dict
    definition_json: dict
    base_backtest_run: dict
    cost_model_params: dict
    instrument: str
    timeframe: Timeframe
    test_start: datetime
    test_end: datetime
    holdout_boundary: datetime
    in_sample_bars: list[dict]
    feature_run_id: str | None
    model_id: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bars_per_month(timeframe: Timeframe) -> int:
    return {
        Timeframe.H1: 1080,
        Timeframe.H4: 270,
        Timeframe.D: 90,
        Timeframe.M1: 43200,
    }.get(timeframe, 1080)


def _parse_dt(value: Any) -> datetime:
    """Coerce a string or datetime to a tz-naive datetime."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)


def _extract_metric(metrics: list[dict], metric_name: str) -> float | None:
    """Pull an overall metric value from a backtest run's performance metrics."""
    for m in metrics:
        if m.get("metric_name") == metric_name and m.get("segment_type") == "overall":
            return m.get("metric_value")
    return None


def _create_child_job(job_manager: JobManager, label: str, params: dict) -> str:
    """Create a child BACKTEST job and return its id."""
    child = job_manager.create(
        job_type=JobType.BACKTEST,
        params={**params, "_battery_label": label},
    )
    return child.id


def _run_child_backtest(
    *,
    label: str,
    strategy_id: str | None,
    inline_strategy: dict | None,
    instrument: str,
    timeframe: Timeframe,
    test_start: datetime,
    test_end: datetime,
    cost_model_params: dict,
    metadata_repo: MetadataRepository,
    market_repo: MarketDataRepository,
    artifact_repo: ArtifactRepository,
    job_manager: JobManager,
    feature_run_id: str | None = None,
    model_id: str | None = None,
) -> tuple[str, dict | None, list[dict]]:
    """Run a single child backtest and return (run_id, run_dict, metrics).

    On failure returns (child_job_id, None, []).
    """
    child_job_id = _create_child_job(job_manager, label, {
        "instrument": instrument,
        "timeframe": timeframe.value,
        "test_start": test_start.isoformat(),
        "test_end": test_end.isoformat(),
    })
    try:
        run_id = run_backtest_job(
            job_id=child_job_id,
            strategy_id=strategy_id,
            inline_strategy=inline_strategy,
            instrument=instrument,
            timeframe=timeframe,
            test_start=test_start,
            test_end=test_end,
            cost_model_params=cost_model_params,
            metadata_repo=metadata_repo,
            market_repo=market_repo,
            artifact_repo=artifact_repo,
            job_manager=job_manager,
            feature_run_id=feature_run_id,
            model_id=model_id,
        )
        run_dict = metadata_repo.get_backtest_run(run_id)
        metrics = metadata_repo.get_performance_metrics(run_id)
        return run_id, run_dict, metrics
    except Exception as exc:
        logger.warning("Child backtest %s (%s) failed: %s", child_job_id, label, exc)
        return child_job_id, None, []


# ---------------------------------------------------------------------------
# Battery sections
# ---------------------------------------------------------------------------

def _run_holdout(
    ctx: _ExperimentContext,
    metadata_repo: MetadataRepository,
    market_repo: MarketDataRepository,
    artifact_repo: ArtifactRepository,
    job_manager: JobManager,
) -> HoldoutResult:
    """Run a backtest on the holdout portion [holdout_boundary, test_end)."""
    run_id, run_dict, metrics = _run_child_backtest(
        label="holdout",
        strategy_id=ctx.strategy_id,
        inline_strategy=None,
        instrument=ctx.instrument,
        timeframe=ctx.timeframe,
        test_start=ctx.holdout_boundary,
        test_end=ctx.test_end,
        cost_model_params=ctx.cost_model_params,
        metadata_repo=metadata_repo,
        market_repo=market_repo,
        artifact_repo=artifact_repo,
        job_manager=job_manager,
        feature_run_id=ctx.feature_run_id,
        model_id=ctx.model_id,
    )

    if run_dict is None:
        return HoldoutResult(
            backtest_run_id=run_id,
            test_start=ctx.holdout_boundary,
            test_end=ctx.test_end,
            net_return_pct=None,
            sharpe_ratio=None,
            max_drawdown_pct=None,
            trade_count=0,
            passed=False,
            block_reason="holdout_backtest_failed",
        )

    net_ret = _extract_metric(metrics, "net_return_pct")
    sharpe = _extract_metric(metrics, "sharpe_ratio")
    max_dd = _extract_metric(metrics, "max_drawdown_pct")
    trade_count = int(_extract_metric(metrics, "total_trades") or 0)

    passed = net_ret is not None and net_ret > 0.0 and trade_count > 0
    block_reason = None
    if not passed:
        if trade_count == 0:
            block_reason = "holdout_zero_trades"
        elif net_ret is not None and net_ret <= 0.0:
            block_reason = "holdout_negative_return"
        else:
            block_reason = "holdout_no_return_metric"

    return HoldoutResult(
        backtest_run_id=run_id,
        test_start=ctx.holdout_boundary,
        test_end=ctx.test_end,
        net_return_pct=net_ret,
        sharpe_ratio=sharpe,
        max_drawdown_pct=max_dd,
        trade_count=trade_count,
        passed=passed,
        block_reason=block_reason,
    )


def _run_walk_forward(
    ctx: _ExperimentContext,
    metadata_repo: MetadataRepository,
    market_repo: MarketDataRepository,
    artifact_repo: ArtifactRepository,
    job_manager: JobManager,
) -> WalkForwardResult:
    """Run up to 5 walk-forward windows over in-sample bars only."""
    bpm = _bars_per_month(ctx.timeframe)
    train_bars = 6 * bpm
    test_bars = 3 * bpm
    total_in_sample = len(ctx.in_sample_bars)

    # Build window specs
    windows: list[WalkForwardWindow] = []
    window_start = 0

    for i in range(5):
        train_end_idx = window_start + train_bars
        test_start_idx = train_end_idx
        test_end_idx = test_start_idx + test_bars

        if test_end_idx > total_in_sample:
            # Partial last window: use remaining bars as test if we have
            # enough for at least the training portion
            if test_start_idx < total_in_sample and train_end_idx <= total_in_sample:
                test_end_idx = total_in_sample
            else:
                break

        if train_end_idx > total_in_sample:
            break

        train_start_ts = ctx.in_sample_bars[window_start]["timestamp_utc"]
        train_end_ts = ctx.in_sample_bars[train_end_idx - 1]["timestamp_utc"]
        test_start_ts = ctx.in_sample_bars[test_start_idx]["timestamp_utc"]
        test_end_ts = ctx.in_sample_bars[min(test_end_idx, total_in_sample) - 1]["timestamp_utc"]

        run_id, run_dict, metrics = _run_child_backtest(
            label=f"walk_forward_{i}",
            strategy_id=ctx.strategy_id,
            inline_strategy=None,
            instrument=ctx.instrument,
            timeframe=ctx.timeframe,
            test_start=_parse_dt(test_start_ts),
            test_end=_parse_dt(test_end_ts),
            cost_model_params=ctx.cost_model_params,
            metadata_repo=metadata_repo,
            market_repo=market_repo,
            artifact_repo=artifact_repo,
            job_manager=job_manager,
            feature_run_id=ctx.feature_run_id,
            model_id=ctx.model_id,
        )

        if run_dict is None:
            windows.append(WalkForwardWindow(
                window_index=i,
                train_start=_parse_dt(train_start_ts),
                train_end=_parse_dt(train_end_ts),
                test_start=_parse_dt(test_start_ts),
                test_end=_parse_dt(test_end_ts),
                backtest_run_id=run_id,
                net_return_pct=None,
                sharpe_ratio=None,
                trade_count=0,
                passed=False,
            ))
        else:
            net_ret = _extract_metric(metrics, "net_return_pct")
            sharpe = _extract_metric(metrics, "sharpe_ratio")
            trade_count = int(_extract_metric(metrics, "total_trades") or 0)
            passed = net_ret is not None and net_ret > 0.0 and trade_count > 0
            windows.append(WalkForwardWindow(
                window_index=i,
                train_start=_parse_dt(train_start_ts),
                train_end=_parse_dt(train_end_ts),
                test_start=_parse_dt(test_start_ts),
                test_end=_parse_dt(test_end_ts),
                backtest_run_id=run_id,
                net_return_pct=net_ret,
                sharpe_ratio=sharpe,
                trade_count=trade_count,
                passed=passed,
            ))

        window_start += test_bars

    if len(windows) == 0:
        return WalkForwardResult(
            windows=[],
            windows_passed=0,
            windows_total=0,
            passed=False,
            block_reason="insufficient_data_for_walk_forward",
        )

    windows_passed = sum(1 for w in windows if w.passed)
    # Require majority of windows to pass
    overall_passed = windows_passed > len(windows) / 2
    block_reason = None if overall_passed else "walk_forward_majority_failed"

    return WalkForwardResult(
        windows=windows,
        windows_passed=windows_passed,
        windows_total=len(windows),
        passed=overall_passed,
        block_reason=block_reason,
    )


def _run_cost_stress(
    ctx: _ExperimentContext,
    metadata_repo: MetadataRepository,
    market_repo: MarketDataRepository,
    artifact_repo: ArtifactRepository,
    job_manager: JobManager,
) -> CostStressResult:
    """Run backtests with 2x and 3x cost multipliers over the full date range."""
    variants: list[CostStressVariant] = []
    all_passed = True

    for multiplier in [2.0, 3.0]:
        stressed_params = {
            "spread_pips": ctx.cost_model_params.get("spread_pips", 2.0) * multiplier,
            "slippage_pips": ctx.cost_model_params.get("slippage_pips", 0.5) * multiplier,
            "commission_per_unit": ctx.cost_model_params.get("commission_per_unit", 0.0) * multiplier,
            "pip_size": ctx.cost_model_params.get("pip_size", 0.0001),  # NOT multiplied
        }

        run_id, run_dict, metrics = _run_child_backtest(
            label=f"cost_stress_{multiplier:.0f}x",
            strategy_id=ctx.strategy_id,
            inline_strategy=None,
            instrument=ctx.instrument,
            timeframe=ctx.timeframe,
            test_start=ctx.test_start,
            test_end=ctx.test_end,
            cost_model_params=stressed_params,
            metadata_repo=metadata_repo,
            market_repo=market_repo,
            artifact_repo=artifact_repo,
            job_manager=job_manager,
            feature_run_id=ctx.feature_run_id,
            model_id=ctx.model_id,
        )

        if run_dict is None:
            variants.append(CostStressVariant(
                multiplier=multiplier,
                backtest_run_id=run_id,
                net_return_pct=None,
                sharpe_ratio=None,
                passed=False,
            ))
            all_passed = False
        else:
            net_ret = _extract_metric(metrics, "net_return_pct")
            sharpe = _extract_metric(metrics, "sharpe_ratio")
            passed = net_ret is not None and net_ret > 0.0
            if not passed:
                all_passed = False
            variants.append(CostStressVariant(
                multiplier=multiplier,
                backtest_run_id=run_id,
                net_return_pct=net_ret,
                sharpe_ratio=sharpe,
                passed=passed,
            ))

    block_reason = None if all_passed else "cost_stress_negative_return"
    return CostStressResult(
        variants=variants,
        passed=all_passed,
        block_reason=block_reason,
    )


def _run_param_sensitivity(
    ctx: _ExperimentContext,
    metadata_repo: MetadataRepository,
    market_repo: MarketDataRepository,
    artifact_repo: ArtifactRepository,
    job_manager: JobManager,
) -> ParamSensitivityResult:
    """Test parameter sensitivity by perturbing stop/take-profit ATR multipliers."""
    defn = ctx.definition_json
    base_stop = defn.get("stop_atr_multiplier")
    base_tp = defn.get("take_profit_atr_multiplier")

    # Skip if neither parameter is present
    if base_stop is None and base_tp is None:
        return ParamSensitivityResult(
            steps=[],
            return_range_pct=0.0,
            base_net_return_pct=0.0,
            passed=False,
            block_reason="no_atr_params_to_perturb",
        )

    # Get base net return from the experiment's original backtest
    base_run_id = ctx.base_backtest_run.get("id", "")
    base_metrics = metadata_repo.get_performance_metrics(base_run_id)
    base_net_return_pct = _extract_metric(base_metrics, "net_return_pct")

    if base_net_return_pct is None or base_net_return_pct == 0.0:
        return ParamSensitivityResult(
            steps=[],
            return_range_pct=0.0,
            base_net_return_pct=base_net_return_pct or 0.0,
            passed=False,
            block_reason="base_return_zero_or_missing",
        )

    multipliers = [0.8, 0.9, 1.0, 1.1, 1.2]
    steps: list[ParamSensitivityStep] = []

    for mult in multipliers:
        perturbed_defn = copy.deepcopy(defn)
        skip = False

        if base_stop is not None:
            new_stop = max(0.5, min(6.0, base_stop * mult))
            perturbed_defn["stop_atr_multiplier"] = new_stop
        if base_tp is not None:
            new_tp = max(0.5, min(8.0, base_tp * mult))
            perturbed_defn["take_profit_atr_multiplier"] = new_tp

        # Check feasibility: stop >= take_profit is infeasible
        eff_stop = perturbed_defn.get("stop_atr_multiplier")
        eff_tp = perturbed_defn.get("take_profit_atr_multiplier")
        if eff_stop is not None and eff_tp is not None and eff_stop >= eff_tp:
            skip = True

        if skip:
            # Record infeasible combo but still need a backtest_run_id placeholder
            steps.append(ParamSensitivityStep(
                param_name="stop_atr_multiplier" if base_stop is not None else "take_profit_atr_multiplier",
                param_value=mult,
                backtest_run_id="infeasible",
                net_return_pct=None,
                sharpe_ratio=None,
            ))
            continue

        run_id, run_dict, metrics = _run_child_backtest(
            label=f"param_sensitivity_{mult:.1f}",
            strategy_id=None,
            inline_strategy=perturbed_defn,
            instrument=ctx.instrument,
            timeframe=ctx.timeframe,
            test_start=ctx.test_start,
            test_end=ctx.test_end,
            cost_model_params=ctx.cost_model_params,
            metadata_repo=metadata_repo,
            market_repo=market_repo,
            artifact_repo=artifact_repo,
            job_manager=job_manager,
            feature_run_id=ctx.feature_run_id,
            model_id=ctx.model_id,
        )

        if run_dict is None:
            steps.append(ParamSensitivityStep(
                param_name="combined_atr",
                param_value=mult,
                backtest_run_id=run_id,
                net_return_pct=None,
                sharpe_ratio=None,
            ))
        else:
            net_ret = _extract_metric(metrics, "net_return_pct")
            sharpe = _extract_metric(metrics, "sharpe_ratio")
            steps.append(ParamSensitivityStep(
                param_name="combined_atr",
                param_value=mult,
                backtest_run_id=run_id,
                net_return_pct=net_ret,
                sharpe_ratio=sharpe,
            ))

    valid_returns = [s.net_return_pct for s in steps if s.net_return_pct is not None]
    if len(valid_returns) < 2:
        return ParamSensitivityResult(
            steps=steps,
            return_range_pct=0.0,
            base_net_return_pct=base_net_return_pct,
            passed=False,
            block_reason="insufficient_valid_param_variants",
        )

    return_range_pct = max(valid_returns) - min(valid_returns)
    passed = return_range_pct <= 0.50 * abs(base_net_return_pct)
    block_reason = None if passed else "param_sensitivity_too_wide"

    return ParamSensitivityResult(
        steps=steps,
        return_range_pct=return_range_pct,
        base_net_return_pct=base_net_return_pct,
        passed=passed,
        block_reason=block_reason,
    )


# ---------------------------------------------------------------------------
# Gate logic
# ---------------------------------------------------------------------------

def _apply_gates(
    holdout: HoldoutResult,
    walk_forward: WalkForwardResult,
    cost_stress: CostStressResult,
    param_sensitivity: ParamSensitivityResult,
) -> tuple[bool, list[str]]:
    block_reasons: list[str] = []
    if holdout.block_reason:
        block_reasons.append(holdout.block_reason)
    if walk_forward.block_reason:
        block_reasons.append(walk_forward.block_reason)
    if cost_stress.block_reason:
        block_reasons.append(cost_stress.block_reason)
    if param_sensitivity.block_reason:
        block_reasons.append(param_sensitivity.block_reason)
    promoted = len(block_reasons) == 0
    return promoted, block_reasons


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_robustness_battery_job(
    job_id: str,
    experiment_id: str,
    metadata_repo: MetadataRepository,
    market_repo: MarketDataRepository,
    artifact_repo: ArtifactRepository,
    job_manager: JobManager,
) -> str:
    """Execute the full robustness battery for an experiment.

    Returns the artifact key where the RobustnessResult is persisted.
    Calls job_manager.fail() and re-raises on unhandled errors.
    """
    try:
        # ------------------------------------------------------------------
        # Stage 1: Load context
        # ------------------------------------------------------------------
        job_manager.start(job_id, stage_label="loading_context")

        from backend.deps import get_experiment_registry
        experiment_registry = get_experiment_registry()
        experiment = experiment_registry.get(experiment_id)

        # Load strategy
        strategy_id = experiment.strategy_id
        if not strategy_id:
            raise ValueError(f"Experiment '{experiment_id}' has no strategy_id")
        strategy_record = metadata_repo.get_strategy(strategy_id)
        if strategy_record is None:
            raise ValueError(f"Strategy '{strategy_id}' not found")
        definition_json = strategy_record.get("definition_json", {})

        # Load base backtest run
        backtest_run_id = experiment.backtest_run_id
        if not backtest_run_id:
            raise ValueError(f"Experiment '{experiment_id}' has no backtest_run_id")
        base_backtest_run = metadata_repo.get_backtest_run(backtest_run_id)
        if base_backtest_run is None:
            raise ValueError(f"Backtest run '{backtest_run_id}' not found")

        cost_model_params = base_backtest_run.get("cost_model_json", {})
        instrument = experiment.instrument
        timeframe = Timeframe(experiment.timeframe)
        test_start = _parse_dt(experiment.test_start)
        test_end = _parse_dt(experiment.test_end)

        # Load all bars for the full date range
        bars = market_repo.get_bars_agg(instrument, timeframe, test_start, test_end)
        if not bars:
            raise ValueError(
                f"No bars found for {instrument} {timeframe.value} "
                f"[{test_start.isoformat()}, {test_end.isoformat()})"
            )

        # 80/20 bar-count split
        split_index = int(len(bars) * 0.80)
        if split_index == 0 or split_index >= len(bars):
            raise ValueError(
                f"Cannot split {len(bars)} bars into 80/20 holdout "
                f"(split_index={split_index})"
            )
        holdout_boundary = _parse_dt(bars[split_index]["timestamp_utc"])
        in_sample_bars = bars[:split_index]

        ctx = _ExperimentContext(
            experiment=experiment,
            strategy_id=strategy_id,
            strategy_record=strategy_record,
            definition_json=definition_json,
            base_backtest_run=base_backtest_run,
            cost_model_params=cost_model_params,
            instrument=instrument,
            timeframe=timeframe,
            test_start=test_start,
            test_end=test_end,
            holdout_boundary=holdout_boundary,
            in_sample_bars=in_sample_bars,
            feature_run_id=experiment.feature_run_id,
            model_id=experiment.model_id,
        )

        # ------------------------------------------------------------------
        # Stage 2: Holdout
        # ------------------------------------------------------------------
        job_manager.progress(job_id, 10.0, "running_holdout")
        holdout = _run_holdout(ctx, metadata_repo, market_repo, artifact_repo, job_manager)

        # ------------------------------------------------------------------
        # Stage 3: Walk-forward
        # ------------------------------------------------------------------
        job_manager.progress(job_id, 30.0, "running_walk_forward")
        walk_forward = _run_walk_forward(ctx, metadata_repo, market_repo, artifact_repo, job_manager)

        # ------------------------------------------------------------------
        # Stage 4: Cost stress
        # ------------------------------------------------------------------
        job_manager.progress(job_id, 60.0, "running_cost_stress")
        cost_stress = _run_cost_stress(ctx, metadata_repo, market_repo, artifact_repo, job_manager)

        # ------------------------------------------------------------------
        # Stage 5: Parameter sensitivity
        # ------------------------------------------------------------------
        job_manager.progress(job_id, 75.0, "running_param_sensitivity")
        param_sensitivity = _run_param_sensitivity(ctx, metadata_repo, market_repo, artifact_repo, job_manager)

        # ------------------------------------------------------------------
        # Stage 6: Apply gates and persist
        # ------------------------------------------------------------------
        job_manager.progress(job_id, 95.0, "persisting_result")

        promoted, block_reasons = _apply_gates(holdout, walk_forward, cost_stress, param_sensitivity)

        result = RobustnessResult(
            experiment_id=experiment_id,
            battery_job_id=job_id,
            computed_at=datetime.utcnow(),
            holdout=holdout,
            walk_forward=walk_forward,
            cost_stress=cost_stress,
            param_sensitivity=param_sensitivity,
            promoted=promoted,
            block_reasons=block_reasons,
        )

        artifact_key = f"robustness/{experiment_id}/battery_{job_id}.json"
        artifact_repo.save(
            artifact_key,
            json.dumps(result.model_dump(mode="json"), default=str).encode(),
        )

        job_manager.succeed(job_id, result_ref=artifact_key)
        return artifact_key

    except Exception as exc:
        job_manager.fail(
            job_id=job_id,
            error_message=str(exc),
            error_code="ROBUSTNESS_BATTERY_ERROR",
        )
        raise
