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


@lru_cache(maxsize=1)
def get_experiment_registry():
    from backend.lab.experiment_registry import ExperimentRegistry
    return ExperimentRegistry(get_metadata_repo())


@lru_cache(maxsize=1)
def get_feature_library():
    from backend.features.feature_library import FeatureLibrary
    return FeatureLibrary(get_metadata_repo())


@lru_cache(maxsize=1)
def get_dataset_builder():
    from backend.automl.dataset_builder import DatasetBuilder
    return DatasetBuilder(market_repo=get_market_repo(), artifact_repo=get_artifact_repo())


@lru_cache(maxsize=1)
def get_sagemaker_runner():
    from backend.automl.sagemaker_runner import SageMakerRunner
    import boto3
    client = boto3.client("sagemaker", region_name=settings.aws_region)
    return SageMakerRunner(sagemaker_client=client, region=settings.aws_region)


@lru_cache(maxsize=1)
def get_chat_repo():
    from backend.data.chat_repository import ChatRepository
    return ChatRepository(settings.metadata_path_resolved)


@lru_cache(maxsize=1)
def get_memory_store():
    from backend.lab.research_memory import ResearchMemoryStore
    return ResearchMemoryStore(get_metadata_repo())
