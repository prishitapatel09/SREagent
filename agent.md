# SREagent — Project Context for LLMs

This document gives a complete picture of the SREagent project so a new LLM can pick up where we left off without re-reading the entire codebase.

---

## What It Is

An **autonomous AI incident-response agent** built as a portfolio piece for SRE/DevOps internship interviews. When a production alert fires, the agent:

1. Receives the Alertmanager webhook instantly
2. Runs an LLM tool-calling investigation loop — queries Prometheus, tails logs, runs `git log -S`, reads diffs, matches runbooks
3. Identifies the likely bad commit with real git tooling (not a lookup table)
4. Computes user impact deterministically from Prometheus (the LLM never does math)
5. Posts a structured Slack incident brief (console fallback if no Slack)
6. After the alert resolves, auto-generates a postmortem with a 1:1 event timeline

The demo stack ships its own breakable "production": a fake store API, live load generator, Prometheus alerting, and a seeded git history with planted bad commits — so you can break prod on purpose and watch the AI respond live.

---

## Stack

| Layer | Choice |
|---|---|
| Agent service | FastAPI (Python 3.11+) |
| LLM | Qwen3:8b via Ollama (OpenAI-compatible SDK) |
| Demo store | FastAPI (`services/shopapi`) |
| Metrics + alerting | Prometheus 2.53 + Alertmanager 0.27 |
| Persistence | SQLite (WAL mode), `asyncio.Lock` |
| Realtime UI | Server-Sent Events (SSE), vanilla JS dashboard |
| Runbook matching | BM25 (`rank_bm25`) |
| Tests | pytest 21 tests — no mocks, stub LLM |

---

## Architecture

```
loadgen ──traffic──▶ shopapi (feature-flag failure injection)
                        │ /metrics                    │ JSON logs → shared volume
                        ▼                             ▼
                   Prometheus ──alert rules──▶ Alertmanager
                                                  │ webhook
                                                  ▼
                        agent (FastAPI: webhook + LLM loop + SSE + dashboard)
                         │ tools: PromQL · logs · git log -S · diffs · runbooks
                         ▼
                  Qwen via Ollama (or any OpenAI-compatible endpoint)
                         │
          Slack brief ◀──┴──▶ SQLite ──▶ live dashboard
                                 └──▶ postmortems/*.md
```

---

## Project Layout

```
agent/                  the AI agent + dashboard (one container)
  app/
    main.py             FastAPI app factory, lifespan recovery hook
    config.py           pydantic-settings (all env vars)
    models.py           AlertInfo, Diagnosis, SuspectCommit, Impact, DiagnosisArgs
    store.py            SQLite CRUD, list_unfinished(), max_global_seq()
    events.py           EventBus: persist-then-SSE-fan-out
    state.py            StateMachine: detected → investigating → diagnosed → resolved → postmortem_published
    webhook.py          /webhook/alertmanager (firing + resolved), /healthz
    impact.py           compute_impact(): PromQL-based, deterministic
    prompts.py          system_prompt(), alert_message(), NUDGE_MESSAGE
    investigation/
      loop.py           InvestigationFailed, Investigator.run(), maybe_finalize()
      tools.py          9 tools: query_prometheus, get_service_logs, get_recent_commits,
                        get_commit_diff, search_commits, search_runbooks, get_runbook,
                        calculate_user_impact, submit_diagnosis (terminal)
      stub.py           StubLLM: duck-types OpenAI SDK, drives real tools deterministically
      llm.py            make_llm_client() — returns StubLLM or openai.OpenAI
      fallback.py       build_fallback_diagnosis() — deterministic last resort
    integrations/
      prometheus.py     Prometheus.query() wrapping /api/v1/query
      gitrepo.py        GitRepo: recent_commits, commit_diff, search_commits, commit_meta
      logs.py           ServiceLogs: tail structured JSON logs
      slack.py          SlackNotifier: posts to webhook, falls back to console card
    runbooks/
      matcher.py        RunbookMatcher: BM25 over frontmatter + body
    postmortem/
      generator.py      PostmortemGenerator: LLM-generated markdown + duration rescaling
      template.md       Jinja2 template
    dashboard/
      routes.py         /api/incidents, /api/stream (SSE), /api/cursor, /api/incidents/{id}/postmortem
      static/
        index.html      Single-page app shell
        app.js          Vanilla JS: EventSource, cold-load buffer, status pills
        style.css       Dark-mode dashboard
        md.js           Tiny marked.js for postmortem rendering
  tests/
    conftest.py         FakeProm, demo_repo fixture, app_factory fixture
    unit/               test_store, test_state, test_impact, test_matcher, test_loop
    integration/        test_webhook_to_incident, test_restart_recovery
    smoke/              test_agent_smoke (full pipeline, stub mode)
    fixtures/           alertmanager_firing.json, alertmanager_resolved.json

services/
  shopapi/              demo store API (also the seeded repo source)
    app/
      main.py           /checkout, /products, /orders, /healthz, /metrics, /admin/flags/{flag}
      flags.py          Feature-flag registry (payments_v2, listing_inventory, order_timestamps)
      payment_client.py payments_v2 flag → charge_v2() → PaymentGatewayError (bad commit #1)
      catalog.py        listing_inventory flag → live inventory lookup (bad commit #2)
      order_utils.py    order_timestamps flag → fromisoformat(None) crash (bad commit #3)
      metrics.py        Prometheus metrics — NO `service` label; agent filters on job="shopapi"
  loadgen/              constant background traffic

ops/
  prometheus/
    prometheus.yml      scrape config for shopapi + agent
    alert_rules.yml     HighErrorRate (>5% 5xx for 30s), HighLatency (p95 >500ms for 45s)
  alertmanager/
    alertmanager.yml    routes all alerts → http://agent:8080/webhook/alertmanager

runbooks/               5 runbooks (3 target, 2 decoys) in YAML-frontmatter markdown
  payment-gateway-outage.md
  product-listing-latency.md
  order-status-errors.md
  database-connection-exhaustion.md
  deploy-rollback.md

scripts/
  seed_history.py       Builds demo-repo/shopapi as real 15-commit git repo; plants 3 bad commits;
                        verifies each bad string appears in exactly one commit (git log -S);
                        verifies final tree == services/shopapi (byte-identical)
  inject.sh             Calls /admin/flags/{flag} to enable a bad commit's code path
  resolve.sh            Disables all flags → alert resolves → postmortem publishes
  dev_stub.sh           Runs the agent locally in stub mode (no docker, no LLM needed)

docker-compose.yml      5 services: shopapi, loadgen, prometheus, alertmanager, agent
Makefile                make up / demo-break / demo-fix / demo-stub / test / smoke
.env.example            All env vars documented
.github/workflows/ci.yml  GitHub Actions: seed + test + smoke on every push
```

---

## How the Demo Works (The Core Trick)

Each failure scenario enables a feature flag. The flag's code path was introduced by a **specific commit in the seeded git history**. Error strings from the runtime stack trace appear verbatim in that commit's diff. The agent finds the culprit with honest `git log -S` tooling — not a rigged lookup table.

| Failure | Flag | Bad commit keyword | Alert |
|---|---|---|---|
| `payment_outage` | `payments_v2` | `payments-v2.internal:9443` | HighErrorRate on /checkout |
| `slow_products` | `listing_inventory` | `live_inventory_lookup` | HighLatency on /products |
| `orders_crash` | `order_timestamps` | `fromisoformat(order["fulfilled_at"])` | HighErrorRate on /orders |

---

## Key Design Decisions

**Investigation loop guardrails** (for small open models):
- Flat tool schemas (string/int only — no nested objects)
- One tool per turn enforced in code (Ollama ignores `parallel_tool_calls`)
- At budget-1 remaining, `tool_choice` forces `submit_diagnosis`
- Pydantic validation + one repair round on diagnosis args
- Deterministic fallback if LLM fails entirely

**Event protocol:**
- Every investigation step is written to SQLite *then* fanned out over SSE
- The SQLite rowid doubles as the SSE `id` — reconnects replay losslessly via `Last-Event-ID`
- `/api/cursor` returns current max seq for the dashboard's first connect (avoids replay races)

**Startup recovery:**
- `lifespan` hook calls `_recover_unfinished()` on every agent start
- Incidents stranded in `investigating` are re-investigated; `resolved` incidents get their postmortem

**Stub mode:**
- `StubLLM` is a duck-typed fake that drives the *real* tools (not canned responses)
- It extracts the actual commit sha from tool results via regex
- Powers the entire test suite and doubles as the demo fallback if Ollama is down

---

## Verified Working (End-to-End)

- `make up` → 5 healthy containers in ~45s
- `make demo-break FAILURE=payment_outage` → alert fires in ~42s
- Live Qwen3:8b investigation completes in ~80s → correct diagnosis (exact sha, confidence high, runbook `payment-gateway-outage`)
- `make demo-fix` → alert resolves → postmortem published in ~101s with correct timeline, suspect commit, impact numbers
- `make test` → 20 unit + integration tests pass in ~1.4s (stub LLM, no docker, no network)
- `make smoke` → full pipeline smoke test passes

---

## How to Run

**Prerequisites:**
- Docker Desktop
- Python 3.11+ (for `make seed` and `make test`)
- Ollama with `ollama pull qwen3:8b` (for live mode); or skip for stub mode

```bash
# 1. Clone and configure
cp .env.example .env   # edit if you want Slack, OpenRouter, etc.

# 2. Full docker stack (live Qwen mode)
make up
make demo-break        # FAILURE=payment_outage (default) | slow_products | orders_crash
# open http://localhost:8080 and watch

make demo-fix          # heals prod → postmortem publishes

# 3. Stub mode (no LLM, no GPU)
make demo-stub         # restarts agent in AGENT_MODE=stub
make demo-break        # same demo, deterministic investigator

# 4. Tests only (no docker)
pip install -e "./agent[test]"
make test              # unit + integration
make smoke             # fast CI gate
```

---

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `LLM_BASE_URL` | `http://host.docker.internal:11434/v1` | Any OpenAI-compatible endpoint |
| `LLM_API_KEY` | `ollama` | Any non-empty string for Ollama |
| `LLM_MODEL` | `qwen3:8b` | Model name |
| `AGENT_MODE` | `live` | `live` or `stub` |
| `SLACK_WEBHOOK_URL` | *(empty)* | Slack incoming webhook; empty = dashboard card |
| `MAX_TOOL_CALLS` | `10` | LLM tool budget per investigation |
| `RPS` | `10` | Load-generator traffic rate |

Hosted alternative: `LLM_BASE_URL=https://openrouter.ai/api/v1`, `LLM_MODEL=qwen/qwen3-32b`.

---

## Known Limitations / Possible Next Steps

- Single-service demo scope; multi-service trace correlation is the obvious extension
- Runbook matching is BM25 (lexical) — deliberate for explainability; embeddings would help at 100+ runbooks
- Agent diagnoses and recommends but doesn't auto-remediate; an approval-gated "disable flag" action is the natural next feature
- One incident per alert fingerprint; alert-storm correlation not modeled
- Dashboard screenshots not yet committed (add after first demo run to `docs/screenshots/`)

---

## Python File Cross-Reference (most important)

| File | What it owns |
|---|---|
| `agent/app/investigation/loop.py` | The LLM tool-calling loop and all its guardrails |
| `agent/app/investigation/tools.py` | All 9 tools + their flat JSON schemas |
| `agent/app/investigation/stub.py` | Deterministic fake LLM for tests and demo fallback |
| `agent/app/store.py` | SQLite persistence; all reads and writes go here |
| `agent/app/events.py` | EventBus: emit = write to SQLite + push to all SSE subscribers |
| `agent/app/impact.py` | PromQL-based user impact calculation |
| `agent/app/postmortem/generator.py` | LLM-generated postmortem with actual duration rescaling |
| `agent/app/dashboard/routes.py` | SSE stream, cursor, incident API |
| `agent/app/dashboard/static/app.js` | Dashboard JS: cold-load buffer, EventSource, routing |
| `services/shopapi/app/flags.py` | Feature-flag registry + admin API |
| `scripts/seed_history.py` | Builds the seeded git repo with planted bad commits |
