SHELL := /bin/bash
FAILURE ?= payment_outage

.PHONY: seed up down nuke demo-break demo-fix demo-check demo-stub drill logs open test smoke

## -- lifecycle ---------------------------------------------------------------

seed:              ## build the seeded git history (demo-repo/)
	python3 scripts/seed_history.py

up: seed           ## seed + start the full stack
	mkdir -p data postmortems   # pre-create user-owned (docker would create them as root on Linux)
	docker compose up -d --build
	@echo ""
	@echo "  dashboard:     http://localhost:8080"
	@echo "  shopapi:       http://localhost:8000/docs"
	@echo "  prometheus:    http://localhost:9090"
	@echo "  alertmanager:  http://localhost:9093"
	@echo ""
	@echo "  break prod:    make demo-break   (FAILURE=payment_outage|slow_products|orders_crash)"

down:              ## stop the stack (incident history survives in ./data)
	docker compose down

nuke:              ## stop everything and delete all generated state
	docker compose down -v
	rm -rf demo-repo data postmortems/*.md

## -- demo choreography -------------------------------------------------------

demo-break:        ## inject a failure (enables the flag gating a planted bad commit)
	./scripts/inject.sh $(FAILURE)

demo-fix:          ## disable all failure flags; alert resolves -> postmortem
	./scripts/resolve.sh

demo-check:        ## pre-demo sanity: agent up? LLM reachable?
	curl -s http://localhost:8080/healthz | python3 -m json.tool

demo-stub:         ## restart the agent in stub mode (no LLM needed)
	AGENT_MODE=stub docker compose up -d --build agent

drill:             ## inject + measure seconds until the alert fires
	@if curl -s http://localhost:9093/api/v2/alerts | grep -q '"state":"active"'; then \
	  echo "an alert is already active — run 'make demo-fix' and wait for it to resolve first"; exit 1; fi
	./scripts/inject.sh $(FAILURE)
	@start=$$(date +%s); \
	echo "waiting for Alertmanager to fire..."; \
	until curl -s http://localhost:9093/api/v2/alerts | grep -q '"state":"active"'; do sleep 2; done; \
	echo "alert firing after $$(( $$(date +%s) - start ))s"

logs:              ## follow the agent's logs
	docker compose logs -f agent

open:
	open http://localhost:8080

## -- tests --------------------------------------------------------------------

test:              ## unit + integration tests (stub LLM, no docker)
	cd agent && python3 -m pytest tests/unit tests/integration -q

smoke:             ## fast CI gate: full pipeline in stub mode
	cd agent && python3 -m pytest tests/smoke -q
