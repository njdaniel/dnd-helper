.DEFAULT_GOAL := help

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy
ALEMBIC := $(VENV)/bin/alembic

.PHONY: help install migrate run cli test lint types check live

help: ## List the available targets
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z_-]+:.*## / {printf "  %-10s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Create the virtualenv and install development dependencies
	python3 -m venv $(VENV)
	$(PIP) install -e ".[dev]"

migrate: ## Apply all database migrations
	$(ALEMBIC) upgrade head

run: ## Start the Discord bot
	$(PYTHON) -m bot.main

cli: ## Run the CLI harness (pass arguments with ARGS="...")
	$(PYTHON) -m bot.cli $(ARGS)

test: ## Run the test suite
	$(PYTEST)

lint: ## Run Ruff lint and formatting checks
	$(RUFF) check .
	$(RUFF) format --check .

types: ## Run static type checks
	$(MYPY) bot/engine bot/db

check: lint types test ## Run all required development checks

live: ## Run the live Ollama conformance test
	OLLAMA_LIVE_TEST=1 $(PYTEST) -m live
