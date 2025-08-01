[« Back to Index](../00%20-%20Index.md)

# Compression

Data compression reduces storage costs and improves I/O performance.

## Snappy vs GZIP vs ZSTD
- **Snappy**: Fast compression/decompression, lower ratio. Great for active processing.
- **GZIP**: High compression ratio, slower speed. Good for archival.
- **ZSTD (Zstandard)**: Excellent balance of high compression ratio and fast decompression.

## Our Choice
We use **ZSTD** or **Snappy** for our Parquet files to optimize the balance between storage size and DuckDB query performance.

---
[« Back to Index](../00%20-%20Index.md)
