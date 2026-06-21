---
label: ready-for-agent
type: AFK
---

# Polymarket Data Ingestion & Integration

## What to build

Implement `fetch_polymarket.py` to pull historical data for the target event (e.g., NVDA earnings) via their REST API. Hook this real data into the pipeline replacing the mock Polymarket data. Verify that the pipeline runs and graphs the real Polymarket "Yes" share price.

## Acceptance criteria

- [ ] `fetch_polymarket.py` correctly fetches minute-level data from Polymarket API.
- [ ] Data is cleaned and standardized into CSV format.
- [ ] Pipeline runs end-to-end with real prediction market data and mock options data.
- [ ] Graph successfully displays the Polymarket data over time.

## Blocked by

- [001-pipeline-skeleton.md](file:///c:/Users/mailt/NewProject/.scratch/001-pipeline-skeleton.md)
