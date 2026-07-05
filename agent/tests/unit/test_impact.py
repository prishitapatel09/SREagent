from datetime import datetime, timedelta, timezone

from app.impact import compute_impact

from ..conftest import FakeProm


def _minutes_ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def test_impact_math_with_empty_baseline():
    # Fresh demo stack: baseline (offset) queries return no data -> baseline 0.
    prom = FakeProm(total=0.166, errors=0.05, p95=0.13)
    impact = compute_impact(prom, "shopapi", "/checkout", _minutes_ago(4))

    assert impact.error_rate_pct == 30.1          # 100 * 0.05 / 0.166
    assert impact.baseline_error_rate_pct == 0.0  # empty-baseline guard
    assert impact.requests_per_min == 10.0        # 0.166/s * 60
    assert 10 <= impact.est_failed_requests <= 13 # 0.05/s * ~4min
    assert impact.p95_ms == 130.0
    assert impact.baseline_p95_ms is None
    assert impact.severity_band == "major"        # >= 25% error rate


def test_impact_with_baseline_available():
    prom = FakeProm(total=0.2, errors=0.002, p95=0.06, baseline_available=True)
    impact = compute_impact(prom, "shopapi", "", _minutes_ago(2))
    assert impact.baseline_error_rate_pct == 1.0  # 100 * 0.002 / 0.2
    assert impact.severity_band == "minor"


def test_impact_survives_bad_timestamp():
    impact = compute_impact(FakeProm(), "shopapi", "/checkout", "not-a-date")
    assert impact.duration_min == 0.0
    assert impact.est_failed_requests == 0
