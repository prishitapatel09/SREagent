---
title: Database connection pool exhaustion
slug: database-connection-exhaustion
keywords: database db connection pool exhausted timeout postgres deadlock saturation
alertnames: HighErrorRate HighLatency
---

# Database connection pool exhaustion

## Symptoms
- Requests across **many endpoints** time out or 500 together (broad blast radius).
- Log lines mention `connection pool exhausted` or `could not obtain connection`.

## Immediate mitigation
1. Restart the API pods one at a time to release leaked connections.
2. Temporarily raise the pool ceiling if the database has headroom.

## Long-term fix
- Find the leak: audit code paths that check out connections without returning them.
