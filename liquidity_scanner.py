import asyncio
import websockets
import requests
import json
import time
import os
from datetime import datetime, timedelta, timezone
import math

# Enable VT100 Escape Codes for Windows Terminal Colors
if os.name == 'nt':
    os.system('color')

RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
RESET = '\033[0m'

STRIKES = [68, 70, 72, 74, 76, 78, 80, 82, 84, 86]

# Dynamically generate the next 6 days
today = datetime.now(timezone.utc)
DATES = [(today + timedelta(days=i)) for i in range(6)]

TARGET_PAYOUT_USD = 1000.0 
# For a $1,000 wide spread, a payout of $1000 requires exactly 1.0 BTC of contract size.
REQUIRED_CONTRACT_SIZE = 1.0 

def get_engulfing_date_str(dt):
    next_day = dt + timedelta(days=1)
    return f"{next_day.day}{next_day.strftime('%b%y').upper()}"

markets = []
for dt in DATES:
    month = dt.month
    d = dt.day
    for s in STRIKES:
        engulfing_date = get_engulfing_date_str(dt)
        month_str = dt.strftime('%B').lower()
        markets.append({
            "poly_slug": f"bitcoin-above-{s}k-on-{month_str}-{d}-{dt.year}",
            "date": d,
            "month": month,
            "month_name": dt.strftime('%B').capitalize(),
            "expiration_date": datetime(dt.year, month, d, 8, 0, 0, tzinfo=timezone.utc),
            "strike": s * 1000,
            "poly_yes": None,
            "poly_no": None,
            "put_long": f"BTC-{engulfing_date}-{s+1}000-P",
            "put_short": f"BTC-{engulfing_date}-{s}000-P",
            "call_long": f"BTC-{engulfing_date}-{s}000-C",
            "call_short": f"BTC-{engulfing_date}-{s+1}000-C",
        })

state = {
    "btc_usd": None,
    "last_print": 0,
    "options": {}
}

def get_spread_cost(long_ticker, short_ticker, btc):
    opt_l = state["options"].get(long_ticker, {})
    opt_s = state["options"].get(short_ticker, {})
    
    l_ask = opt_l.get("ask")
    l_ask_size = opt_l.get("ask_size", 0.0)
    
    s_bid = opt_s.get("bid")
    s_bid_size = opt_s.get("bid_size", 0.0)
    
    if l_ask is None or s_bid is None:
        return None, "WAITING"
        
    if l_ask_size < REQUIRED_CONTRACT_SIZE or s_bid_size < REQUIRED_CONTRACT_SIZE:
        return None, "ILLIQUID"
        
    fee_l = min(0.0003, 0.125 * l_ask)
    fee_s = min(0.0003, 0.125 * s_bid)
        
    spread_btc = (l_ask - s_bid) + fee_l + fee_s
    cost = (spread_btc * btc) / 1000.0  # Normalized to $1.00 payout
    return cost, "OK"

def update_dashboard():
    if state["btc_usd"] is None: return
    current_time = time.time()
    if current_time - state["last_print"] < 1.0: return
    state["last_print"] = current_time
    
    btc = state["btc_usd"]
    
    output = []
    output.append("="*115)
    output.append(f" LIQUIDITY-FILTERED OPTIONS SCANNER | LIVE BTC: ${btc:,.2f} | TARGET SIZE: {REQUIRED_CONTRACT_SIZE} BTC ($1k Payout)")
    output.append("="*115)
    
    header = f"{'MARKET':<18} | {'BEAR HEDGE (Put Spread + PolyYes)':<45} | {'BULL HEDGE (Call Spread + PolyNo)':<45}"
    output.append(header)
    output.append("-" * 115)
    
    for m in markets:
        target = m["strike"]
        
        # Dynamic Danger Zone Check (IV Cone)
        # Fallback to 50% IV if not available in state
        iv = 50.0
        # Try to pull mark_iv from the options if available
        if m["put_long"] in state["options"] and "mark_iv" in state["options"][m["put_long"]]:
            iv = state["options"][m["put_long"]]["mark_iv"]
        
        tte = max((m["expiration_date"] - datetime.now(timezone.utc)).total_seconds() / (365*24*3600), 0.0001)
        sd_move = btc * (iv / 100.0) * math.sqrt(tte)
        
        danger = abs(btc - target) < sd_move
        row_color = RED if danger else ""
        end_color = RESET if danger else ""
        
        # 1. Bear Hedge
        bear_str = "WAITING FOR DATA..."
        if m["poly_yes"] is not None:
            put_cost, status = get_spread_cost(m["put_long"], m["put_short"], btc)
            
            if status == "ILLIQUID":
                bear_str = f"{YELLOW}[LIQUIDITY MIRAGE] < {REQUIRED_CONTRACT_SIZE} BTC{RESET}"
            elif put_cost is not None:
                total_bear = put_cost + m["poly_yes"]
                prof_bear = 1.00 - total_bear
                if danger:
                    bear_str = f"Cost: ${total_bear:.4f} [DANGER ZONE]"
                elif prof_bear > 0:
                    apy = (prof_bear / total_bear) * (1 / tte) * 100
                    bear_str = f"{GREEN}Cost: ${total_bear:.4f} | PROF: ${prof_bear:.4f} ({apy:.0f}% APY){RESET}"
                else:
                    bear_str = f"Cost: ${total_bear:.4f} | No Arb"
                    
        # 2. Bull Hedge
        bull_str = "WAITING FOR DATA..."
        if m["poly_no"] is not None:
            call_cost, status = get_spread_cost(m["call_long"], m["call_short"], btc)
            
            if status == "ILLIQUID":
                bull_str = f"{YELLOW}[LIQUIDITY MIRAGE] < {REQUIRED_CONTRACT_SIZE} BTC{RESET}"
            elif call_cost is not None:
                total_bull = call_cost + m["poly_no"]
                prof_bull = 1.00 - total_bull
                if danger:
                    bull_str = f"Cost: ${total_bull:.4f} [DANGER ZONE]"
                elif prof_bull > 0:
                    apy = (prof_bull / total_bull) * (1 / tte) * 100
                    bull_str = f"{GREEN}Cost: ${total_bull:.4f} | PROF: ${prof_bull:.4f} ({apy:.0f}% APY){RESET}"
                else:
                    bull_str = f"Cost: ${total_bull:.4f} | No Arb"
        
        month_name = m.get("month_name", "Unknown")
        name = f"{month_name} {m['date']} @ {m['strike']//1000}k"
        output.append(f"{row_color}{name:<18}{end_color} | {bear_str:<45} | {bull_str:<45}")
        
    output.append("-" * 115)
    output.append(f"{RED}RED = DANGER ZONE{RESET} (Pin Risk). Math is unreliable.")
    output.append(f"{YELLOW}YELLOW = LIQUIDITY MIRAGE{RESET}. Not enough order book depth to fill a {REQUIRED_CONTRACT_SIZE} BTC trade.")
    output.append(f"{GREEN}GREEN = ARBITRAGE{RESET}. Safe, Liquid, Out-Of-The-Money arbitrage detected.")
    output.append("="*115)
    
    os.system('cls' if os.name == 'nt' else 'clear')
    print("\n".join(output))

async def poll_polymarket():
    query_params = "&".join([f"slug={m['poly_slug']}" for m in markets])
    url = f"https://gamma-api.polymarket.com/markets?{query_params}"
    
    while True:
        try:
            resp = requests.get(url)
            data = resp.json()
            
            for m in markets:
                for d in data:
                    if d.get("slug") == m["poly_slug"]:
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
    for m in markets:
        channels.append(f"ticker.{m['put_long']}.100ms")
        channels.append(f"ticker.{m['put_short']}.100ms")
        channels.append(f"ticker.{m['call_long']}.100ms")
        channels.append(f"ticker.{m['call_short']}.100ms")
        
    channels = list(set(channels))
    
    async with websockets.connect("wss://www.deribit.com/ws/api/v2") as ws:
        for i in range(0, len(channels), 20):
            batch = channels[i:i+20]
            msg = {
                "jsonrpc": "2.0",
                "id": i,
                "method": "public/subscribe",
                "params": {"channels": batch}
            }
            await ws.send(json.dumps(msg))
            
        while True:
            try:
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
                            
                        # Store both Price and Size (Amount)
                        if tick.get("best_ask_price"):
                            state["options"][inst]["ask"] = tick.get("best_ask_price")
                            state["options"][inst]["ask_size"] = tick.get("best_ask_amount")
                        
                        if tick.get("best_bid_price"):
                            state["options"][inst]["bid"] = tick.get("best_bid_price")
                            state["options"][inst]["bid_size"] = tick.get("best_bid_amount")
                        if tick.get("mark_iv"):
                            state["options"][inst]["mark_iv"] = tick.get("mark_iv")
                            
                    update_dashboard()
            except Exception as e:
                await asyncio.sleep(1)

async def main():
    print("Initializing Liquidity-Filtered Options Scanner...")
    print("Subscribing to 64 Deribit Options and parsing Order Book Depth...")
    
    await asyncio.gather(
        poll_polymarket(),
        subscribe_deribit()
    )

if __name__ == "__main__":
    asyncio.run(main())
