# Lab environment and test commands. Run "make help" for a list.

COMPOSE := docker compose
PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
STARTUP_TIMEOUT_SECONDS := 300

.PHONY: help venv up down reset ps logs test test-lab test-lifecycle

help:
	@echo "make venv            create .venv and install test dependencies"
	@echo "make up              start the lab and wait until every service is healthy"
	@echo "make down            stop the lab, keep the data"
	@echo "make reset           stop the lab, delete the data, start from the seed"
	@echo "make ps              show service status"
	@echo "make logs            follow service logs"
	@echo "make test            fast unit tests (no containers)"
	@echo "make test-lab        tests against the running lab"
	@echo "make test-lifecycle  stop/start/reset tests (slow, restarts the lab)"

.env:
	@echo "No .env file. Run: cp .env.example .env   and set the passwords." >&2
	@exit 1

START_LAB := $(COMPOSE) up --detach --build --wait --wait-timeout $(STARTUP_TIMEOUT_SECONDS)
START_FAILED_HINT := echo "Lab did not become healthy. If the first start failed part-way, the data volume is incomplete: run 'make reset'." >&2; exit 1

up: .env
	$(START_LAB) || { $(START_FAILED_HINT); }

down:
	$(COMPOSE) down

reset: .env
	$(COMPOSE) down --volumes
	$(START_LAB) || { $(START_FAILED_HINT); }

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --follow

test:
	$(PYTHON) -m pytest

test-lab:
	$(PYTHON) -m pytest -m lab

test-lifecycle:
	$(PYTHON) -m pytest -m lifecycle

venv:
	python3 -m venv .venv
	.venv/bin/pip install --quiet -r requirements-dev.txt
