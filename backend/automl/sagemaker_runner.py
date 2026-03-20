"""SageMaker Autopilot runner — launch and poll AutoML v2 jobs.

The boto3 SageMaker client is injected at construction time so callers
(tests included) can provide a mock without patching module-level imports.

AUC-ROC acceptance threshold: >= 0.55 (hard gate — set at module level so
tests and production code share the same constant).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Hard gate: candidates with best AUC-ROC below this are never accepted.
AUC_ROC_ACCEPTANCE_THRESHOLD: float = 0.55

# SageMaker InProgress status strings that map to our "running" status.
_SAGEMAKER_RUNNING_STATUSES: frozenset[str] = frozenset({"InProgress", "Stopped"})
_SAGEMAKER_STATUS_MAP: dict[str, str] = {
    "InProgress": "running",
    "Stopping": "running",
    "Stopped": "running",
    "Completed": "completed",
    "Failed": "failed",
}


class SageMakerRunner:
    """Thin facade over the SageMaker AutoML v2 API.

    Parameters
    ----------
    sagemaker_client:
        An already-constructed ``boto3.client("sagemaker")`` (or compatible
        mock). Never instantiated internally so callers control credentials,
        region, and test doubles.
    region:
        AWS region string — stored for reference; the client is already
        region-bound when injected.
    """

    def __init__(self, sagemaker_client: Any, region: str = "us-east-1") -> None:
        self._client = sagemaker_client
        self.region = region

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def launch_automl_job(
        self,
        job_name: str,
        target_column: str,
        target_type: str,
        train_s3_uri: str,
        output_s3_prefix: str,
        max_runtime_seconds: int = 3600,
    ) -> str:
        """Create a SageMaker AutoML v2 job and return its name.

        Parameters
        ----------
        job_name:
            Unique name for this AutoML job (must satisfy SageMaker naming
            rules: alphanumeric + hyphens, <= 32 chars).
        target_column:
            Name of the column in the training dataset to predict.
        target_type:
            ``"direction"`` → BinaryClassification,
            ``"return_bucket"`` → MulticlassClassification.
        train_s3_uri:
            S3 URI of the training CSV file or prefix
            (e.g. ``"s3://bucket/prefix/"``).
        output_s3_prefix:
            S3 prefix where SageMaker writes AutoML outputs.
        max_runtime_seconds:
            Wall-clock cap for the entire AutoML job. Default 3600 (1 hour).

        Returns
        -------
        str
            ``job_name`` as returned (or as passed — SageMaker echoes it).

        Raises
        ------
        botocore.exceptions.ClientError
            On any SageMaker API error (wrong credentials, quota, etc.).
        ValueError
            When ``target_type`` is not one of the supported values.
        """
        problem_type_config = self._build_problem_type_config(
            target_type, target_column
        )

        request: dict[str, Any] = {
            "AutoMLJobName": job_name,
            "AutoMLJobInputDataConfig": [
                {
                    "DataSource": {
                        "S3DataSource": {
                            "S3DataType": "S3Prefix",
                            "S3Uri": train_s3_uri,
                        }
                    },
                    "TargetAttributeName": target_column,
                }
            ],
            "OutputDataConfig": {
                "S3OutputPath": output_s3_prefix,
            },
            "AutoMLProblemTypeConfig": problem_type_config,
            "AutoMLJobObjective": {"MetricName": "AUC"},
            "AutoMLJobConfig": {
                "CompletionCriteria": {
                    "MaxAutoMLJobRuntimeInSeconds": max_runtime_seconds,
                }
            },
        }

        logger.info(
            "Launching SageMaker AutoML v2 job: job_name=%s target_type=%s",
            job_name,
            target_type,
        )
        self._client.create_auto_ml_job_v2(**request)
        return job_name

    def poll_job(self, job_name: str) -> dict[str, Any]:
        """Poll a running AutoML job and return a normalised status dict.

        Parameters
        ----------
        job_name:
            Name of the AutoML job to describe.

        Returns
        -------
        dict with keys:
            ``status``         — ``"running" | "completed" | "failed" | "stopped"``
            ``best_candidate`` — dict or ``None``
            ``candidates``     — list of candidate dicts
            ``failure_reason`` — str or ``None``
            ``accepted``       — ``bool`` (True iff best AUC-ROC >= threshold)
        """
        response = self._client.describe_auto_ml_job_v2(AutoMLJobName=job_name)

        sm_status: str = response.get("AutoMLJobStatus", "InProgress")
        our_status = _SAGEMAKER_STATUS_MAP.get(sm_status, "running")

        failure_reason: str | None = response.get("FailureReason")

        # Best candidate lives under "BestCandidate" in the describe response.
        best_candidate: dict[str, Any] | None = response.get("BestCandidate")
        candidates: list[dict[str, Any]] = []

        accepted = False
        if best_candidate is not None:
            auc_roc = _extract_best_auc_roc(best_candidate)
            if auc_roc is not None and auc_roc >= AUC_ROC_ACCEPTANCE_THRESHOLD:
                accepted = True

        logger.info(
            "Polled AutoML job %s: sm_status=%s our_status=%s accepted=%s",
            job_name,
            sm_status,
            our_status,
            accepted,
        )

        return {
            "status": our_status,
            "best_candidate": best_candidate,
            "candidates": candidates,
            "failure_reason": failure_reason,
            "accepted": accepted,
        }

    def get_candidates(self, job_name: str) -> list[dict[str, Any]]:
        """Return all candidates for a completed AutoML job.

        Parameters
        ----------
        job_name:
            Name of the AutoML job.

        Returns
        -------
        list[dict]
            Normalised candidate dicts (direct SageMaker response items).
        """
        response = self._client.list_candidates_for_auto_ml_job(
            AutoMLJobName=job_name
        )
        candidates: list[dict[str, Any]] = response.get("Candidates", [])
        logger.info(
            "Retrieved %d candidates for AutoML job %s", len(candidates), job_name
        )
        return candidates

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_problem_type_config(
        target_type: str, target_column: str
    ) -> dict[str, Any]:
        """Map our ``target_type`` string to the SageMaker problem type config."""
        if target_type == "direction":
            return {
                "TabularJobConfig": {
                    "TargetAttributeName": target_column,
                    "ProblemType": "BinaryClassification",
                }
            }
        if target_type == "return_bucket":
            return {
                "TabularJobConfig": {
                    "TargetAttributeName": target_column,
                    "ProblemType": "MulticlassClassification",
                }
            }
        raise ValueError(
            f"Unsupported target_type '{target_type}'. "
            "Expected 'direction' or 'return_bucket'."
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _extract_best_auc_roc(candidate: dict[str, Any]) -> float | None:
    """Try to extract the best AUC-ROC value from a SageMaker candidate dict.

    SageMaker stores final metrics under
    ``candidate["FinalAutoMLJobObjectiveMetric"]`` with structure::

        {
            "MetricName": "validation:auc",
            "Value": 0.72,
            "Type": "Maximize",
        }

    We also check a ``"CandidateMetrics"`` list for AUC entries as a fallback.
    Returns ``None`` if no AUC metric can be found.
    """
    # Primary: FinalAutoMLJobObjectiveMetric
    final_metric = candidate.get("FinalAutoMLJobObjectiveMetric")
    if final_metric is not None:
        value = final_metric.get("Value")
        if value is not None:
            return float(value)

    # Fallback: scan CandidateMetrics list for any AUC entry
    for metric in candidate.get("CandidateMetrics", []):
        name: str = metric.get("MetricName", "").lower()
        if "auc" in name:
            value = metric.get("Value")
            if value is not None:
                return float(value)

    return None
