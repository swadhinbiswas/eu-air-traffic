# Hugging Face dataset lake

The pipeline's durable copy lives as a public dataset at
https://huggingface.co/datasets/swadhinbiswas/air-traffic (MIT).
It holds timestamped Bronze intake windows from the collector and the
eleven Silver snapshots the lake pipeline replaces roughly every 15 minutes.

## Layout

- `bronze/parquet/<source>/<source>_YYYY-MM-DDTHHMMSSffffffZ.parquet` — immutable windows exactly as drained from Kafka.
- `bronze/raw/<source>/<date>/*.jsonl` — the same records before Parquet conversion. Download a prefix such as `bronze/parquet/flights/` for history.
- `silver/<source>/data.parquet` (and `silver/airports/airports.parquet`) — one snapshot per source, replaced in place: `positions`, `flights`, `weather`, `weather_forecast`, `weather_taf`, `fuel`, `routes`, `airports`, `holidays`, `aircraft`, `notams`, `emissions`.

Gold marts and the serving tables are not stored here; dbt output lives in
MotherDuck and the read-only copy in Turso. The Eurostat airport-traffic
benchmark and dbt Gold marts are documented with the pipeline instead.

## Working with it locally

Local paths mirror the repo: `warehouse/bronze/<source>/…`,
`warehouse/silver/<source>/…`.

```bash
uv run python -m scripts.lake_sync pull-silver   # Hub silver → warehouse/silver/
uv run python -m scripts.lake_sync pull-bronze   # Hub bronze → warehouse/bronze/
uv run python -m scripts.lake_sync push-silver   # warehouse/silver/ → Hub silver/
uv run pytest                                    # 120+ tests
```

`push-silver` refuses mock data (`MOCK_MODE=true`) and fails the run unless
every file lands; unchanged files are no-ops on the Hub, not failures.

## Dataset card

The Hub landing page (`README.md` there) is generated, not hand-written:

```bash
uv run python -m scripts.hf_dataset_card          # print the card
uv run python -m scripts.hf_dataset_card --push   # upload README.md to the Hub
```

Rules the generator enforces, so reviews don't have to: it writes only
`README.md` at the repo root (no `README.yaml` sidecar), it refuses any repo
other than `swadhinbiswas/air-traffic`, it refuses a private repo, and it
skips cleanly without `HF_TOKEN`. Field lists come from the column schemas in
`scripts/hf_dataset_card.py`, so a schema change there is what updates the card.

## Cadence and freshness

The collector writes Bronze continuously (positions about every 5 minutes,
departures twice an hour, arrivals backfilled nightly). The lake pipeline
replaces each Silver snapshot every 15 minutes. Age therefore varies by
source: positions are minutes old, same-day arrivals lag by design. Nothing
here is deleted on the Hub; old Bronze windows stay for replay.
