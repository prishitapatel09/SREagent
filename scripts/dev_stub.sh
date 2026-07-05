#!/usr/bin/env bash
# Run the agent locally without docker (default: stub mode).
# Useful for dashboard development and demo rehearsal on a machine without Docker.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

[ -d demo-repo/shopapi ] || python3 scripts/seed_history.py

export AGENT_MODE="${AGENT_MODE:-stub}"
export DB_PATH="${DB_PATH:-$ROOT/data/dev.db}"
export REPO_PATH="${REPO_PATH:-$ROOT/demo-repo/shopapi}"
export RUNBOOKS_DIR="$ROOT/runbooks"
export SERVICE_LOG_PATH="${SERVICE_LOG_PATH:-$ROOT/data/shopapi.log}"
export POSTMORTEM_DIR="$ROOT/postmortems"
export PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"

mkdir -p "$ROOT/data"
touch "$SERVICE_LOG_PATH"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3

cd agent
exec "$PY" -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8080
