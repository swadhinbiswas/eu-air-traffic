.PHONY: setup bootstrap run test lint format dashboard dbt api streamlit verify quality clean dbt-docs dbt-test web web-data web-build web-install collector collector-once sink sink-loop live lake-pull-silver lake-push-silver motherduck reference-data

PYTHON ?= .venv/bin/python

setup:
	uv venv --python 3.12 --clear
	uv pip install -e ".[dev]"

bootstrap: setup
	mkdir -p warehouse/raw warehouse/bronze warehouse/silver warehouse/gold warehouse/quarantine warehouse/checkpoints
	cp -n .env.example .env || true
	@echo "Bootstrap complete. Dirs created."

# ── Pipeline commands ─────────────────────────────────────────────────────────
run:
	$(PYTHON) -m pipelines.orchestrator

run-full: run dbt

# ── VPS collector + live API ──────────────────────────────────────────────────
collector:
	$(PYTHON) -m services.collector

collector-once:
	$(PYTHON) -m services.collector --once

live:
	$(PYTHON) -m services.collector --no-api

# ── Kafka sink (Kafka → Bronze Parquet → Hugging Face) ────────────────────────
sink:
	$(PYTHON) -m services.sink

sink-loop:
	$(PYTHON) -m services.sink --loop

# ── Lake / warehouse publishing ───────────────────────────────────────────────
lake-pull-bronze:
	$(PYTHON) -m scripts.lake_sync pull-bronze

lake-pull-silver:
	$(PYTHON) -m scripts.lake_sync pull-silver

lake-push-silver:
	$(PYTHON) -m scripts.lake_sync push-silver

motherduck:
	$(PYTHON) -m scripts.publish_motherduck

# Regenerate the aircraft/operator reference data used by the classifier.
reference-data:
	$(PYTHON) -m scripts.build_reference_data

# ── API & Dashboard ───────────────────────────────────────────────────────────
api:
	$(PYTHON) -m uvicorn apps.main:app --host 0.0.0.0 --port 8000 --reload

streamlit:
	$(PYTHON) -m streamlit run streamlit_app.py --server.port 8501

dashboard:
	$(PYTHON) -m scripts.build_dashboard

# ── React dashboard (God's Eye View) ──────────────────────────────────────────
# Runtime data: the app reads from the API. `web-data` only generates the
# optional offline bundle for a fully static deployment.
web-data:
	$(PYTHON) -m scripts.build_web_bundle

web-install:
	cd web && npm install

web:
	cd web && npm run dev

web-build:
	cd web && npm run build

# ── dbt commands ──────────────────────────────────────────────────────────────
dbt:
	cd dbt && ../.venv/bin/dbt build --profiles-dir .

dbt-test:
	cd dbt && ../.venv/bin/dbt test --profiles-dir .

dbt-docs:
	cd dbt && ../.venv/bin/dbt docs generate --profiles-dir . && ../.venv/bin/dbt docs serve --profiles-dir .

dbt-freshness:
	cd dbt && ../.venv/bin/dbt source freshness --profiles-dir .

# ── Development ──────────────────────────────────────────────────────────────
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

quality-full: quality dbt-test dbt-freshness

# ── Docker ────────────────────────────────────────────────────────────────────
superset:
	docker compose -f docker/docker-compose.yml up -d superset

docker-up:
	docker compose -f docker/docker-compose.yml up -d

# ── Cleanup ──────────────────────────────────────────────────────────────────
clean:
	rm -rf warehouse/raw warehouse/bronze warehouse/silver warehouse/gold warehouse/quarantine warehouse/checkpoints
	rm -f warehouse/*.duckdb warehouse/*.duckdb.wal

clean-all: clean
	rm -rf dbt/target dbt/logs
