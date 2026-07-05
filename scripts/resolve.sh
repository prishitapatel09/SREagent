#!/usr/bin/env bash
# Disable all failure flags. Alertmanager sends the "resolved" webhook once metrics recover,
# which is what triggers the agent's postmortem.
set -euo pipefail

for FLAG in payments_v2 listing_inventory order_timestamps; do
  curl -sf -X POST "http://localhost:8000/admin/flags/${FLAG}" \
    -H 'content-type: application/json' -d '{"enabled": false}' > /dev/null
done

echo "All flags disabled. The alert should resolve within ~2 minutes (postmortem follows)."
