---
title: Payment gateway outage (checkout failing with 5xx)
slug: payment-gateway-outage
keywords: checkout payment payments gateway 502 PaymentGatewayError charge payments_v2 unreachable
alertnames: HighErrorRate
---

# Payment gateway outage

## Symptoms
- `POST /checkout` returns 502s; error rate on the checkout endpoint spikes.
- Service log shows `PaymentGatewayError` with a stack trace through `payment_client.py`.
- Product browsing is unaffected — the blast radius is checkout only.

## Likely causes
- The `payments_v2` rollout: the v2 client targets the new gateway host, which may be
  unreachable from this environment (DNS or firewall).
- Upstream payment provider outage (check provider status page).

## Immediate mitigation
1. Disable the `payments_v2` feature flag to fall back to the legacy client:
   `curl -X POST http://shopapi:8000/admin/flags/payments_v2 -H 'content-type: application/json' -d '{"enabled": false}'`
2. Confirm checkout error rate returns to baseline in Prometheus:
   `sum(rate(http_requests_total{job="shopapi", endpoint="/checkout", status=~"5.."}[1m]))`

## Verification
- `POST /checkout` returns 200 with a `receipt`.
- The `HighErrorRate` alert transitions firing → resolved within ~2 minutes.

## Escalation
- If the legacy path also fails, page the payments on-call; do not retry charges blindly
  (double-charge risk).
