from app.runbooks.matcher import RunbookMatcher

from ..conftest import RUNBOOKS_DIR


def test_payment_incident_matches_payment_runbook():
    matcher = RunbookMatcher(str(RUNBOOKS_DIR))
    results = matcher.search("HighErrorRate checkout 502 PaymentGatewayError payment gateway")
    assert results[0]["slug"] == "payment-gateway-outage"
    assert "checkout" in results[0]["matched_terms"]


def test_latency_incident_matches_listing_runbook():
    matcher = RunbookMatcher(str(RUNBOOKS_DIR))
    results = matcher.search("HighLatency products slow p95 inventory lookup")
    assert results[0]["slug"] == "product-listing-latency"


def test_orders_incident_matches_orders_runbook():
    matcher = RunbookMatcher(str(RUNBOOKS_DIR))
    results = matcher.search("HighErrorRate orders TypeError timestamp 500")
    assert results[0]["slug"] == "order-status-errors"


def test_get_by_slug_and_missing_dir():
    matcher = RunbookMatcher(str(RUNBOOKS_DIR))
    assert "payments_v2" in matcher.get("payment-gateway-outage")
    assert matcher.get("nope") is None

    empty = RunbookMatcher("/nonexistent/dir")
    assert empty.search("anything") == []
