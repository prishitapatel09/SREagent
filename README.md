# SREagent — an autonomous AI incident responder

An AI system that responds to production outages **the moment an alert fires**:
it identifies the likely bad commit, finds the right runbook, estimates user
impact, posts a Slack brief — and auto-generates a postmortem once the issue
is fixed.

> `docs/screenshots/` — add a dashboard GIF here after your first demo run.

**What it does**

1. Reacts instantly to Alertmanager webhooks (firing *and* resolved)
2. Identifies the likely bad commit from the service's real git history
3. Finds the right runbook (explainable BM25 matching, not a black box)
4. Estimates user impact deterministically from Prometheus (the LLM never does math)
5. Posts a structured Slack incident brief (console fallback without Slack)
6. Auto-generates a postmortem — timeline mapped 1:1 to recorded events

The whole thing ships with its own breakable "production": a demo store API,
live traffic, Prometheus alerting, and a seeded git history with planted bad
commits — so you can **break prod on purpose and watch the AI respond, live**.

## Architecture

```
loadgen ──traffic──▶ shopapi (demo store API, feature-flag failure injection)
                        │ /metrics                      │ JSON logs → shared volume
                        ▼                               ▼
                   Prometheus ──alert rules──▶ Alertmanager
                                                    │ webhook (firing/resolved)
                                                    ▼
                              agent (FastAPI: webhook + LLM loop + SSE + dashboard)
                               │ tools: PromQL · logs · git log -S · runbooks · impact
                               ▼
                    Qwen (Ollama, or any OpenAI-compatible endpoint)
                               │
              Slack brief ◀────┴────▶ SQLite (incidents + events) ──▶ live dashboard
                                              └──▶ postmortems/*.md
```

**The trick that makes the demo honest:** each failure injection enables a
feature flag whose buggy code path was introduced by a *specific commit in a
real seeded git history*. Error strings in the runtime stack traces appear
verbatim in that commit's diff — so the agent finds the culprit with honest
tooling (`git log -S`), not a rigged lookup.

## Quickstart

Prereqs: Docker Desktop, Python 3.11+, and (for live mode) [Ollama](https://ollama.com)
with `ollama pull qwen3:8b` — or any OpenAI-compatible endpoint.
(Linux: run Ollama with `OLLAMA_HOST=0.0.0.0` so the agent container can reach it.)

```bash
cp .env.example .env
make up            # seeds the git history + starts 5 containers
make demo-break    # break prod (payment gateway outage)
```

Open http://localhost:8080 and watch the incident stream in. Then:

```bash
make demo-fix      # heal prod -> alert resolves -> postmortem publishes
```

No GPU / Ollama? `make demo-stub` runs the identical pipeline with a
deterministic scripted investigator.

## The 10-minute demo script

| When | Do | Audience sees |
|---|---|---|
| 0:00 | (pre-staged: `make up`, `make demo-check` green) | Healthy store, empty dashboard |
| 1:00 | `git -C demo-repo/shopapi log --oneline -8` | A team's commit history; the bad commit is buried, not HEAD |
| 2:00 | `make demo-break` | Checkout starts 500ing; narrate while Prometheus notices (~60–90s) |
| 3:00 | — | Incident slides into the dashboard, status pulses red |
| 3:30 | — | Timeline streams live: metrics → logs → `git log -S` → diff → runbook → impact |
| 5:30 | Point at the diagnosis card | Exact planted commit sha + author, matched runbook, computed impact, Slack brief |
| 6:30 | `make demo-fix` | Alert resolves; status flips green |
| 7:30 | — | Postmortem renders: metadata, impact, 1:1 event timeline, action items |
| 8:30 | Open `agent/app/investigation/loop.py` | 60-second code skim: guardrails, forced structured diagnosis, fallback |

Encore: `make demo-break FAILURE=slow_products` shows the latency-alert path
(N+1 bug), a completely different failure class.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `LLM_BASE_URL` | `http://host.docker.internal:11434/v1` | Any OpenAI-compatible endpoint |
| `LLM_API_KEY` | `ollama` | API key (any non-empty string for Ollama) |
| `LLM_MODEL` | `qwen3:8b` | Model name (`qwen3:4b` for 8GB machines) |
| `AGENT_MODE` | `live` | `live` or `stub` (deterministic, LLM-free) |
| `SLACK_WEBHOOK_URL` | *(empty)* | Slack incoming webhook; empty = console/dashboard card |
| `MAX_TOOL_CALLS` | `10` | Investigation tool budget |
| `RPS` | `10` | Load-generator traffic rate |

Hosted Qwen instead of local Ollama: set `LLM_BASE_URL=https://openrouter.ai/api/v1`,
`LLM_API_KEY=sk-or-...`, `LLM_MODEL=qwen/qwen3-32b` (fastest demo turns).

## How it works

- **Investigation loop** ([loop.py](agent/app/investigation/loop.py)) — an OpenAI
  tool-calling loop hardened for small open models: flat tool schemas, one tool
  per turn, result truncation, a forced terminal `submit_diagnosis` tool (works
  on every OpenAI-compatible backend, unlike `response_format`), validation with
  one repair round, and a deterministic fallback so the pipeline never dead-ends.
- **Tools** ([tools.py](agent/app/investigation/tools.py)) — PromQL queries, log
  tailing, `git log`/`git show`/`git log -S`, runbook search, impact calculation.
- **Event protocol** ([events.py](agent/app/events.py), [store.py](agent/app/store.py)) —
  every investigation step is persisted to SQLite *then* fanned out over SSE;
  the rowid doubles as the SSE id, so reconnects replay losslessly via
  `Last-Event-ID`, even across agent restarts.
- **Stub mode** ([stub.py](agent/app/investigation/stub.py)) — a scripted
  investigator with the same client surface as the openai SDK. It drives the
  real tools and extracts the real commit sha, deterministically. It powers the
  test suite and doubles as the demo fallback.

## Testing

```bash
pip install -e "./agent[test]"
make test    # unit + integration (stub LLM, no docker, no network)
make smoke   # fast CI gate: webhook fixture in -> postmortem out
```

CI runs the same targets on every push (`.github/workflows/ci.yml`).

## Project layout

```
agent/            the AI agent + dashboard (FastAPI, one container)
services/         shopapi (breakable demo store) + loadgen
ops/              Prometheus alert rules + Alertmanager routing
runbooks/         team runbooks the agent searches (3 real + 2 decoys)
scripts/          seed_history.py (planted git history) + inject/resolve
demo-repo/        generated: the seeded repo, also the shopapi build context
postmortems/      generated postmortem markdown files
```

## Limitations & next steps

- Single-service demo scope; multi-service correlation (traces) is the obvious extension.
- Runbook matching is lexical (BM25) — deliberate for explainability at this corpus
  size; embeddings become worthwhile at ~100+ runbooks.
- The agent diagnoses and recommends but doesn't auto-remediate; an
  approval-gated "disable the flag for me" action is the natural next feature.
- One incident per alert fingerprint; alert-storm correlation isn't modeled.
