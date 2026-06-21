---
label: ready-for-agent
type: AFK
---

# End-to-End Pipeline Skeleton (Mock Data)

## What to build

Build the initial `config.py`, `analyzer.py`, and `visualize.py` skeleton. Feed it synthetic mock CSV data for both Polymarket and Polygon. `analyzer.py` will align the mock timestamps and apply the `max(K1-S, 0) - max(K2-S, 0)` spread calculation logic (dummy calculation for now, just sum them). `visualize.py` will output a basic graph. This establishes the testable boundaries of the system.

## Acceptance criteria

- [ ] `config.py` contains basic target configuration.
- [ ] Pipeline runs end-to-end via a main entrypoint.
- [ ] Uses dummy mock data for prediction market and options chain.
- [ ] `visualize.py` outputs a basic graph.
- [ ] Tests verify execution boundaries using mock CSV data.

## Blocked by

None - can start immediately
