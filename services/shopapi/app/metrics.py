"""Prometheus metrics: one middleware, two series."""

import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests by method, route template, and status code",
    ["method", "endpoint", "status"],
)

# Buckets straddle the ~50ms healthy baseline and the ~2s failure state.
LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request latency by route template",
    ["endpoint"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

_EXCLUDED = ("/metrics", "/healthz", "/admin")


async def middleware(request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    endpoint = getattr(route, "path", request.url.path)
    if not endpoint.startswith(_EXCLUDED):
        REQUESTS.labels(request.method, endpoint, str(response.status_code)).inc()
        LATENCY.labels(endpoint).observe(time.perf_counter() - start)
    return response


def exposition() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
