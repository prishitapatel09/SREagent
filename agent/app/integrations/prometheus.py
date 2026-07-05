"""Thin Prometheus HTTP API client (instant queries only)."""

import math

import httpx


class Prometheus:
    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")

    def query(self, promql: str) -> list[dict]:
        """Run an instant query; returns [{labels, value}, ...]."""
        response = httpx.get(
            f"{self._base}/api/v1/query", params={"query": promql}, timeout=10.0
        )
        response.raise_for_status()
        body = response.json()
        if body.get("status") != "success":
            raise RuntimeError(f"prometheus query failed: {body}")
        results = []
        for item in body["data"]["result"]:
            value = float(item["value"][1])
            results.append({"labels": item.get("metric", {}), "value": value})
        return results

    def scalar(self, promql: str) -> float | None:
        """First sample of an instant query, or None if empty/NaN."""
        results = self.query(promql)
        if not results or math.isnan(results[0]["value"]):
            return None
        return results[0]["value"]
