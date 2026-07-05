# On-call notes — shopapi

- Dashboards: Prometheus at `:9090`, alerts route through Alertmanager at `:9093`.
- Rollouts ship behind feature flags (`GET /admin/flags`). When an incident starts,
  check whether a flag was toggled recently — flag changes are logged as
  `{"event": "flag_change", ...}` in the service log.
- Rollback = disable the offending flag; no redeploy needed.
- Runbooks live in the team runbook repo (`runbooks/`).
