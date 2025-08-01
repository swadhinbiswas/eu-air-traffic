[« Back to Index](../00%20-%20Index.md)

# Data Lake

## Definition

A **Data Lake** is a centralized repository that stores structured, semi-structured, and unstructured data at any scale, in its raw native format.

Key principle: **Schema-on-read** (not schema-on-write). Data is stored first; structure is applied when you read it.

## Data Lake vs Data Warehouse

| | Data Lake | Data Warehouse |
|---|-----------|---------------|
| **Data state** | Raw, unprocessed | Processed, structured |
| **Schema** | Schema-on-read | Schema-on-write |
| **Users** | Data engineers, data scientists | Analysts, business users |
| **Data types** | Any (JSON, CSV, Parquet, images, logs) | Structured (tables with defined columns) |
| **Storage** | Object storage (S3, Hugging Face, MinIO) | Database engine (DuckDB, Redshift) |
| **Query** | Read files directly | SQL against tables |
| **Governance** | Loose (can become "data swamp") | Strict (well-defined models) |
| **Latency** | Direct file access | Optimized query engine |

## How This Project Uses a Data Lake

We use **Hugging Face Datasets** as our data lake for the Bronze and Silver layers:

```
Hugging Face Dataset Repository
├── bronze/
│   ├── flights/
│   │   ├── 2024/01/     ← Parquet files per month
│   │   └── 2024/02/
│   ├── weather/
│   ├── airports/
│   ├── fuel/
│   └── holidays/
│
├── silver/
│   ├── flights/
│   ├── weather/
│   └── airports/
│
└── gold/
    ├── fact_flights/
    ├── fact_delays/
    ├── dim_airport/
    ├── dim_airline/
    ├── dim_weather/
    └── dim_date/
```

## Why Hugging Face as a Data Lake?

| Feature | Hugging Face Datasets | Traditional S3 |
|---------|----------------------|----------------|
| **Cost** | Free (no storage cost) | $0.023/GB/month |
| **Versioning** | Built-in dataset cards + commits | Manual versioning (prefix folders) |
| **Schema discovery** | Parquet metadata auto-detected | Manual schema management |
| **Public access** | Public by default (good for portfolio) | IAM policy complexity |
| **API** | HTTP download + streaming | boto3 SDK |
| **Size limit** | 500 GB (plenty for flight data) | Unlimited |
| **Community** | ML datasets, not data engineering (unusual) | Standard DE platform |

## Data Lake Zones (Medallion Architecture in a Data Lake)

```text
┌─────────────────────────────────────────────┐
│                  Data Lake                    │
│                                               │
│  ┌──────────┐                               │
│  │  Bronze  │  Raw, immutable, source format │
│  └────┬─────┘                               │
│       │                                       │
│  ┌────┴─────┐                               │
│  │  Silver  │  Cleaned, validated, deduped  │
│  └────┬─────┘                               │
│       │                                       │
│  ┌────┴─────┐                               │
│  │   Gold   │  Business-ready, metric-rich │
│  └──────────┘                               │
│                                               │
│  ← Schema-on-read (Parquet metadata)         │
│  ← Not schema-on-write (no rigid DB schema)  │
└─────────────────────────────────────────────┘
```

## Preventing a "Data Swamp"

A data swamp is a disorganized data lake. Prevention strategies in this project:

| Strategy | Implementation |
|----------|---------------|
| **Folder convention** | `{layer}/{dataset}/{YYYY}/{MM}/` |
| **File naming** | `YYYY-MM-DD-Thhmm.parquet` (UTC) |
| **Validation gates** | Silver = validated; corrupt records quarantined |
| **Metadata** | Hugging Face dataset card = README per dataset |
| **Retention** | Bronze: keep 1 year; Silver/Gold: keep indefinitely |
| **Lineage** | Each Parquet file has generation timestamp in metadata |

## Quarantine Pattern

```python
# During Bronze → Silver transformation:
# Valid rows → Silver
# Invalid rows → Quarantine
import polars as pl

raw = pl.scan_parquet("bronze/flights/**/*.parquet")

validation_errors = validate_and_split(raw)

valid = validation_errors["valid"]
quarantine = validation_errors["errors"]

valid.collect().write_parquet("silver/flights/")
quarantine.collect().write_parquet("quarantine/flights/")
```

Quarantine is in a **separate Hugging Face dataset** (not in the main repo) to avoid polluting the lake.

## File Formats in the Lake

| Layer | Format | Compression | Rationale |
|-------|--------|-------------|-----------|
| Bronze | Parquet | Snappy | Fast read/write, preserves schema |
| Silver | Parquet | Zstd | Better compression, clean data deserves it |
| Gold | Parquet | Zstd | Query performance, used by DuckDB/dbt |

## Benefits Demonstrated

By using a data lake pattern:
1. **Raw data preserved** (Bronze immutable): replay pipeline from scratch
2. **Multiple consumers**: Polars (Python) and DuckDB (SQL) both read Parquet
3. **Cost awareness**: Hugging Face free vs S3 paid — demonstrates creative solutions
4. **Schema evolution**: New fields added to APIs → Bronze auto-accepts; Silver transforms selectively

---
[« Back to Index](../00%20-%20Index.md)
