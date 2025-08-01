[« Back to Index](../00%20-%20Index.md)

# Extraction

Extraction is the process of pulling data from source systems.

## Sources
- **Aviation APIs**: Real-time flight tracking.
- **Weather APIs**: Hourly weather conditions.
- **Static Files**: Airport and airline metadata.

## Challenges
- Rate limits on free API tiers.
- Network failures.
- Pagination and changing API structures.

## Solution
We implement robust `Collectors` with retry mechanisms (exponential backoff) and pagination handling. Data is saved exactly as received (raw) to the Bronze layer.

---
[« Back to Index](../00%20-%20Index.md)
