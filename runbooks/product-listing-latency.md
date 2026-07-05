---
title: Product listing latency (slow /products)
slug: product-listing-latency
keywords: latency slow products listing p95 inventory lookup N+1 timeout duration
alertnames: HighLatency
---

# Product listing latency

## Symptoms
- p95 latency on `GET /products` far above the ~60ms baseline (often 1.5-2.5s).
- Error rate roughly unchanged — requests succeed, they're just slow.
- Service log shows repeated `inventory lookup for product N took ...ms` warnings.

## Likely causes
- The `listing_inventory` rollout: the listing now calls the inventory service once per
  product (an N+1 pattern) instead of batching, multiplying downstream latency by the
  page size.
- Inventory service itself degraded (check its latency independently).

## Immediate mitigation
1. Disable the `listing_inventory` feature flag:
   `curl -X POST http://shopapi:8000/admin/flags/listing_inventory -H 'content-type: application/json' -d '{"enabled": false}'`
2. Watch p95 recover:
   `histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{job="shopapi", endpoint="/products"}[1m])))`

## Verification
- `GET /products` p95 back under 200ms; `HighLatency` alert resolves.

## Long-term fix
- Batch the inventory lookups (single call for all page items) before re-enabling the flag.
