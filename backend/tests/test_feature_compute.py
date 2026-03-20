"""Unit tests for FeatureComputer class in backend/features/compute.py."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backend.features.compute import FeatureComputer
from backend.features.sandbox import SandboxError, SandboxTimeoutError, SandboxValidationError
from backend.schemas.enums import Timeframe


def _make_spec(code: str = "result = df['close']", name: str = "test_feat"):
    """Build a minimal FeatureSpec-like object for testing."""
    from backend.agents.tools.schemas import FeatureSpec
    return FeatureSpec(
        name=name,
        family="momentum",
        formula_description="test",
        lookback_bars=1,
        dependency_columns=["close"],
        transformation="identity",
        expected_intuition="test",
        leakage_risk="none",
        code=code,
    )


def _make_bars(n: int = 20):
    """Generate fake bar rows for mocking get_bars_agg."""
    return [
        {
            "instrument_id": "EUR_USD",
            "timeframe": "H4",
            "timestamp_utc": datetime(2024, 1, d, 0, 0, 0, tzinfo=timezone.utc),
            "open": 1.10,
            "high": 1.11,
            "low": 1.09,
            "close": 1.10 + d * 0.001,
            "volume": 100.0,
            "source": "test",
            "derivation_version": "1",
        }
        for d in range(1, n + 1)
    ]


class TestFeatureComputer:

    def _make_computer(self, bars=None):
        mock_repo = MagicMock()
        mock_repo.get_bars_agg.return_value = bars if bars is not None else _make_bars()
        return FeatureComputer(mock_repo), mock_repo

    def test_compute_returns_series_aligned_to_bars(self):
        """FeatureComputer.compute returns a pd.Series with DatetimeIndex."""
        computer, _ = self._make_computer()
        spec = _make_spec("result = df['close']")
        with patch("backend.features.sandbox.execute_feature_code") as mock_sandbox:
            n = 20
            ts_index = pd.date_range("2024-01-01", periods=n, freq="4h")
            mock_sandbox.return_value = pd.Series([1.0] * n, index=ts_index)
            series = computer.compute(
                spec, "EUR_USD", Timeframe.H4,
                datetime(2024, 1, 1), datetime(2024, 1, 21),
            )
        assert isinstance(series, pd.Series)
        assert len(series) == n

    def test_compute_raises_on_empty_bars(self):
        """compute raises ValueError when DuckDB returns no bars."""
        computer, _ = self._make_computer(bars=[])
        spec = _make_spec()
        with pytest.raises(ValueError, match="No bars"):
            computer.compute(
                spec, "EUR_USD", Timeframe.H4,
                datetime(2024, 1, 1), datetime(2024, 1, 21),
            )

    def test_compute_propagates_sandbox_timeout(self):
        """SandboxTimeoutError from execute_feature_code propagates out."""
        computer, _ = self._make_computer()
        spec = _make_spec()
        with patch("backend.features.sandbox.execute_feature_code") as mock_sandbox:
            mock_sandbox.side_effect = SandboxTimeoutError("timeout")
            with pytest.raises(SandboxTimeoutError):
                computer.compute(
                    spec, "EUR_USD", Timeframe.H4,
                    datetime(2024, 1, 1), datetime(2024, 1, 21),
                )

    def test_compute_propagates_sandbox_validation_error(self):
        """SandboxValidationError from execute_feature_code propagates out."""
        computer, _ = self._make_computer()
        spec = _make_spec()
        with patch("backend.features.sandbox.execute_feature_code") as mock_sandbox:
            mock_sandbox.side_effect = SandboxValidationError("forbidden import: os")
            with pytest.raises(SandboxValidationError):
                computer.compute(
                    spec, "EUR_USD", Timeframe.H4,
                    datetime(2024, 1, 1), datetime(2024, 1, 21),
                )

    def test_compute_passes_spec_code_to_sandbox(self):
        """FeatureComputer passes spec.code to execute_feature_code."""
        computer, _ = self._make_computer()
        code = "result = df['close'].rolling(5).mean()"
        spec = _make_spec(code=code)
        with patch("backend.features.sandbox.execute_feature_code") as mock_sandbox:
            n = 20
            ts_index = pd.date_range("2024-01-01", periods=n, freq="4h")
            mock_sandbox.return_value = pd.Series([1.0] * n, index=ts_index)
            computer.compute(
                spec, "EUR_USD", Timeframe.H4,
                datetime(2024, 1, 1), datetime(2024, 1, 21),
            )
        call_args = mock_sandbox.call_args
        assert call_args[0][0] == code  # first positional arg is the code string

    def test_compute_dataframe_has_ohlcv_columns(self):
        """The DataFrame passed to sandbox has open/high/low/close/volume columns."""
        computer, _ = self._make_computer()
        spec = _make_spec()
        captured_df = []
        with patch("backend.features.sandbox.execute_feature_code") as mock_sandbox:
            def capture(code, df, **kwargs):
                captured_df.append(df)
                return pd.Series(df["close"].values, index=df.index)
            mock_sandbox.side_effect = capture
            computer.compute(
                spec, "EUR_USD", Timeframe.H4,
                datetime(2024, 1, 1), datetime(2024, 1, 21),
            )
        assert len(captured_df) == 1
        df = captured_df[0]
        for col in ("open", "high", "low", "close", "volume"):
            assert col in df.columns, f"Column '{col}' missing from DataFrame"
        assert isinstance(df.index, pd.DatetimeIndex)
