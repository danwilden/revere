"""Central settings for the Medallion platform.

Read from environment variables / .env file via pydantic-settings.
All modules import `settings` from here — no direct os.getenv calls.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve the project root at import time so that relative storage paths
# always anchor to the repo root regardless of the process working directory.
# config.py lives at <project-root>/backend/config.py, so:
#   Path(__file__).resolve().parent  → <project-root>/backend/
#   .parent                          → <project-root>/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Environment ---
    environment: str = "local"  # local | dev | prod

    # --- OANDA ---
    oanda_account_id: str = ""
    oanda_access_token: str = ""
    oanda_environment: str = "practice"  # practice | live

    # --- Local storage paths ---
    duckdb_path: str = "data/market.duckdb"
    metadata_path: str = "data/metadata"
    artifact_path: str = "data/artifacts"

    # --- AWS ---
    aws_region: str = "us-east-1"
    aws_stage: str = "dev"
    s3_bucket: str = ""

    # --- Bedrock (agents) ---
    bedrock_model_id: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    bedrock_region: str = "us-east-1"

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # --- Research defaults ---
    default_pairs: list[str] = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD"]
    default_hmm_states: int = 7

    # --- Dukascopy Node downloader ---
    dukascopy_node_cmd: str = "npx dukascopy-node"
    """Shell command (or path) to the dukascopy-node CLI downloader.
    Override via DUKASCOPY_NODE_CMD env var if using a local install."""

    dukascopy_download_dir: str = "data/dukascopy_downloads"
    """Base directory where Node-downloaded CSVs are written.
    Per-job subdirectories are created here: {base}/{job_id}/{instrument}/"""

    dukascopy_node_timeout_secs: int = 600
    """Seconds before the Node subprocess is killed (per instrument)."""

    @property
    def is_local(self) -> bool:
        return self.environment == "local"

    @property
    def duckdb_path_resolved(self) -> Path:
        p = Path(self.duckdb_path)
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def metadata_path_resolved(self) -> Path:
        p = Path(self.metadata_path)
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def artifact_path_resolved(self) -> Path:
        p = Path(self.artifact_path)
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def dukascopy_download_dir_resolved(self) -> Path:
        p = Path(self.dukascopy_download_dir)
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
