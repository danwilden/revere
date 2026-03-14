"""
Central configuration singleton. Every other module imports from here.
Reads from .env file via pydantic-settings.
"""

from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    print(model_config)

    # ── OANDA ──────────────────────────────────────────────────────────────
    oanda_env: str = "practice"          # "practice" | "live"
    oanda_api_token: str = ""
    oanda_account_id: str = ""
    base_ccy: str = "USD"

    @property
    def oanda_base_url(self) -> str:
        if self.oanda_env == "live":
            return "https://api-fxtrade.oanda.com"
        return "https://api-fxpractice.oanda.com"

    # ── Paths ───────────────────────────────────────────────────────────────
    # Resolved relative to the project root (two levels above this file)
    @property
    def project_root(self) -> Path:
        return Path(__file__).parents[2]

    @property
    def data_raw(self) -> Path:
        return self.project_root / "data" / "raw"

    @property
    def data_processed(self) -> Path:
        return self.project_root / "data" / "processed"

    @property
    def data_artifacts(self) -> Path:
        return self.project_root / "data" / "artifacts"

    @property
    def data_reports(self) -> Path:
        return self.project_root / "data" / "reports"

    # ── Instruments ─────────────────────────────────────────────────────────
    # CHANGE 1 (v2.4): Concentrated pair universe — only 2 pairs with demonstrated
    # edge (Sharpe > 0.5 in H4 walk-forward). EUR_USD, GBP_USD, AUD_USD, NZD_USD,
    # USD_CAD all had Sharpe < 0.35; their correlated losses amplify drawdown.
    # Uncomment each pair only after independently demonstrating Sharpe > 0.5
    # on a clean out-of-sample walk-forward test.
    active_pairs: List[str] = ["USD_JPY", "USD_CHF"]

    major_pairs: List[str] = [
        # "EUR_USD",   # v2.3 Sharpe 0.160 — insufficient edge, EUR block correlation
        # "GBP_USD",   # v2.3 Sharpe 0.206 — volatile, highly correlated with EUR_USD
        "USD_JPY",     # v2.3 Sharpe 0.795 — demonstrated edge ✓
        "USD_CHF",     # v2.3 Sharpe 0.804 — demonstrated edge ✓
        # "AUD_USD",   # v2.3 Sharpe 0.183 — commodity/risk proxy, no consistent edge
        # "NZD_USD",   # v2.3 Sharpe 0.020 — near-zero, correlated with AUD_USD
        # "USD_CAD",   # v2.3 Sharpe 0.332 — borderline; revisit after JPY/CHF stable
    ]

    # Primary research timeframes (D used for regime filter)
    granularities: List[str] = ["H1", "H4", "D"]

    # ── ML flags ─────────────────────────────────────────────────────────────
    # CHANGE 5 (v2.4): ML removed from live signal path. It is contra-predictive
    # (mean Sharpe -1.151 across all 7 pairs in v2.3 walk-forward). ML re-enters
    # the signal path only when shadow-mode IC on trade-level P&L exceeds 0.06
    # for 90+ consecutive trading days on BOTH USD_JPY and USD_CHF simultaneously.
    ml_enabled: bool = False
    ml_evaluation_mode: bool = True  # True = log ML predictions without acting on them

    # ── Risk defaults ────────────────────────────────────────────────────────
    default_risk_pct: float = 0.005          # 0.5% equity per trade
    max_risk_pct: float = 0.01               # 1.0% hard cap
    max_gross_exposure_multiple: float = 4.0  # × equity notionally
    max_margin_usage_pct: float = 0.25        # 25% of available margin
    max_concurrent_positions: int = 6
    drawdown_throttle_levels: List[float] = [0.03, 0.05, 0.08]

    # ── Strategy / regime parameters ─────────────────────────────────────────
    # CHANGE 10 (v2.5): Controls when the D1 regime gate is applied.
    #   "full"       — gate vetoes contradicting TRENDING/BREAKOUT signals on every bar,
    #                  including bars where an existing position is already open
    #                  (original v2.4 behaviour).
    #   "entry_only" — gate is checked only at the bar where a new signal begins;
    #                  open positions are not closed if the D1 regime flips mid-trade.
    #   "disabled"   — gate not applied at all (for diagnostic comparison).
    # Run scripts/run_d1_gate_diagnostic.py to compare modes before changing this.
    d1_gate_mode: str = "entry_only"

    # ── Logging ─────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    @field_validator("oanda_env")
    @classmethod
    def validate_env(cls, v: str) -> str:
        if v not in ("practice", "live"):
            raise ValueError("OANDA_ENV must be 'practice' or 'live'")
        return v

    @field_validator("d1_gate_mode")
    @classmethod
    def validate_d1_gate_mode(cls, v: str) -> str:
        if v not in ("full", "entry_only", "disabled"):
            raise ValueError("d1_gate_mode must be 'full', 'entry_only', or 'disabled'")
        return v


# Module-level singleton — import and reuse everywhere
settings = Settings()
