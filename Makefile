PYTHON  = .venv/bin/python
PIP     = .venv/bin/pip
SOURCE ?= all

.PHONY: help setup run dry-run source test-greenhouse test-ashby lint clean clean-runs

# ── Default target ────────────────────────────────────────────────────────────
help:
	@echo ""
	@echo "  JobPilot — available commands"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36mmake %-18s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ── Setup ─────────────────────────────────────────────────────────────────────
setup: ## Create venv, install deps, install Playwright, create working dirs
	python3 -m venv .venv
	$(PIP) install --upgrade pip --quiet
	$(PIP) install -r requirements.txt --quiet
	$(PYTHON) -m playwright install chromium
	mkdir -p runs manual_queue history data
	@echo ""
	@echo "  Setup complete."
	@echo "  Next: copy .env.example to .env and fill in ANTHROPIC_API_KEY + SHEET_ID"
	@echo ""

# ── Pipeline ──────────────────────────────────────────────────────────────────
run: ## Discover, score, review, apply, sync — all sources
	$(PYTHON) main.py

run-v: ## Same as run with verbose debug logging
	$(PYTHON) main.py -v

dry-run: ## Discover + gate only — no LLM calls, no sheet writes
	$(PYTHON) main.py --dry-run

source: ## Run one source: make source SOURCE=greenhouse
	$(PYTHON) main.py --source $(SOURCE)

source-v: ## Run one source with verbose logging: make source-v SOURCE=ashby
	$(PYTHON) main.py --source $(SOURCE) -v

# ── Form filler tests ─────────────────────────────────────────────────────────
test-greenhouse: ## Test Greenhouse filler — opens browser, fills form, dry_run=true
	$(PYTHON) test_apply.py greenhouse

test-ashby: ## Test Ashby filler — opens browser, fills form, dry_run=true
	$(PYTHON) test_apply.py ashby

# ── Code quality ──────────────────────────────────────────────────────────────
test: ## Run the test suite
	.venv/bin/pytest tests/ -v

test-fast: ## Run the test suite without verbose output
	.venv/bin/pytest tests/

lint: ## Run ruff linter over src/ and main.py
	.venv/bin/ruff check src/ main.py test_apply.py

lint-fix: ## Auto-fix lint issues where possible
	.venv/bin/ruff check --fix src/ main.py test_apply.py

# ── Cleanup ───────────────────────────────────────────────────────────────────
clean: ## Remove __pycache__ and .pyc files
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -not -path './.venv/*' -delete 2>/dev/null || true

clean-runs: ## Delete all run evidence, screenshots, and manual queue files
	rm -rf runs/* manual_queue/*
	@echo "  runs/ and manual_queue/ cleared."
