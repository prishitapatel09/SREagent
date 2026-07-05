# Postmortem: {{title}}

| | |
|---|---|
| **Incident** | {{incident_id}} |
| **Service** | {{service}} |
| **Severity** | {{severity}} ({{severity_band}}) |
| **Started** | {{started_at}} |
| **Resolved** | {{resolved_at}} |
| **Duration** | {{duration_min}} min |
| **Suspect commit** | {{suspect_commit}} |
| **Runbook** | {{runbook_slug}} |
| **Confidence** | {{confidence}} |

## Summary

{{summary}}

## Impact

- **{{error_rate_pct}}%** of requests failing (baseline {{baseline_error_rate_pct}}%)
- **~{{est_failed_requests}}** requests estimated failed over {{duration_min}} minutes
- p95 latency **{{p95_ms}}** (baseline {{baseline_p95_ms}})
- Traffic: {{requests_per_min}} requests/min

## Root cause

{{root_cause_analysis}}

## Timeline (UTC)

{{timeline}}

## Lessons learned

{{lessons_learned}}

## Action items

{{action_items}}

---
*Generated automatically by SREagent. Timeline entries map 1:1 to recorded
investigation events; impact numbers are computed from Prometheus, not estimated
by the model.*
