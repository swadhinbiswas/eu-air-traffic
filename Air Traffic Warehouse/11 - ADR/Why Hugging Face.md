[« Back to Index](../00%20-%20Index.md)

# ADR: Why Hugging Face Datasets?

**Context**: We need a place to store our Data Lake (Parquet files).

**Options Considered**: AWS S3, Google Cloud Storage, Local Filesystem, Hugging Face Hub.

**Decision**: Hugging Face Hub.

**Rationale**:
- Cloud storage (S3/GCS) costs money and requires setting up IAM roles, making reproduction harder for others.
- Hugging Face provides free hosting for open datasets, complete with version control and an API to download the data directly into Pandas/Polars.

---
[« Back to Index](../00%20-%20Index.md)
