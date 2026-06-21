---
label: ready-for-agent
type: AFK
---

# Arbitrage Window Identification & Final Visualization

## What to build

Finalize `analyzer.py` to calculate the total combined theoretical cost (Polymarket + TradFi spread) and flag the exact timestamps where the combined cost dips below $1.00. Update `visualize.py` to visually highlight these arbitrage windows on the graph. Verify against boundary tests.

## Acceptance criteria

- [ ] `analyzer.py` combines TradFi spread cost and Polymarket share cost.
- [ ] Timestamps where total cost < 1.00 are explicitly flagged.
- [ ] `visualize.py` highlights these windows clearly on the graph (e.g. shaded regions).
- [ ] Unit tests use boundary mock CSV datasets to ensure the analyzer accurately flags arbitrage windows.

## Blocked by

- [003-polygon-integration.md](file:///c:/Users/mailt/NewProject/.scratch/003-polygon-integration.md)
