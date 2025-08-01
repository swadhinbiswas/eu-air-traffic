[« Back to Index](../00%20-%20Index.md)

# Hugging Face Datasets

We use Hugging Face (HF) Hub as our raw data lake (Bronze layer). While unconventional for typical enterprise DW (which use S3/GCS), it's perfect for this open-source portfolio project.

## Why Hugging Face?
- **Free Storage**: Generous limits for open datasets.
- **Versioning**: Git-backed versioning for datasets.
- **Accessibility**: Anyone can easily download the dataset to reproduce this project using the `datasets` library.
- **Parquet Support**: Natively supports Parquet formats.

## Implementation
Our ingestion scripts push daily partitioned Parquet files to a dedicated dataset repository on HF Hub.

---
[« Back to Index](../00%20-%20Index.md)
