# 📊 Polymarket-Deribit Arbitrage Engine (polyarbresearch)

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg?style=flat-square&logo=python&logoColor=white)
![Quantitative Trading](https://img.shields.io/badge/Quantitative-Trading-green.svg?style=flat-square)
![Status](https://img.shields.io/badge/Status-Production-brightgreen.svg?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-purple.svg?style=flat-square)

An institutional-grade mathematical arbitrage engine designed to exploit structural inefficiencies between decentralized binary prediction markets (Polymarket) and centralized linear options exchanges (Deribit). 

This repository contains the core execution scanners and delta-hedging simulators used to isolate risk-free Annualized Percentage Yield (APY) by Legging Synthetic Digital Spreads.

---

## 🔬 Core Theory
Polymarket offers pure binary options (resolving at $1.00 or $0.00). Deribit restricts true digital options for retail users, so we synthetically construct them using tight linear vertical spreads (e.g., $1,000-wide Call/Put Spreads). By normalizing the contract sizes, we replicate the $1.00 payout. If the combined cost of the Polymarket token and the Deribit options spread is less than $1.00, we execute the arbitrage.

*(For the complete mathematical breakdown, including how we resolve "Pin Risk" and Time Mismatches, refer to the [idea.md](./idea.md) academic summary).*

---

## ⚙️ The Execution Engines

This repository contains three independent execution engines, tailored for different levels of microstructure friction:

### 1. The Retail Scanner (`matrix_scanner.py`)
A live, real-time dashboard that streams Polymarket endpoints and 64 Deribit option chains via WebSockets.
* **Dynamic Danger Zones:** Calculates live Black-Scholes 1-Standard-Deviation probability cones using live Implied Volatility (`mark_iv`) and precise Time-to-Expiry (TTE) to avoid Pin Risk.
* **Net-of-Fees Math:** Automatically calculates the complex Deribit 12.5% fee cap, so profits shown are true net yields.
* **APY Annualization:** Automatically formats the return into a standardized APY based on the fraction of the year the capital is locked.

### 2. The Sniper Engine (`liquidity_scanner.py`)
An advanced upgrade to the Retail Scanner that introduces **Level-2 Order Book Analysis**.
* **Liquidity Mirage Filtering:** Options order books are notoriously thin. This engine parses exact Bid/Ask resting sizes. It will flag a trade as a `[LIQUIDITY MIRAGE]` if there isn't at least **1.0 BTC** of depth available to fill the synthetic spread.

### 3. The Institutional Simulator (`matrix_simulator.py`)
A Wall Street-level **Delta Hedging Simulator**. Instead of buying wide, illiquid option spreads to hedge Polymarket, this engine treats Polymarket as a pure mathematical Greek (`Delta`).
* **Binary Delta Calculus:** Calculates the total Binary Delta for 16 concurrent Polymarket positions using the Black-Scholes density function.
* **Perpetual Hedging:** Simulates continuous buying/selling of highly liquid **Bitcoin Perpetual Futures** (`BTC-PERPETUAL`) to keep the global portfolio Delta-Neutral, eating the "Gamma Bleed" to collect the arbitrage spread safely.

---

## 🚀 Usage

### Prerequisites
* Python 3.11+
* `websockets`
* `requests`
* **WARNING:** If running from a restricted jurisdiction (e.g., the United States), you **must** use a VPN to connect to the APIs. Both Polymarket (`gamma-api`) and Deribit WebSocket endpoints aggressively geoblock restricted IP addresses.

### Running the Scanners
```bash
# To run the core options scanner
python matrix_scanner.py

# To run the L2 liquidity-filtered scanner
python liquidity_scanner.py

# To run the Delta-Hedging simulator
python matrix_simulator.py
```
