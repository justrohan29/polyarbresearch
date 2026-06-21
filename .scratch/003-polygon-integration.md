---
label: ready-for-agent
type: AFK
---

# Polygon TradFi Ingestion & Spread Calculation

## What to build

Implement `fetch_polygon.py` to pull minute-level options chain data for the target stock. Update `analyzer.py` to align these timestamps with the Polymarket data, handling missing minutes and market closures. Calculate the actual 1-Point Bear Put Spread cost based on the real options data and graph it alongside the prediction market data.

## Acceptance criteria

- [ ] `fetch_polygon.py` correctly fetches minute-level data from Polygon.io.
- [ ] Data is cleaned and standardized into CSV format.
- [ ] `analyzer.py` aligns timestamps between TradFi and Polymarket data seamlessly.
- [ ] `analyzer.py` calculates the 1-Point Bear Put Spread net premium.
- [ ] Graph successfully displays both data streams over the aligned timeline.

## Blocked by

- [002-polymarket-integration.md](file:///c:/Users/mailt/NewProject/.scratch/002-polymarket-integration.md)
