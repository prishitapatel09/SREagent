"""Deterministic user-impact estimation.

The LLM never does arithmetic — it is handed these numbers and only narrates
them. Baseline uses a short 5m offset with an empty-result guard because the
demo stack is typically only minutes old.
"""

from datetime import datetime, timezone

from .integrations.prometheus import Prometheus
from .models import Impact


def _escape(value: str) -> str:
    """Escape a label value for interpolation into a PromQL matcher."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _selector(job: str, endpoint: str, extra: str = "") -> str:
    parts = [f'job="{_escape(job)}"']
    if endpoint:
        parts.append(f'endpoint="{_escape(endpoint)}"')
    if extra:
        parts.append(extra)
    return "{" + ", ".join(parts) + "}"


_ERROR_STATUS_MATCHER = 'status=~"5.."'


def compute_impact(prom: Prometheus, job: str, endpoint: str, started_at: str) -> Impact:
    total_q = f"sum(rate(http_requests_total{_selector(job, endpoint)}[1m]))"
    errors_q = f"sum(rate(http_requests_total{_selector(job, endpoint, _ERROR_STATUS_MATCHER)}[1m]))"
    p95_q = (
        "histogram_quantile(0.95, sum by (le) "
        f"(rate(http_request_duration_seconds_bucket{_selector(job, endpoint)}[1m])))"
    )

    total_now = prom.scalar(total_q) or 0.0
    errors_now = prom.scalar(errors_q) or 0.0
    p95_now = prom.scalar(p95_q)

    # `offset` must sit inside the range selector — `sum(rate(...)) offset 5m`
    # is invalid PromQL. Each query contains exactly one "[1m]".
    base_window = "[1m] offset 5m"
    total_base = prom.scalar(total_q.replace("[1m]", base_window))
    errors_base = (
        prom.scalar(errors_q.replace("[1m]", base_window)) or 0.0
    ) if total_base else 0.0
    p95_base = prom.scalar(p95_q.replace("[1m]", base_window))

    try:
        started = datetime.fromisoformat(started_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        duration_min = max(
            (datetime.now(timezone.utc) - started).total_seconds() / 60, 0.0
        )
    except (ValueError, TypeError):
        duration_min = 0.0

    error_rate_pct = 100.0 * errors_now / total_now if total_now else 0.0
    baseline_error_rate_pct = (
        100.0 * errors_base / total_base if total_base else 0.0
    )
    excess_failures_per_s = max(errors_now - errors_base, 0.0)

    p95_ms = round(p95_now * 1000, 1) if p95_now is not None else None
    baseline_p95_ms = round(p95_base * 1000, 1) if p95_base is not None else None

    if error_rate_pct >= 25 or (p95_ms or 0) > 2000:
        severity_band = "major"
    elif error_rate_pct >= 5 or (p95_ms or 0) > 500:
        severity_band = "moderate"
    else:
        severity_band = "minor"

    return Impact(
        error_rate_pct=round(error_rate_pct, 1),
        baseline_error_rate_pct=round(baseline_error_rate_pct, 1),
        requests_per_min=round(total_now * 60, 1),
        est_failed_requests=int(excess_failures_per_s * duration_min * 60),
        p95_ms=p95_ms,
        baseline_p95_ms=baseline_p95_ms,
        duration_min=round(duration_min, 1),
        severity_band=severity_band,
    )
