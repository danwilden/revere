"""Central settings for the Medallion platform.

Read from environment variables / .env file via pydantic-settings.
All modules import `settings` from here — no direct os.getenv calls.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def is_local(self) -> bool:
        return self.environment == "local"

    @property
    def duckdb_path_resolved(self) -> Path:
        p = Path(self.duckdb_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def metadata_path_resolved(self) -> Path:
        p = Path(self.metadata_path)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def artifact_path_resolved(self) -> Path:
        p = Path(self.artifact_path)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
