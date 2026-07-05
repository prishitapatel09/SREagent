#!/usr/bin/env bash
# Inject a failure into shopapi by enabling the feature flag that gates its bad code path.
set -euo pipefail

MODE="${1:-payment_outage}"
case "$MODE" in
  payment_outage) FLAG=payments_v2 ;;
  slow_products)  FLAG=listing_inventory ;;
  orders_crash)   FLAG=order_timestamps ;;
  *) echo "usage: $0 payment_outage|slow_products|orders_crash" >&2; exit 1 ;;
esac

curl -sf -X POST "http://localhost:8000/admin/flags/${FLAG}" \
  -H 'content-type: application/json' -d '{"enabled": true}' > /dev/null

echo "Injected ${MODE} (flag ${FLAG}=true). Expect the alert to fire in ~60-90s."
