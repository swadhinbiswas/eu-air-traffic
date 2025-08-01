[« Back to Index](../00%20-%20Index.md)

# Dataset Versioning

Dataset versioning ensures reproducibility and allows rolling back in case of data corruption.

## How it works here
By leveraging Hugging Face Datasets for the Bronze layer, every push creates a Git commit. We can track data changes over time. For the warehouse (DuckDB), we rely on reproducible ETL pipelines: we can always rebuild the DuckDB file from the versioned Bronze/Silver data.

---
[« Back to Index](../00%20-%20Index.md)
