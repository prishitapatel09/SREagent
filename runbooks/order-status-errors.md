---
title: Order status endpoint errors (partial 5xx on /orders)
slug: order-status-errors
keywords: orders order status 500 TypeError timestamp fulfilled partial crash internal error
alertnames: HighErrorRate
---

# Order status endpoint errors

## Symptoms
- `GET /orders/{id}` fails for a **subset** of orders (~40%) with 500s; others succeed.
- Service log shows `TypeError` with a stack trace through `order_utils.py`.
- The failing orders are the unfulfilled ones (`fulfilled_at` is null).

## Likely causes
- The `order_timestamps` rollout: the new centralized timestamp parser assumes every
  order has a `fulfilled_at` value; unfulfilled orders crash it.

## Immediate mitigation
1. Disable the `order_timestamps` feature flag to restore the null-safe legacy path:
   `curl -X POST http://shopapi:8000/admin/flags/order_timestamps -H 'content-type: application/json' -d '{"enabled": false}'`
2. Confirm error rate on `/orders/{order_id}` returns to zero.

## Verification
- Both fulfilled and unfulfilled orders return 200.

## Long-term fix
- Make the parser null-safe and add a regression test with an unfulfilled order.
