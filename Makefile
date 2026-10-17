# finance-digest - curs BNR, ETF-uri globale si dobanzi in EUR, pe email
SHELL := /bin/bash
.DEFAULT_GOAL := help

STATE  ?= .local/state
REPORT ?= .local/report.json

.PHONY: help
help: ## Arata tintele disponibile
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install: ## Instaleaza dependintele (uv sync)
	uv sync

.PHONY: lint
lint: ## ruff check + format --check
	uv run ruff check .
	uv run ruff format --check .

.PHONY: fmt
fmt: ## ruff format + autofix
	uv run ruff check --fix .
	uv run ruff format .

.PHONY: test
test: ## Ruleaza pytest (doar fixture-uri, fara retea)
	uv run pytest

.PHONY: preview
preview: ## Randeaza emailul din datele de exemplu in preview.html si il deschide
	uv run finance render --report tests/fixtures/preview_report.json --prose tests/fixtures/preview_prose.json \
		--link https://github.com/teodorio95-portofolio/finance-digest/tree/data/digests --preview preview.html
	open preview.html

.PHONY: send-test
send-test: preview ## Trimite preview-ul pe email (MAIL_USERNAME / MAIL_PASSWORD din .env)
	@set -a; [ -f .env ] && . ./.env; set +a; \
	rm -rf .local/send-test && mkdir -p .local/send-test/digests && \
	cp preview.html .local/send-test/digests/2026-10-16.html && \
	cp preview.txt .local/send-test/digests/2026-10-16.txt && \
	uv run python -c "from pathlib import Path; from datetime import date; from finance import outbox; \
outbox.enqueue(Path('.local/send-test'), date(2026, 10, 16), 'Bani în euro: email de test')" && \
	uv run finance send --root .local/send-test

.PHONY: collect
collect: ## Colectare live cu state local in .local/ (pe Mac, hook-ul de egress poate bloca)
	uv run finance collect --state-dir $(STATE) --out $(REPORT)

.PHONY: run
run: ## Ruleaza digest.yml pe GitHub (FORCE=true trimite si fara date noi; MODE=weekly = rezumatul saptamanii)
	gh workflow run digest.yml -f force=$(or $(FORCE),false) -f mode=$(or $(MODE),daily)

.PHONY: probe
probe: ## Ruleaza probe.yml pe GitHub (status + numar de puncte per sursa)
	gh workflow run probe.yml

.PHONY: clean
clean: ## Sterge fisierele locale generate
	rm -rf .local preview.html preview.txt .pytest_cache .ruff_cache
