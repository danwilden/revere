"""Feature code sandbox -- subprocess isolation with import whitelist.

Executes LLM-generated feature code in a child process via multiprocessing.Process.
The child receives a pickled DataFrame, runs the code, and sends back a pickled Series
through a Pipe. A hard timeout kills runaway processes.

Security: import whitelist is enforced BEFORE spawning. Only numpy and pandas are allowed.
"""
from __future__ import annotations

import multiprocessing
import pickle
import re
from typing import Any

import pandas as pd


class SandboxError(Exception):
    """Base class for all feature sandbox errors."""


class SandboxTimeoutError(SandboxError):
    """Raised when child process exceeds timeout_seconds."""


class SandboxValidationError(SandboxError):
    """Raised when code imports non-whitelisted module or returns wrong type."""


_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+(\w+)", re.MULTILINE)
_ALLOWED_IMPORTS = {"numpy", "pandas", "np", "pd"}


def _check_imports(code: str) -> None:
    """Reject code that imports anything outside the whitelist.

    Raises SandboxValidationError for forbidden imports.
    """
    for m in _IMPORT_RE.finditer(code):
        module = m.group(1)
        if module not in _ALLOWED_IMPORTS:
            raise SandboxValidationError(f"Forbidden import: {module}")


def _child_worker(code: str, df_bytes: bytes, conn: Any) -> None:
    """Runs in child process. Receives df via pickle, sends Series or error back."""
    import pickle as _pickle

    import numpy as _np
    import pandas as _pd

    try:
        df = _pickle.loads(df_bytes)
        local_ns: dict[str, Any] = {"pd": _pd, "np": _np, "df": df}
        exec(code, local_ns)  # noqa: S102 — isolated in child process
        result = local_ns.get("result")
        if not isinstance(result, _pd.Series):
            conn.send({"error": "code must assign a pd.Series to 'result'"})
        elif len(result) != len(df):
            conn.send({"error": f"result length {len(result)} != df length {len(df)}"})
        else:
            conn.send({"series": _pickle.dumps(result)})
    except Exception as exc:
        conn.send({"error": str(exc)})
    finally:
        conn.close()


def execute_feature_code(
    code: str,
    df: pd.DataFrame,
    timeout_seconds: float = 5.0,
) -> pd.Series:
    """Execute feature code in a subprocess. Return pd.Series aligned to df.index.

    The code receives a variable ``df`` (DataFrame with columns open/high/low/close/volume
    and a DatetimeIndex) and must assign a ``pd.Series`` to ``result``.

    Parameters
    ----------
    code:
        Python source code string. Only numpy and pandas imports are allowed.
    df:
        Bar data DataFrame to pass to the code.
    timeout_seconds:
        Maximum wall-clock seconds for the child process.

    Returns
    -------
    pd.Series aligned to df.index.

    Raises
    ------
    SandboxValidationError
        If the code uses forbidden imports, returns the wrong type,
        produces a wrong-length Series, or has >20% NaN values.
    SandboxTimeoutError
        If the child process exceeds the timeout.
    SandboxError
        If the child exits without producing a result.
    """
    _check_imports(code)

    parent_conn, child_conn = multiprocessing.Pipe(duplex=False)
    df_bytes = pickle.dumps(df)
    process = multiprocessing.Process(
        target=_child_worker,
        args=(code, df_bytes, child_conn),
    )
    process.start()
    child_conn.close()

    process.join(timeout=timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join()
        raise SandboxTimeoutError(f"Feature code exceeded {timeout_seconds}s timeout")

    if not parent_conn.poll():
        raise SandboxError("Child process exited without sending result")

    result = parent_conn.recv()
    parent_conn.close()

    if "error" in result:
        raise SandboxValidationError(result["error"])

    series: pd.Series = pickle.loads(result["series"])

    # Validate NaN ratio
    nan_ratio = series.isna().mean()
    if nan_ratio > 0.20:
        raise SandboxValidationError(
            f"Result contains {nan_ratio:.1%} NaN values (max 20%)"
        )

    series.index = df.index
    return series
