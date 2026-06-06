.PHONY: install dev test evals docker-build docker-up docker-test docker-evals lint format

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

install:
	python3.11 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

dev:
	$(VENV)/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	$(VENV)/bin/pytest tests/ -v --cov=app --cov-report=term-missing

evals:
	EVAL_PROVIDER=$${EVAL_PROVIDER:-mock} $(VENV)/bin/pytest evals/test_evals.py -v

docker-build:
	docker build -t creative-review-api .

docker-up:
	docker-compose up --build

docker-test:
	docker-compose --profile test run --rm test

docker-evals:
	docker-compose --profile test run --rm evals

lint:
	$(VENV)/bin/python -m py_compile $$(find app -name "*.py")
	@echo "Lint OK"

format:
	$(VENV)/bin/python -m black app/ tests/ evals/ 2>/dev/null || true
