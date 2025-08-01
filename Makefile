.PHONY: setup bootstrap run test lint format dashboard dbt api streamlit verify quality clean

PYTHON ?= .venv/bin/python

setup:
	uv venv --python 3.12 --clear
	uv pip install -e ".[dev]"

bootstrap: setup
	mkdir -p warehouse/raw warehouse/bronze warehouse/silver warehouse/gold warehouse/quarantine warehouse/checkpoints
	cp -n .env.example .env || true
	@echo "Bootstrap complete. Dirs created."

run:
	$(PYTHON) -m pipelines.orchestrator

api:
	$(PYTHON) -m uvicorn apps.main:app --host 0.0.0.0 --port 8000 --reload

streamlit:
	$(PYTHON) -m streamlit run streamlit_app.py --server.port 8501

dbt:
	cd dbt && ../.venv/bin/dbt build --profiles-dir .

test:
	$(PYTHON) -m pytest tests/ -v

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m ruff format .

typecheck:
	$(PYTHON) -m mypy config ingestion pipelines apps scripts --ignore-missing-imports

verify: lint typecheck test

quality:
	$(PYTHON) -m pipelines.quality

dashboard:
	$(PYTHON) -m scripts.build_dashboard

superset:
	docker compose -f docker/docker-compose.yml up -d superset

docker-up:
	docker compose -f docker/docker-compose.yml up -d

clean:
	rm -rf warehouse/raw warehouse/bronze warehouse/silver warehouse/gold warehouse/quarantine warehouse/checkpoints
	rm -f warehouse/*.duckdb warehouse/*.duckdb.wal
