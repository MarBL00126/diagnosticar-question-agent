# Development tasks. Needs only `python3` on PATH (and docker for docker-build).
# Every Python tool runs from the project's virtualenv, created on first use.

PYTHON ?= python3
VENV ?= .venv
PY = $(VENV)/bin/python
STAMP = $(VENV)/.installed
IMAGE ?= diagnosticar-question-agent:dev
KB ?= data/kb.sqlite
# A local diagnostic.ar model service; override to point `make run` elsewhere.
PREGUNTAS_MODEL_SERVICE_URL ?= http://127.0.0.1:18021

.PHONY: install lint format format-check typecheck test test-live kb run docker-build ci

install: $(STAMP)

$(STAMP): pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --quiet -e '.[dev]'
	touch $(STAMP)

lint: $(STAMP)
	$(PY) -m ruff check .

format: $(STAMP)
	$(PY) -m ruff check --fix --select I .
	$(PY) -m ruff format .

format-check: $(STAMP)
	$(PY) -m ruff format --check .

typecheck: $(STAMP)
	$(PY) -m mypy

test: $(STAMP)
	$(PY) -m pytest -m 'not live'

# Spends Claude quota and needs the claude CLI logged in.
test-live: $(STAMP)
	$(PY) -m pytest -m live

kb: $(STAMP)
	$(PY) -m diagnosticar_preguntas.kb.build --download --out $(KB)
	cat $(KB).sha256

# Local development only: no token, so unauthenticated calls must be allowed explicitly.
run: $(STAMP)
	PREGUNTAS_MODEL_SERVICE_URL='$(PREGUNTAS_MODEL_SERVICE_URL)' PREGUNTAS_ALLOW_UNAUTHENTICATED=true \
		$(PY) -m diagnosticar_preguntas.api

docker-build:
	docker build -t $(IMAGE) .

ci: lint format-check typecheck test
