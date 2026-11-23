PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)
UVICORN := $(PY) -m uvicorn

.PHONY: reset-data test run api api-dev web-dev web-build install

install:            ## python package + web deps (in ./.venv — make targets use it)
	@test -d .venv || python3 -m venv .venv
	.venv/bin/pip install -e ".[api]" pytest
	cd web && npm install

test:               ## fast tier, in parallel (skips the slow campaign and replay tests)
	$(PY) -m pytest tests/ -q -n auto -m "not slow"

test-full:          ## everything, in parallel: the gate before a push
	$(PY) -m pytest tests/ -q -n auto

test-ci:            ## the whole suite in CI's `core` environment (no RDKit, NumPy, ...): run before a push
	scripts/ci_core.sh

test-live:          ## adapters against the REAL services (network); also detects API drift against tests/fixtures/api
	PROEF_LIVE=1 $(PY) -m pytest tests/live -q

record-fixtures:    ## refresh the recorded real API responses (network)
	$(PY) scripts/record_api_fixtures.py

smoke-real:         ## one real-model campaign, asserting the invariants (uses model quota; needs PROEF_LLM_PRESET/MODEL)
	$(PY) scripts/smoke_real_model.py

reset-data:         ## back up then wipe local provenance + workbench state
	./scripts/reset_data.sh

run:                ## start Proef: backend + built SPA on :8000 (PORT=... to change)
	cd web && npm run build
	PYTHONPATH=src $(UVICORN) proef.api.app:app --port $(or $(PORT),8000)

api:                ## alias of run, kept for older docs and habits
	@$(MAKE) --no-print-directory run

api-dev:            ## backend only (auto-reload)
	PYTHONPATH=src $(UVICORN) proef.api.app:app --port 8000 --reload

web-dev:            ## Vite dev server on :5173 (proxies /api to :8000)
	cd web && npm run dev

sample:            ## seed ./.sample with two simulated campaigns and run the workbench on :8000
	$(PY) scripts/seed_sample_workspace.py .sample
	cd .sample && PROEF_DATA_DIR=.data ../$(UVICORN) proef.api.app:app --port $(or $(PORT),8000)

api-types:          ## regenerate the typed TS client from the backend's OpenAPI
	$(PY) scripts/export_openapi.py && cd web && npm run gen:api

web-build:          ## production SPA build
	cd web && npm run build
