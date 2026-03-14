"""Code-first strategy adapter.

Loads a user-supplied Python class that inherits BaseStrategy from a code
string, and delegates all strategy calls to it.

EXECUTION MODEL — READ BEFORE USING:
    CodeStrategy uses importlib to load and execute user code in-process.
    This is intentional for the backtesting worker context.

    The PROJECT_SPEC isolation requirement ("user code shall not run inside
    the API server process") is satisfied by running the backtester in
    apps/worker/, not apps/api/. CodeStrategy MUST NOT be called from the
    API server process directly.

    Per-bar event loop in the backtester  → use CodeStrategy (zero subprocess overhead)
    One-shot validation in API routes     → use run_sandboxed() from sandbox.py
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from backend.strategies.base import BaseStrategy
from backend.strategies.state import StrategyState


class CodeStrategy(BaseStrategy):
    """Wraps a user-defined Python class loaded from a source code string.

    The user code must define a class that inherits from BaseStrategy and
    implement should_enter_long, should_enter_short, and should_exit.

    Parameters
    ----------
    code:
        Python source code string. Must contain a class that subclasses
        BaseStrategy.
    class_name:
        Name of the class to instantiate. If None, the first BaseStrategy
        subclass found in the module is used.
    """

    def __init__(self, code: str, class_name: str | None = None) -> None:
        self._code = code
        self._class_name = class_name
        self._delegate: BaseStrategy = self._load(code, class_name)

    # ------------------------------------------------------------------
    # Internal loader
    # ------------------------------------------------------------------

    @staticmethod
    def _load(code: str, class_name: str | None) -> BaseStrategy:
        """Compile the code string and instantiate the strategy class."""
        # Write to a temp module file. delete=False is required on all platforms
        # because the file must remain readable by importlib after the context manager
        # exits. We clean it up explicitly in the finally block.
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", delete=False, prefix="medallion_strategy_"
        ) as f:
            f.write(code)
            tmp_path = f.name

        try:
            spec = importlib.util.spec_from_file_location("_user_strategy", tmp_path)
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as exc:
                raise ValueError(f"Failed to load strategy code: {exc}") from exc
        finally:
            # Always clean up the temp file — even on exception paths.
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass

        if class_name:
            cls = getattr(module, class_name, None)
            if cls is None:
                raise ValueError(
                    f"Class '{class_name}' not found in the provided strategy code."
                )
            if not (isinstance(cls, type) and issubclass(cls, BaseStrategy)):
                raise ValueError(
                    f"Class '{class_name}' must be a subclass of BaseStrategy."
                )
            return cls()

        # Auto-discover the first BaseStrategy subclass
        for name in dir(module):
            obj = getattr(module, name)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseStrategy)
                and obj is not BaseStrategy
            ):
                return obj()

        raise ValueError(
            "No BaseStrategy subclass found in the provided strategy code. "
            "Make sure your class inherits from BaseStrategy."
        )

    # ------------------------------------------------------------------
    # Delegation
    # ------------------------------------------------------------------

    def should_enter_long(
        self,
        bar: dict,
        features: dict,
        state: StrategyState,
    ) -> bool:
        return self._delegate.should_enter_long(bar, features, state)

    def should_enter_short(
        self,
        bar: dict,
        features: dict,
        state: StrategyState,
    ) -> bool:
        return self._delegate.should_enter_short(bar, features, state)

    def should_exit(
        self,
        bar: dict,
        features: dict,
        position: dict,
        state: StrategyState,
    ) -> bool:
        return self._delegate.should_exit(bar, features, position, state)

    def position_size(self, bar: dict, equity: float, params: dict) -> float:
        return self._delegate.position_size(bar, equity, params)

    def stop_price(self, bar: dict, side: str, params: dict) -> float | None:
        return self._delegate.stop_price(bar, side, params)

    def take_profit_price(self, bar: dict, side: str, params: dict) -> float | None:
        return self._delegate.take_profit_price(bar, side, params)
