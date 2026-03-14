"""Shared dependency instances for the API layer.

All repository singletons are created once at startup and shared across
requests. For local development these are file-based; in cloud they are
swapped for DynamoDB/S3-backed implementations.
"""
from __future__ import annotations

from functools import lru_cache

from backend.config import settings
from backend.data.duckdb_store import DuckDBStore
from backend.data.repositories import LocalArtifactRepository, LocalMetadataRepository
from backend.jobs.status import JobManager


@lru_cache(maxsize=1)
def get_market_repo() -> DuckDBStore:
    return DuckDBStore(settings.duckdb_path_resolved)


@lru_cache(maxsize=1)
def get_metadata_repo() -> LocalMetadataRepository:
    return LocalMetadataRepository(settings.metadata_path_resolved)


@lru_cache(maxsize=1)
def get_artifact_repo() -> LocalArtifactRepository:
    return LocalArtifactRepository(settings.artifact_path_resolved)


@lru_cache(maxsize=1)
def get_job_manager() -> JobManager:
    return JobManager(get_metadata_repo())
