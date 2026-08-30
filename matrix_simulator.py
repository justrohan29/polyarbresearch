import asyncio
import websockets
import requests
import json
import time
import os
import math
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------
# PATH B: WALL STREET MATRIX SIMULATOR
# ---------------------------------------------------------

STRIKES = [68, 70, 72, 74, 76, 78, 80, 82, 84, 86]

# Dynamically generate the next 6 days
today = datetime.now(timezone.utc)
DATES = [(today + timedelta(days=i)) for i in range(6)]

IV = 0.50 # 50% Annualized Volatility
R = 0.0 # Risk-free interest rate
POSITION_SIZE_USD = 1000 # We simulate buying $1,000 worth of PolyNo for EVERY market

markets = []
for dt in DATES:
    month = dt.month
    d = dt.day
    for s in STRIKES:
        month_str = dt.strftime('%B').lower()
        slug = f"bitcoin-above-{s}k-on-{month_str}-{d}-{dt.year}"
        expiration = datetime(dt.year, month, d, 8, 0, 0, tzinfo=timezone.utc)
        markets.append({
            "slug": slug,
            "strike": s * 1000,
            "expiration": expiration,
            "poly_no": None,
            "poly_yes": None,
            "delta": 0.0
        })

state = {
    "btc_usd": None,
    "last_print": 0,
    "futures_position_btc": 0.0,
    "total_fees_paid_usd": 0.0
}

# The Standard Normal Probability Density Function
def norm_pdf(x):
    return math.exp(-x**2 / 2.0) / math.sqrt(2 * math.pi)

# Black-Scholes Binary Delta Derivative
def calculate_binary_delta(S, K, T, IV, r=0.0):
    if T <= 0: return 0.0
    
    # Calculate d2
    d2 = (math.log(S/K) + (r - (IV**2)/2) * T) / (IV * math.sqrt(T))
    
    # Delta of a Binary Option
    delta = math.exp(-r*T) * norm_pdf(d2) / (S * IV * math.sqrt(T))
    return delta

def update_dashboard():
    if state["btc_usd"] is None: return
    current_time = time.time()
    if current_time - state["last_print"] < 1.0: return
    state["last_print"] = current_time
    
    btc = state["btc_usd"]
    now = datetime.now(timezone.utc)
    
    total_hedge_required_btc = 0.0
    
    output = []
    output.append("="*90)
    output.append(f" WALL STREET MATRIX SIMULATOR (PATH B) | LIVE BTC SPOT: ${btc:,.2f}")
    output.append("="*90)
    
    header = f"{'MARKET':<30} | {'POLY NO':<10} | {'TTE (Days)':<10} | {'DELTA HEDGE (BTC)':<15}"
    output.append(header)
    output.append("-" * 90)
    
    for m in markets:
        T = (m["expiration"] - now).total_seconds() / (365 * 24 * 3600)
        
        hedge_btc = 0.0
        if T > 0:
            # We hold a $1,000 PolyNo (Short) position. 
            # We calculate the Delta, and multiply by payout size to get the exact fraction of a Bitcoin needed to hedge.
            delta_val = calculate_binary_delta(btc, m["strike"], T, IV, R)
            hedge_btc = delta_val * POSITION_SIZE_USD
            total_hedge_required_btc += hedge_btc
        
        poly_no_str = f"${m['poly_no']:.4f}" if m['poly_no'] is not None else "WAITING"
        tte_str = f"{max(T*365, 0):.2f}"
        
        # Only display markets that haven't expired
        if T > 0:
            month_name = "June" if m["expiration"].month == 6 else "July"
            name = f"{month_name} {m['expiration'].day} @ {m['strike']//1000}k Target"
            output.append(f"{name:<30} | {poly_no_str:<10} | {tte_str:<10} | +{hedge_btc:.4f} BTC")
        
    output.append("-" * 90)
    output.append(f" TOTAL PORTFOLIO HEDGE REQUIRED : {total_hedge_required_btc:.4f} BTC")
    output.append(f" CURRENT FUTURES POSITION       : {state['futures_position_btc']:.4f} BTC")
    
    # Simulate dynamic execution (Rebalancing the Delta)
    diff = total_hedge_required_btc - state['futures_position_btc']
    if abs(diff) > 0.0001:  # Minimum trade size
        trade_usd = abs(diff) * btc
        fee = trade_usd * 0.0005  # 0.05% taker fee simulation
        state['total_fees_paid_usd'] += fee
        state['futures_position_btc'] = total_hedge_required_btc
        output.append(f" >>> [ACTION] EXECUTED REBALANCE TRADE: {diff:+.4f} BTC (Fee: ${fee:.2f})")
    else:
        output.append(" >>> [ACTION] PORTFOLIO BALANCED. NO TRADE REQUIRED.")
        
    output.append(f" TOTAL ACCUMULATED GAMMA BLEED  : ${state['total_fees_paid_usd']:.2f}")
    output.append("="*90)
    
    os.system('cls' if os.name == 'nt' else 'clear')
    print("\n".join(output))

async def poll_polymarket():
    while True:
        try:
            # We ping Polymarket to get prices, though our Black-Scholes engine doesn't strictly need them to hedge!
            # It just needs spot price and time to expiration. But we show it for the dashboard.
            slugs = ",".join([m["slug"] for m in markets])
            url = f"https://gamma-api.polymarket.com/markets?slugs={slugs}"
            
            resp = requests.get(url)
            data = resp.json()
            
            for m in markets:
                for d in data:
                    if d.get("slug") == m["slug"]:
                        prices = d.get("outcomePrices", [])
                        if isinstance(prices, str):
                            prices = json.loads(prices)
                        if len(prices) >= 2:
                            m["poly_yes"] = float(prices[0])
                            m["poly_no"] = float(prices[1])
                            
            update_dashboard()
        except Exception as e:
            pass
        await asyncio.sleep(2)

async def subscribe_deribit():
    channels = ["ticker.BTC-PERPETUAL.100ms"]
    
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "public/subscribe",
        "params": {
            "channels": channels
        }
    }
    
    async with websockets.connect("wss://www.deribit.com/ws/api/v2") as ws:
        await ws.send(json.dumps(msg))
        
        while True:
            try:
                response = await ws.recv()
                data = json.loads(response)
                
                if "params" in data and "data" in data["params"]:
                    tick = data["params"]["data"]
                    if tick.get("instrument_name") == "BTC-PERPETUAL":
                        state["btc_usd"] = tick.get("last_price")
                        update_dashboard()
            except Exception as e:
                await asyncio.sleep(1)

async def main():
    print("Initializing Wall Street Matrix Simulator...")
    print("Loading Black-Scholes Engine for 16 Concurrent Markets...")
    
    await asyncio.gather(
        poll_polymarket(),
        subscribe_deribit()
    )

if __name__ == "__main__":
    asyncio.run(main())
