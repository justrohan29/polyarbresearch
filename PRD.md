## Problem Statement

The user wants to exploit the liquidity and regulatory fragmentation between decentralized prediction markets (like Polymarket) and traditional finance options (TradFi). Spreads on identical contracts can reach significant percentage points because regulatory walls prevent efficient capital flow. The user needs a system to mathematically prove and eventually extract this risk-free return by acting as the bridge between these isolated liquidity pools. However, matching continuous payouts (stock options) to binary payouts (prediction markets) is mathematically complex, and execution carries significant risk.

## Solution

A multi-phase cross-market arbitrage machine. Phase 1 is a historical backtester written in Python that fetches minute-level historical data from Polymarket (REST API) and TradFi options chains (Polygon.io). It calculates the theoretical net cost of the Prediction "Yes" share + the TradFi Bear Put Spread at every minute to identify exactly how often the total cost dips below $1.00 (guaranteeing arbitrage) and the duration of these arbitrage windows. This will serve as the mathematical proof before building low-latency live execution systems.

## User Stories

1. As a quantitative researcher, I want to fetch minute-level historical options chain data for a specific stock (e.g., NVDA) from Polygon.io, so that I have the TradFi side of the pricing equation.
2. As a quantitative researcher, I want to fetch historical price data for a specific binary event (e.g., NVDA earnings) from Polymarket, so that I have the prediction market side of the pricing equation.
3. As a quantitative researcher, I want to align the timestamps of both datasets, so that I can compare the exact prices of both markets at any given minute.
4. As a quantitative researcher, I want the system to calculate the cost of a Tight Vertical Spread (Bear Put Spread) at a specific strike, so that I can translate the continuous TradFi payout into a capped binary-style payout.
5. As a quantitative researcher, I want the system to calculate the combined theoretical cost of the TradFi Bear Put Spread and the Polymarket "Yes" share, so that I know the total cost of the arbitrage trade.
6. As a quantitative researcher, I want the system to flag timestamps where the total combined cost is less than $1.00, so that I can identify guaranteed arbitrage windows.
7. As a quantitative researcher, I want to visualize the theoretical net cost over time on a graph, so that I can see the frequency and duration of the arbitrage windows.

## Implementation Decisions

- **Language**: Python for the data extraction and analysis pipeline.
- **Data APIs**: Polymarket REST API (or Gamma API) for prediction market data. Polygon.io API for minute-level options data.
- **Target Event**: NVDA earnings (specific date to be configured).
- **Libraries**: `pandas` for data manipulation, timestamp alignment, and analysis. `matplotlib` or `plotly` for visualization.
- **Modules**:
  - `config.py`: Centralized configuration for target dates, strikes, and API keys.
  - `fetch_polymarket.py`: Module to pull and clean Polymarket data into a standard CSV format.
  - `fetch_polygon.py`: Module to pull and clean TradFi options data into a standard CSV format.
  - `analyzer.py`: The core logic engine that calculates the spread cost and identifies arbitrage windows.
  - `visualize.py`: Generates the time-series graphs.

## Testing Decisions

- **Test Philosophy**: Tests should focus on the external behavior of the modules (e.g., does the analyzer correctly identify a sub-$1.00 spread given mock data?) rather than implementation details (like the specific pandas dataframe merging method).
- **Modules to be tested**:
  - The calculation logic in `analyzer.py` (ensuring the max(K1-S, 0) - max(K2-S, 0) math is perfectly translated and cost is summed correctly).
  - The timestamp alignment logic (handling missing minutes, market closures).
- **Test Seams**: We will test at the boundary of data fetching and analysis. By creating mock CSV datasets with known synthetic arbitrage windows, we can inject them directly into the `analyzer.py` module to verify it outputs the correct metrics and graphs without hitting live APIs.

## Out of Scope

- Live trading or WebSocket connections (reserved for Phase 2).
- Real-time dashboard (reserved for Phase 3).
- Direct execution of orders on brokerages or crypto wallets.

## Further Notes

- This system relies on the assumption that the 1-Point Bear Put Spread perfectly replicates the binary payout, minus the translation friction (if the stock lands exactly between the strikes).
- Capital inefficiency and bid-ask spreads will be significant factors in Phase 2, but Phase 1 focuses purely on the theoretical mid-price or assumed spread proof.
