.PHONY: help up down reset bootstrap migrate provision test conformance handoff gates lint typecheck ci baseline-api-schema

VENV := .venv/bin

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up:            ## Start postgres + pgbouncer + minio + kafka
	docker compose up -d
	@echo "waiting for health..." && sleep 30 && docker compose ps

down:          ## Stop the stack (keeps volumes)
	docker compose down

reset:         ## Destroy and rebuild the stack from empty. Bootstrap re-runs.
	docker compose down -v
	docker compose up -d
	@sleep 32
	$(MAKE) migrate

migrate:       ## Apply the shared chain, then every tenant, then check drift
	$(VENV)/python -m src.cli migrate

baseline-api-schema: ## Record manually applied API migrations after schema verification
	$(VENV)/python scripts/baseline_api_schema.py --tenant acme --tenant globex

provision:     ## make provision SLUG=acme
	$(VENV)/python -m src.cli provision $(SLUG)

test:          ## Full suite
	$(VENV)/python -m pytest tests -q

conformance:   ## Isolation gates only (ARCHITECTURE.md 10.1-10.8)
	$(VENV)/python -m pytest tests -q -m conformance

handoff:       ## Handoff gates only (ARCHITECTURE.md 10.9-10.12)
	$(VENV)/python -m pytest tests -q -m handoff

gates:         ## The two gates that must also run POST-MIGRATE in every environment
	$(VENV)/python scripts/check_isolation.py
	$(VENV)/python scripts/check_static.py

lint:
	$(VENV)/ruff check src scripts tests

typecheck:
	$(VENV)/mypy src

ci: lint typecheck test gates   ## Everything CI runs
