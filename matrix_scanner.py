import socket

# -------------------------------------------------------------
# DNS Bypass for ISP Sinkholing (Jio / Indian ISP blocking fix)
# Directly resolves Polymarket hostnames to official Cloudflare Anycast IPs
# -------------------------------------------------------------
POLYMARKET_IPS = {
    "gamma-api.polymarket.com": "172.64.153.51",
    "clob.polymarket.com": "172.64.153.51",
    "ws-subscriptions-clob.polymarket.com": "172.64.153.51",
}
_orig_getaddrinfo = socket.getaddrinfo

def _custom_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host in POLYMARKET_IPS:
        host = POLYMARKET_IPS[host]
    return _orig_getaddrinfo(host, port, family, type, proto, flags)

socket.getaddrinfo = _custom_getaddrinfo

import asyncio
import websockets
import requests
import json
import time
import os
import math
import webbrowser
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# Enable VT100 Escape Codes for Windows Terminal Colors
if os.name == 'nt':
    os.system('color')

RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
RESET = '\033[0m'

# User constraint: strikes 74k to 94k even numbers only
STRIKES = [74, 76, 78, 80, 82, 84, 86, 88, 90, 92, 94]
SPREAD_WIDTH = 2000.0  # Widen spread to $2,000 for halved fee overhead

# Next active days matching open Polymarket events and available Deribit expiries
now_utc = datetime.now(timezone.utc)
# Daily Polymarket event resolves at 16:00 UTC; if current UTC hour is >= 16, start from tomorrow
start_offset = 1 if now_utc.hour >= 16 else 0

# Query Deribit active expiries so we only track dates with real option contracts
deribit_expiries = set()
try:
    _r = requests.get("https://www.deribit.com/api/v2/public/get_instruments?currency=BTC&kind=option&expired=false", timeout=5)
    if _r.ok:
        for _inst in _r.json().get("result", []):
            _parts = _inst["instrument_name"].split("-")
            if len(_parts) >= 2:
                deribit_expiries.add(_parts[1])
except Exception:
    pass

DATES = []
for i in range(10):
    dt = now_utc + timedelta(days=i + start_offset)
    next_day = dt + timedelta(days=1)
    d_part = next_day.day
    b_part = next_day.strftime('%b%y').upper()
    eng = f"{d_part}{b_part}"
    if not deribit_expiries or eng in deribit_expiries:
        DATES.append(dt)
    if len(DATES) >= 5:
        break

if not DATES:
    DATES = [(now_utc + timedelta(days=i + start_offset)) for i in range(3)]

def get_engulfing_date_str(dt: datetime) -> str:
    # Deribit daily options expire 08:00 UTC the day AFTER Polymarket event
    next_day = dt + timedelta(days=1)
    return f"{next_day.day}{next_day.strftime('%b%y').upper()}"

# Global in-memory state
state: Dict[str, Any] = {
    "btc_usd": None,
    "last_print": 0,
    "options": {},       # ticker -> {ask, bid, mark, mark_iv}
    "poly_tokens": {},   # token_id -> {bid, ask, mid, last}
    "token_to_market": {} # token_id -> market_ref
}

# Construct markets
markets: List[Dict[str, Any]] = []
token_ids_to_subscribe: List[str] = []

for dt in DATES:
    month = dt.month
    d = dt.day
    engulfing_date = get_engulfing_date_str(dt)
    month_str = dt.strftime('%B').lower()
    event_slug = f"bitcoin-above-on-{month_str}-{d}-{dt.year}"
    
    # Polymarket daily resolution: 16:00 UTC (12:00 PM EST)
    resolution_time = datetime(dt.year, month, d, 16, 0, 0, tzinfo=timezone.utc)

    for s in STRIKES:
        m = {
            "id": f"{d}_{s}",
            "date": d,
            "month": month,
            "year": dt.year,
            "month_name": dt.strftime('%B').capitalize(),
            "date_str": f"{dt.strftime('%b %d').upper()}",
            "strike": s * 1000,
            "strike_str": f"${s}k → ${s+2}k",
            "upper_strike": (s + 2) * 1000,
            "poly_slug": f"bitcoin-above-{s}k-on-{month_str}-{d}-{dt.year}",
            "event_slug": event_slug,
            "resolution_time": resolution_time,
            
            # Token IDs from Polymarket CLOB
            "token_yes": None,
            "token_no": None,
            
            # Poly Quotes
            "poly_yes_ask": None,
            "poly_yes_bid": None,
            "poly_no_ask": None,
            "poly_no_bid": None,
            
            # $2,000 Wide Deribit Leg Tickers
            "put_long": f"BTC-{engulfing_date}-{(s+2)}000-P",
            "put_short": f"BTC-{engulfing_date}-{s}000-P",
            "call_long": f"BTC-{engulfing_date}-{s}000-C",
            "call_short": f"BTC-{engulfing_date}-{(s+2)}000-C",
        }
        markets.append(m)

# -------------------------------------------------------------
# Black-Scholes Cumulative Normal & Probabilistic Pin Risk
# -------------------------------------------------------------
def normal_cdf(x: float) -> float:
    """Standard Normal Cumulative Distribution Function using erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def calculate_pin_risk(S: float, K1: float, K2: float, T: float, iv: float = 0.50) -> float:
    """
    Computes exact probability of BTC spot finishing between K1 and K2 at expiry:
    P(K1 <= S_T <= K2) = |N(d2(K1)) - N(d2(K2))|
    """
    if T <= 0 or S <= 0 or iv <= 0:
        return 1.0 if (K1 <= S <= K2) else 0.0
    
    vol_sqrt_t = iv * math.sqrt(T)
    d2_k1 = (math.log(S / K1) - 0.5 * (iv ** 2) * T) / vol_sqrt_t
    d2_k2 = (math.log(S / K2) - 0.5 * (iv ** 2) * T) / vol_sqrt_t
    
    prob_pin = abs(normal_cdf(d2_k1) - normal_cdf(d2_k2))
    return max(0.0, min(1.0, prob_pin))

# -------------------------------------------------------------
# Dual-Mode Spread Calculations (Taker vs Maker)
# -------------------------------------------------------------
def calculate_deribit_spread(long_ticker: str, short_ticker: str, btc: float):
    """
    Returns (taker_cost_usd, maker_cost_usd) for a $2,000-wide spread normalized to $1.00 payout.
    """
    opt_l = state["options"].get(long_ticker, {})
    opt_s = state["options"].get(short_ticker, {})

    l_ask = opt_l.get("ask")
    l_bid = opt_l.get("bid")
    l_mark = opt_l.get("mark")

    s_ask = opt_s.get("ask")
    s_bid = opt_s.get("bid")
    s_mark = opt_s.get("mark")

    # We need prices for both legs
    if (l_ask is None and l_mark is None) or (s_bid is None and s_mark is None):
        return None, None

    # --- TAKER MODE (Aggressive fill: Long at Ask, Short at Bid + Deribit 12.5% fee cap) ---
    taker_cost_usd = None
    if l_ask is not None and s_bid is not None:
        fee_l = min(0.0003, 0.125 * l_ask)
        fee_s = min(0.0003, 0.125 * s_bid)
        spread_btc_taker = (l_ask - s_bid) + fee_l + fee_s
        # Normalized to $1.00 payout across $2,000 spread
        taker_cost_usd = (spread_btc_taker * btc) / SPREAD_WIDTH

    # --- MAKER MODE (Passive fill: Mid-market quoting + 0% Maker fee on Deribit) ---
    l_mid = (l_ask + l_bid) / 2.0 if (l_ask is not None and l_bid is not None) else (l_mark or l_ask or l_bid)
    s_mid = (s_ask + s_bid) / 2.0 if (s_ask is not None and s_bid is not None) else (s_mark or s_bid or s_ask)

    maker_cost_usd = None
    if l_mid is not None and s_mid is not None:
        spread_btc_maker = (l_mid - s_mid)
        maker_cost_usd = (spread_btc_maker * btc) / SPREAD_WIDTH

    return taker_cost_usd, maker_cost_usd

# -------------------------------------------------------------
# Compute Active Trades (Hide trades waiting for data)
# -------------------------------------------------------------
def get_computed_trades():
    btc = state["btc_usd"]
    if btc is None:
        return [], len(markets) * 2

    now = datetime.now(timezone.utc)
    active_trades = []
    hidden_count = 0

    for m in markets:
        # Time to expiration in years
        time_diff = (m["resolution_time"] - now).total_seconds()
        tte = max(time_diff / (365.0 * 24.0 * 3600.0), 0.0002)

        # Pull IV if available
        iv = 0.50
        if m["put_long"] in state["options"] and state["options"][m["put_long"]].get("mark_iv"):
            iv = state["options"][m["put_long"]]["mark_iv"] / 100.0

        # Pin probability in [K, K + $2,000]
        pin_prob = calculate_pin_risk(btc, m["strike"], m["upper_strike"], tte, iv)

        # --- 1. BEAR HEDGE (Put Spread + PolyYes) ---
        poly_yes_ask = m["poly_yes_ask"]
        poly_yes_bid = m["poly_yes_bid"]
        poly_yes_mid = (poly_yes_ask + poly_yes_bid) / 2.0 if (poly_yes_ask is not None and poly_yes_bid is not None) else poly_yes_ask

        put_taker, put_maker = calculate_deribit_spread(m["put_long"], m["put_short"], btc)

        # "If a trade is stuck at waiting for data dont show it"
        # Only show if we have either taker or maker quote ready
        if (poly_yes_ask is not None or poly_yes_mid is not None) and (put_taker is not None or put_maker is not None):
            # Taker calculations
            t_cost = (put_taker + poly_yes_ask) if (put_taker is not None and poly_yes_ask is not None) else 999.0
            t_profit = 1.00 - t_cost
            t_apy = (t_profit / t_cost) * (1.0 / tte) * 100.0 if t_cost > 0 else 0.0

            # Maker calculations
            m_cost = (put_maker + (poly_yes_mid or poly_yes_ask)) if put_maker is not None else 999.0
            m_profit = 1.00 - m_cost
            m_apy = (m_profit / m_cost) * (1.0 / tte) * 100.0 if m_cost > 0 else 0.0

            # Expected Value considering Pin Risk
            ev = (1.0 - pin_prob) * 1.00 + (pin_prob * 0.50) - (m_cost if m_cost < 999 else t_cost)

            # Categorize Status
            def classify_status(cost, prof):
                if cost >= 1.00 or prof <= 0:
                    return "NO ARB"
                if pin_prob < 0.03:
                    return "🟢 PURE ARB"
                if pin_prob <= 0.20 and ev > 0:
                    return "🟡 STAT EDGE"
                return "🔴 PIN RISK"

            t_status = classify_status(t_cost, t_profit)
            m_status = classify_status(m_cost, m_profit)

            active_trades.append({
                "id": f"{m['id']}_bear",
                "name": f"{m['date_str']} @ ${m['strike']//1000}k",
                "date_str": m["date_str"],
                "strike_str": m["strike_str"],
                "direction": "BEAR",
                "strategy": "Bear Put Spread + PolyYes",
                "poly_ask": poly_yes_ask or 0.0,
                "poly_bid": poly_yes_bid or 0.0,
                "poly_mid": poly_yes_mid or 0.0,
                "deribit_taker": put_taker or 0.0,
                "deribit_maker": put_maker or 0.0,
                "taker_cost": t_cost,
                "taker_profit": t_profit,
                "taker_apy": t_apy,
                "taker_status": t_status,
                "maker_cost": m_cost,
                "maker_profit": m_profit,
                "maker_apy": m_apy,
                "maker_status": m_status,
                "pin_prob": pin_prob,
                "ev": ev
            })
        else:
            hidden_count += 1

        # --- 2. BULL HEDGE (Call Spread + PolyNo) ---
        poly_no_ask = m["poly_no_ask"]
        poly_no_bid = m["poly_no_bid"]
        poly_no_mid = (poly_no_ask + poly_no_bid) / 2.0 if (poly_no_ask is not None and poly_no_bid is not None) else poly_no_ask

        call_taker, call_maker = calculate_deribit_spread(m["call_long"], m["call_short"], btc)

        if (poly_no_ask is not None or poly_no_mid is not None) and (call_taker is not None or call_maker is not None):
            t_cost = (call_taker + poly_no_ask) if (call_taker is not None and poly_no_ask is not None) else 999.0
            t_profit = 1.00 - t_cost
            t_apy = (t_profit / t_cost) * (1.0 / tte) * 100.0 if t_cost > 0 else 0.0

            m_cost = (call_maker + (poly_no_mid or poly_no_ask)) if call_maker is not None else 999.0
            m_profit = 1.00 - m_cost
            m_apy = (m_profit / m_cost) * (1.0 / tte) * 100.0 if m_cost > 0 else 0.0

            ev = (1.0 - pin_prob) * 1.00 + (pin_prob * 0.50) - (m_cost if m_cost < 999 else t_cost)

            def classify_status(cost, prof):
                if cost >= 1.00 or prof <= 0:
                    return "NO ARB"
                if pin_prob < 0.03:
                    return "🟢 PURE ARB"
                if pin_prob <= 0.20 and ev > 0:
                    return "🟡 STAT EDGE"
                return "🔴 PIN RISK"

            t_status = classify_status(t_cost, t_profit)
            m_status = classify_status(m_cost, m_profit)

            active_trades.append({
                "id": f"{m['id']}_bull",
                "name": f"{m['date_str']} @ ${m['strike']//1000}k",
                "date_str": m["date_str"],
                "strike_str": m["strike_str"],
                "direction": "BULL",
                "strategy": "Bull Call Spread + PolyNo",
                "poly_ask": poly_no_ask or 0.0,
                "poly_bid": poly_no_bid or 0.0,
                "poly_mid": poly_no_mid or 0.0,
                "deribit_taker": call_taker or 0.0,
                "deribit_maker": call_maker or 0.0,
                "taker_cost": t_cost,
                "taker_profit": t_profit,
                "taker_apy": t_apy,
                "taker_status": t_status,
                "maker_cost": m_cost,
                "maker_profit": m_profit,
                "maker_apy": m_apy,
                "maker_status": m_status,
                "pin_prob": pin_prob,
                "ev": ev
            })
        else:
            hidden_count += 1

    return active_trades, hidden_count

# -------------------------------------------------------------
# Polymarket CLOB WebSocket & Metadata Discovery
# -------------------------------------------------------------
def bootstrap_polymarket_metadata():
    """Fetches event metadata once to extract exact clobTokenIds and initial book quotes."""
    print(f"{CYAN}[POLYMARKET]{RESET} Discovering active CLOB token IDs for even strikes 74k-94k across {len(DATES)} expiries...")
    for dt in DATES:
        month_str = dt.strftime('%B').lower()
        event_slug = f"bitcoin-above-on-{month_str}-{dt.day}-{dt.year}"
        url = f"https://gamma-api.polymarket.com/events?slug={event_slug}"
        try:
            resp = requests.get(url, timeout=5)
            if not resp.ok:
                print(f"{YELLOW}[POLYMARKET WARN]{RESET} {event_slug} -> HTTP {resp.status_code}")
                continue
            data = resp.json()
            if not data or not isinstance(data, list):
                print(f"{YELLOW}[POLYMARKET WARN]{RESET} {event_slug} -> No event data found")
                continue
            event_markets = data[0].get("markets", [])
            matched_count = 0
            for em in event_markets:
                slug = em.get("slug", "")
                raw_tokens = em.get("clobTokenIds", [])
                if isinstance(raw_tokens, str):
                    try:
                        raw_tokens = json.loads(raw_tokens)
                    except Exception:
                        raw_tokens = []
                
                prices = em.get("outcomePrices", [])
                if isinstance(prices, str):
                    try:
                        prices = json.loads(prices)
                    except Exception:
                        prices = []

                best_bid = float(em.get("bestBid")) if em.get("bestBid") else None
                best_ask = float(em.get("bestAsk")) if em.get("bestAsk") else None

                for m in markets:
                    if m["poly_slug"] == slug and len(raw_tokens) >= 2:
                        m["token_yes"] = raw_tokens[0]
                        m["token_no"] = raw_tokens[1]

                        # Initial seed
                        if len(prices) >= 2:
                            yes_val = float(prices[0])
                            no_val = float(prices[1])
                            m["poly_yes_ask"] = best_ask or yes_val
                            m["poly_yes_bid"] = best_bid or (yes_val * 0.98)
                            m["poly_no_ask"] = (1.0 - (best_bid or yes_val))
                            m["poly_no_bid"] = (1.0 - (best_ask or yes_val))

                        token_ids_to_subscribe.append(raw_tokens[0])
                        token_ids_to_subscribe.append(raw_tokens[1])
                        state["token_to_market"][raw_tokens[0]] = (m, "YES")
                        state["token_to_market"][raw_tokens[1]] = (m, "NO")
                        matched_count += 1
            print(f"{GREEN}[POLYMARKET]{RESET} {dt.strftime('%b %d')} ({event_slug}): Seeded {matched_count} strike markets")
        except Exception as e:
            print(f"{RED}[POLYMARKET ERROR]{RESET} Error loading {event_slug}: {e}")

    print(f"{GREEN}[POLYMARKET]{RESET} Successfully seeded {len(token_ids_to_subscribe)} CLOB token subscriptions.")

    # Bootstrap BTC Spot price immediately
    try:
        btc_r = requests.get("https://www.deribit.com/api/v2/public/ticker?instrument_name=BTC-PERPETUAL", timeout=5)
        if btc_r.ok:
            state["btc_usd"] = float(btc_r.json()["result"]["last_price"])
            print(f"{GREEN}[DERIBIT]{RESET} Bootstrapped initial BTC Spot: ${state['btc_usd']:,.2f}")
    except Exception as e:
        print(f"{YELLOW}[DERIBIT WARN]{RESET} Could not bootstrap BTC spot: {e}")

async def stream_polymarket_clob():
    """Connects to Polymarket CLOB WebSocket and processes real-time L2 order books."""
    clob_ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    
    while True:
        try:
            tokens = list(set(token_ids_to_subscribe))
            if not tokens:
                await asyncio.sleep(2)
                continue

            async with websockets.connect(clob_ws_url, ping_interval=20, ping_timeout=20) as ws:
                # Subscribe in batches
                for i in range(0, len(tokens), 30):
                    batch = tokens[i:i+30]
                    sub_msg = {"assets_ids": batch, "type": "market"}
                    await ws.send(json.dumps(sub_msg))

                print(f"{GREEN}[POLYMARKET WS]{RESET} Subscribed to {len(tokens)} token books.")

                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)

                    items = data if isinstance(data, list) else [data]
                    for item in items:
                        asset_id = item.get("asset_id")
                        if not asset_id or asset_id not in state["token_to_market"]:
                            continue

                        m, side = state["token_to_market"][asset_id]
                        bids = item.get("bids", [])
                        asks = item.get("asks", [])

                        best_bid = float(bids[0]["price"]) if bids else None
                        best_ask = float(asks[0]["price"]) if asks else None

                        if side == "YES":
                            if best_ask is not None: m["poly_yes_ask"] = best_ask
                            if best_bid is not None: m["poly_yes_bid"] = best_bid
                        else:
                            if best_ask is not None: m["poly_no_ask"] = best_ask
                            if best_bid is not None: m["poly_no_bid"] = best_bid
        except Exception as e:
            await asyncio.sleep(2)

# -------------------------------------------------------------
# Deribit WebSocket
# -------------------------------------------------------------
async def subscribe_deribit():
    channels = ["ticker.BTC-PERPETUAL.100ms"]
    for m in markets:
        channels.append(f"ticker.{m['put_long']}.100ms")
        channels.append(f"ticker.{m['put_short']}.100ms")
        channels.append(f"ticker.{m['call_long']}.100ms")
        channels.append(f"ticker.{m['call_short']}.100ms")

    channels = list(set(channels))

    while True:
        try:
            async with websockets.connect("wss://www.deribit.com/ws/api/v2") as ws:
                for i in range(0, len(channels), 25):
                    batch = channels[i:i+25]
                    msg = {
                        "jsonrpc": "2.0",
                        "id": i,
                        "method": "public/subscribe",
                        "params": {"channels": batch}
                    }
                    await ws.send(json.dumps(msg))

                print(f"{GREEN}[DERIBIT WS]{RESET} Subscribed to {len(channels)} instruments.")

                while True:
                    response = await ws.recv()
                    data = json.loads(response)

                    if "params" in data and "data" in data["params"]:
                        tick = data["params"]["data"]
                        inst = tick.get("instrument_name")

                        if inst == "BTC-PERPETUAL":
                            state["btc_usd"] = tick.get("last_price")
                        else:
                            if inst not in state["options"]:
                                state["options"][inst] = {}
                            if tick.get("best_ask_price") is not None:
                                state["options"][inst]["ask"] = tick.get("best_ask_price")
                            if tick.get("best_bid_price") is not None:
                                state["options"][inst]["bid"] = tick.get("best_bid_price")
                            if tick.get("mark_price") is not None:
                                state["options"][inst]["mark"] = tick.get("mark_price")
                            if tick.get("mark_iv") is not None:
                                state["options"][inst]["mark_iv"] = tick.get("mark_iv")
        except Exception as e:
            await asyncio.sleep(1)

# -------------------------------------------------------------
# FastAPI Bloomberg Terminal Web Application
# -------------------------------------------------------------
app = FastAPI(title="Poly-Deribit Arbitrage Terminal")
active_ws_connections: List[WebSocket] = []

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/state")
async def get_api_state():
    """Fallback REST endpoint for real-time arbitrage data."""
    active_trades, hidden_count = get_computed_trades()
    pure_arbs = [t for t in active_trades if "PURE" in t["taker_status"]]
    best_pure = max(pure_arbs, key=lambda x: x["taker_apy"]) if pure_arbs else None
    makers = [t for t in active_trades if t["maker_profit"] > 0]
    best_maker = max(makers, key=lambda x: x["maker_apy"]) if makers else None
    return {
        "btc_usd": state["btc_usd"],
        "active_trades": active_trades,
        "hidden_count": hidden_count,
        "best_pure_taker": {
            "name": best_pure["name"],
            "strike": best_pure["strike_str"],
            "apy": best_pure["taker_apy"]
        } if best_pure else None,
        "best_maker": {
            "name": best_maker["name"],
            "strike": best_maker["strike_str"],
            "apy": best_maker["maker_apy"]
        } if best_maker else None
    }

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    active_ws_connections.append(ws)
    try:
        while True:
            # Keep socket alive without blocking read
            await asyncio.sleep(60)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        if ws in active_ws_connections:
            active_ws_connections.remove(ws)

async def broadcast_dashboard_state():
    """Pushes computed arbitrage state to all connected Bloomberg terminal dashboards."""
    while True:
        await asyncio.sleep(0.3)
        if not active_ws_connections:
            continue

        active_trades, hidden_count = get_computed_trades()

        # Find best pure arb and best maker
        pure_arbs = [t for t in active_trades if "PURE" in t["taker_status"]]
        best_pure = max(pure_arbs, key=lambda x: x["taker_apy"]) if pure_arbs else None

        makers = [t for t in active_trades if t["maker_profit"] > 0]
        best_maker = max(makers, key=lambda x: x["maker_apy"]) if makers else None

        payload = {
            "btc_usd": state["btc_usd"],
            "active_trades": active_trades,
            "hidden_count": hidden_count,
            "best_pure_taker": {
                "name": best_pure["name"],
                "strike": best_pure["strike_str"],
                "apy": best_pure["taker_apy"]
            } if best_pure else None,
            "best_maker": {
                "name": best_maker["name"],
                "strike": best_maker["strike_str"],
                "apy": best_maker["maker_apy"]
            } if best_maker else None
        }

        raw = json.dumps(payload)
        dead = []
        for ws in active_ws_connections:
            try:
                await ws.send_text(raw)
            except Exception:
                dead.append(ws)
        for d in dead:
            if d in active_ws_connections:
                active_ws_connections.remove(d)

async def run_server():
    # ws_ping_interval=None prevents Python 3.14 asyncio assertion error in websockets
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning", ws_ping_interval=None, ws_ping_timeout=None)
    server = uvicorn.Server(config)
    await server.serve()

# -------------------------------------------------------------
# Main Orchestrator
# -------------------------------------------------------------
async def main():
    print("=" * 80)
    print(f" {YELLOW}BLOOMBERG TERMINAL // POLYMARKET-DERIBIT ARB MATRIX v2.0{RESET}")
    print("=" * 80)
    print(f" • Dynamic Spread Width : {GREEN}$2,000 WIDE (50% fee reduction){RESET}")
    print(f" • Target Strikes Grid  : {CYAN}74k to 94k (Even numbers only){RESET}")
    print(f" • Real-time Feeds      : {GREEN}Polymarket CLOB WS + Deribit WS (No REST polling){RESET}")
    print(f" • Risk Engine          : {YELLOW}Black-Scholes P(Pin) Multi-Tier Calculus{RESET}")
    print(f" • Dual Mode Pricing    : {CYAN}Taker Instant Fill + Maker Mid-Market Quoting{RESET}")
    print("=" * 80)
    print(f"\n >>> Launching Institutional Dashboard at: {GREEN}http://127.0.0.1:8000{RESET}\n")

    # Bootstrap metadata
    bootstrap_polymarket_metadata()

    # Open browser automatically after 1 second
    async def open_browser():
        await asyncio.sleep(1.2)
        webbrowser.open("http://127.0.0.1:8000")

    asyncio.create_task(open_browser())

    await asyncio.gather(
        subscribe_deribit(),
        stream_polymarket_clob(),
        broadcast_dashboard_state(),
        run_server()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[TERMINAL] Shutting down cleanly.")
