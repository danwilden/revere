"""
Tear sheet writer and performance reporting.

TearSheetWriter persists backtest results as:
    {name}_metrics.json   — all performance metrics
    {name}_equity.csv     — equity curve time series
    {name}_trades.csv     — individual trade log (if provided)

Usage:
    from forex_system.monitoring.reporting import TearSheetWriter

    writer = TearSheetWriter()
    metrics = writer.write(
        name="wf_lightgbm_H1_EURUSD",
        equity_curve=result.equity_curve,
        trades=result.trades_df(),
        extra_metadata={"instrument": "EUR_USD", "granularity": "H1"},
    )
    print(f"Sharpe: {metrics['sharpe']:.2f}")
"""

import json
from pathlib import Path

import pandas as pd
from loguru import logger

from forex_system.backtest.metrics import full_tearsheet
from forex_system.config import settings


class TearSheetWriter:
    """
    Writes backtest or paper-trade results to data/reports/.

    Outputs are plain files (JSON + CSV) so they can be inspected without
    any special tooling and displayed in the Streamlit run-logs page.
    """

    def __init__(self, output_dir: Path | str | None = None) -> None:
        self.output_dir = Path(output_dir) if output_dir else Path(settings.data_reports)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        name: str,
        equity_curve: pd.Series,
        trades: pd.DataFrame | None = None,
        extra_metadata: dict | None = None,
    ) -> dict:
        """
        Compute metrics and write all output files.

        Args:
            name:           Base filename (no extension). e.g. "wf_lgbm_H1_EURUSD"
            equity_curve:   Time-indexed equity Series.
            trades:         Optional DataFrame with "pnl" column (per-trade P&L).
            extra_metadata: Any extra key/values to include in the JSON report.

        Returns:
            Dict of computed metrics (same as stored in JSON).
        """
        metrics = full_tearsheet(equity_curve, trades)
        if extra_metadata:
            metrics.update(extra_metadata)

        # JSON summary
        json_path = self.output_dir / f"{name}_metrics.json"
        with open(json_path, "w") as fh:
            json.dump(metrics, fh, indent=2, default=str)

        # Equity curve CSV
        csv_path = self.output_dir / f"{name}_equity.csv"
        equity_curve.to_csv(csv_path, header=["equity"])

        # Trades CSV
        if trades is not None and not trades.empty:
            trades_path = self.output_dir / f"{name}_trades.csv"
            trades.to_csv(trades_path, index=False)
            logger.info(f"Saved trades: {trades_path.name} ({len(trades)} rows)")

        logger.info(
            f"Tear sheet saved: {json_path.name} | "
            f"sharpe={metrics.get('sharpe', 0):.2f} | "
            f"maxDD={metrics.get('max_drawdown', 0):.2%} | "
            f"CAGR={metrics.get('cagr', 0):.2%}"
        )
        return metrics

    def load_metrics(self, name: str) -> dict:
        """Load a previously saved metrics JSON by name."""
        path = self.output_dir / f"{name}_metrics.json"
        if not path.exists():
            raise FileNotFoundError(f"No metrics file: {path}")
        with open(path) as fh:
            return json.load(fh)

    def list_reports(self) -> list[str]:
        """Return list of report base names available in output_dir."""
        paths = sorted(self.output_dir.glob("*_metrics.json"))
        return [p.stem.replace("_metrics", "") for p in paths]
