"""Typed contracts shared across the agent: alerts, diagnoses, impact, incidents."""

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]

STATES = ("detected", "investigating", "diagnosed", "resolved", "postmortem_published")


class AlertInfo(BaseModel):
    fingerprint: str
    alertname: str
    service: str = ""
    severity: str = ""
    endpoint: str = ""
    summary: str = ""
    description: str = ""
    starts_at: str = ""


class SuspectCommit(BaseModel):
    sha: str
    author: str = ""
    message: str = ""


class Diagnosis(BaseModel):
    summary: str
    root_cause: str
    suspect_commit: SuspectCommit | None = None
    confidence: Confidence = "low"
    runbook_slug: str = "none"
    remediation: str = ""
    evidence: list[str] = Field(default_factory=list)


class Impact(BaseModel):
    error_rate_pct: float = 0.0
    baseline_error_rate_pct: float = 0.0
    requests_per_min: float = 0.0
    est_failed_requests: int = 0
    p95_ms: float | None = None
    baseline_p95_ms: float | None = None
    duration_min: float = 0.0
    severity_band: str = "minor"  # minor | moderate | major


class DiagnosisArgs(BaseModel):
    """What the LLM must supply via the terminal `submit_diagnosis` tool.

    Flat, string-only fields: small open models mangle nested schemas.
    """

    summary: str
    root_cause: str
    suspect_commit_sha: str = "unknown"
    confidence: Confidence = "medium"
    runbook_slug: str = "none"
    remediation: str = ""
    evidence: list[str] = Field(default_factory=list)
