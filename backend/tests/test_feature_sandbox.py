"""Tests for backend.features.sandbox -- subprocess feature code execution."""
from __future__ import annotations

import multiprocessing
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from backend.features.sandbox import (
    SandboxError,
    SandboxTimeoutError,
    SandboxValidationError,
    _check_imports,
    execute_feature_code,
)


def _make_df(n: int = 100) -> pd.DataFrame:
    """Create a simple bar DataFrame with DatetimeIndex."""
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    rng = np.random.default_rng(42)
    close = 1.1000 + rng.standard_normal(n).cumsum() * 0.001
    return pd.DataFrame(
        {
            "open": close - 0.0001,
            "high": close + 0.0005,
            "low": close - 0.0005,
            "close": close,
            "volume": rng.integers(100, 1000, n).astype(float),
        },
        index=idx,
    )


class TestCheckImports:
    def test_allowed_imports_pass(self) -> None:
        code = "import numpy\nimport pandas\nimport np\nimport pd"
        _check_imports(code)  # should not raise

    def test_forbidden_import_raises(self) -> None:
        with pytest.raises(SandboxValidationError, match="Forbidden import: os"):
            _check_imports("import os\nresult = pd.Series([1])")

    def test_from_import_forbidden(self) -> None:
        with pytest.raises(SandboxValidationError, match="Forbidden import: subprocess"):
            _check_imports("from subprocess import run")

    def test_indented_import_forbidden(self) -> None:
        with pytest.raises(SandboxValidationError, match="Forbidden import: sys"):
            _check_imports("  import sys")


class TestExecuteFeatureCode:
    def test_valid_code_returns_series(self) -> None:
        df = _make_df(50)
        code = "result = df['close'] - df['open']"
        series = execute_feature_code(code, df, timeout_seconds=10.0)
        assert isinstance(series, pd.Series)
        assert len(series) == len(df)
        assert series.index.equals(df.index)

    def test_rolling_feature(self) -> None:
        df = _make_df(50)
        code = "result = df['close'].rolling(5).mean().bfill()"
        series = execute_feature_code(code, df, timeout_seconds=10.0)
        assert isinstance(series, pd.Series)
        assert len(series) == 50

    def test_forbidden_import_before_spawn(self) -> None:
        """Forbidden imports should raise BEFORE spawning a child process."""
        df = _make_df(10)
        with pytest.raises(SandboxValidationError, match="Forbidden import: os"):
            execute_feature_code("import os\nresult = pd.Series([1]*len(df))", df)

    def test_wrong_result_type_raises(self) -> None:
        """Code that assigns a dict to result should raise SandboxValidationError."""
        df = _make_df(10)
        code = "result = {'a': 1}"
        with pytest.raises(SandboxValidationError, match="must assign a pd.Series"):
            execute_feature_code(code, df, timeout_seconds=10.0)

    def test_wrong_length_raises(self) -> None:
        """Series with wrong length should raise SandboxValidationError."""
        df = _make_df(10)
        code = "result = pd.Series([1.0, 2.0, 3.0])"
        with pytest.raises(SandboxValidationError, match="result length"):
            execute_feature_code(code, df, timeout_seconds=10.0)

    def test_timeout_raises(self) -> None:
        """Code that runs too long should raise SandboxTimeoutError."""
        df = _make_df(10)
        code = "import time\ntime.sleep(30)\nresult = df['close']"
        # time is not in allowed imports, so this will be caught by import check
        # Use a code that loops instead:
        with pytest.raises(SandboxTimeoutError):
            # Bypass import check for this test by patching
            with patch("backend.features.sandbox._check_imports"):
                execute_feature_code(
                    "import time\ntime.sleep(30)\nresult = df['close']",
                    df,
                    timeout_seconds=0.5,
                )

    def test_nan_ratio_above_threshold_raises(self) -> None:
        """Series with >20% NaN should raise SandboxValidationError."""
        df = _make_df(10)
        # Create a Series that's mostly NaN
        code = (
            "import numpy as np\n"
            "s = pd.Series(np.nan, index=df.index)\n"
            "s.iloc[0] = 1.0\n"
            "s.iloc[1] = 2.0\n"
            "result = s"
        )
        with pytest.raises(SandboxValidationError, match="NaN values"):
            execute_feature_code(code, df, timeout_seconds=10.0)

    def test_nan_ratio_at_threshold_passes(self) -> None:
        """Series with exactly 20% NaN should pass (threshold is strict >)."""
        df = _make_df(10)
        # 2 NaN out of 10 = 20% exactly
        code = (
            "import numpy as np\n"
            "s = pd.Series(range(len(df)), index=df.index, dtype=float)\n"
            "s.iloc[0] = np.nan\n"
            "s.iloc[1] = np.nan\n"
            "result = s"
        )
        series = execute_feature_code(code, df, timeout_seconds=10.0)
        assert len(series) == 10

    def test_code_exception_raises_validation_error(self) -> None:
        """Runtime errors in user code should raise SandboxValidationError."""
        df = _make_df(10)
        code = "result = 1 / 0"
        with pytest.raises(SandboxValidationError, match="division by zero"):
            execute_feature_code(code, df, timeout_seconds=10.0)

    def test_no_result_variable_raises(self) -> None:
        """Code that does not assign 'result' should raise."""
        df = _make_df(10)
        code = "x = df['close'] * 2"
        with pytest.raises(SandboxValidationError, match="must assign a pd.Series"):
            execute_feature_code(code, df, timeout_seconds=10.0)
