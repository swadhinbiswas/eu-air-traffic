# Contributing

Thanks for taking a look. This is currently a solo project, but issues and pull
requests are welcome.

## Reporting problems

Open an issue at https://github.com/swadhinbiswas/eu-air-traffic/issues with
what you ran, what you expected, and what happened instead. If a pipeline step
failed, paste the failing command and the error output. Do not paste API keys,
tokens or `.env` contents; if a secret appears in a log, say which variable it
was without quoting its value.

For anything security related, please contact the maintainer directly rather
than opening a public issue.

## Setting up

```bash
uv sync --extra dev --extra stream --extra turso
cp .env.example .env      # fill in what you have; several sources work without keys
uv run pytest
```

Python 3.12 or newer. No paid account is needed to work on the code. The collector needs Kafka and OpenSky credentials
only if you want to run the full live path; the pipeline and test suite run
without them.

## Running the pipeline locally

```bash
MOCK_MODE=true uv run python -m pipelines.orchestrator
AIR_TRAFFIC_DUCKDB_PATH=$PWD/warehouse/air_traffic.duckdb uv run dbt build --project-dir dbt --profiles-dir dbt
```

`MOCK_MODE=true` generates synthetic records and never touches the real lake.
The upload paths refuse to run in mock mode on purpose, so mock data cannot
reach Hugging Face. Do not remove or weaken that guard.

## Checks before opening a pull request

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy config ingestion pipelines apps scripts --ignore-missing-imports
uv run pytest
uv run dbt build --project-dir dbt --profiles-dir dbt
```

All of these run in CI on every push. The dbt build must stay green; a failing
dbt test fails the pipeline run in production, not just CI.

## Code conventions

- Formatting and linting are handled by ruff, line length 100. Run
  `uv run ruff format .` before committing.
- Types are checked with mypy. New code should be typed, and public functions
  should have docstrings.
- Tests live in `tests/unit` and run with pytest. Add a test with behaviour
  fixes, especially for failure handling: most bugs here have been silent
  failures rather than crashes.
- Data is deduplicated rather than overwritten. When a value is unknown, keep it
  NULL instead of substituting a zero.
- Never commit `.env`, tokens, or credentials. If a change needs a new secret,
  add it to `.env.example` with an empty value and document it in the README.

## Pull requests

Keep them focused: one change per pull request, with the reason in the
description and any behaviour change called out. If a change alters a dataset
schema, mention it, since the Hugging Face dataset card is generated from the
column schemas and has to be regenerated with `make hf-card`.

## Support

Use the issue tracker for questions as well as bug reports. There is no chat
channel.
