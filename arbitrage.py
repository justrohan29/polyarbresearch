"""
arbitrage.py – Cross-Market Arbitrage Backtester (Phase 1)
===========================================================
Single-file implementation of the full pipeline described in PRD.md.
Covers issues 001 → 004:
  001  Pipeline skeleton  (Config, mock data, visualize skeleton)
  002  Polymarket ingestion (REST API fetch + CSV standardisation)
  003  Polygon ingestion   (options chain fetch + Bear Put Spread)
  004  Arbitrage detection (combined cost flag + highlighted graph)

Run modes
---------
  python arbitrage.py                 # full pipeline (real APIs)
  python arbitrage.py --mock          # mock data only (no API keys needed)
  python arbitrage.py --test          # run built-in unit tests and exit

Dependencies
------------
  pip install requests pandas matplotlib
"""

from __future__ import annotations

import argparse
import io
import sys
import textwrap
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import requests

# ---------------------------------------------------------------------------
# ── SECTION 1: CONFIG (Issue 001) ──────────────────────────────────────────
# ---------------------------------------------------------------------------

@dataclass
class Config:
    """Centralised configuration.  Edit values here or override at runtime."""

    # ── Target event ──────────────────────────────────────────────────────
    ticker: str = "NVDA"

    # Date range for the backtest (inclusive, UTC)
    start_date: str = "2024-11-20"   # NVDA earnings window
    end_date:   str = "2024-11-22"

    # ── Bear Put Spread strikes (K1 > K2, width = $1 for binary replication)
    strike_k1: float = 136.0   # buy put at this strike  (upper boundary)
    strike_k2: float = 135.0   # sell put at this strike (lower boundary)

    # Polymarket market slug / condition ID for the event
    # (NVDA closing below K2 on expiry day)
    polymarket_market_slug: str = "nvda-below-135-nov-22-2024"

    # ── Polygon.io ────────────────────────────────────────────────────────
    polygon_api_key: str = "YOUR_POLYGON_API_KEY"

    # Option contract details expected on Polygon
    # Pattern: O:{ticker}{YYMMDD}{C/P}{strike*1000 padded to 8 digits}
    option_k1: str = "O:NVDA241122P00136000"
    option_k2: str = "O:NVDA241122P00135000"

    # ── Analysis ──────────────────────────────────────────────────────────
    arbitrage_threshold: float = 1.00   # combined cost must be < this

    # ── Output ────────────────────────────────────────────────────────────
    output_csv:  str = "combined_data.csv"
    output_plot: str = "arbitrage_windows.png"

    # ── Proxy ─────────────────────────────────────────────────────────────
    proxy_url: Optional[str] = None


# Module-level singleton – imported by all sections below.
CONFIG = Config()


# ---------------------------------------------------------------------------
# ── SECTION 2: POLYMARKET FETCHER (Issue 002) ──────────────────────────────
# ---------------------------------------------------------------------------
# Polymarket exposes two REST endpoints we care about:
#   Gamma API  – market metadata (slug → condition_id / market_id)
#   CLOB API   – time-series price history
# ---------------------------------------------------------------------------

POLYMARKET_GAMMA_BASE = "https://gamma-api.polymarket.com"
POLYMARKET_CLOB_BASE  = "https://clob.polymarket.com"


def _polymarket_resolve_market_id(slug: str, proxy: Optional[str] = None) -> tuple[str, str]:
    """Return (condition_id, token_id_yes) for the given market slug.

    Raises ValueError if the market cannot be found.
    """
    url = f"{POLYMARKET_GAMMA_BASE}/markets"
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(url, params={"slug": slug}, timeout=30, proxies=proxies)
        resp.raise_for_status()
    except requests.exceptions.ProxyError as e:
        raise RuntimeError(f"Proxy dead or unreachable. Check proxy config.\nUnderlying error: {e}") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(
            "Failed to connect to Polymarket Gamma API. "
            "Polymarket actively geo-blocks US IPs, and local ISPs in countries like India also block the site. "
            "Please set your VPN/proxy to a supported region (e.g., Europe or Japan), or run with '--mock' for local testing.\n"
            f"Underlying error: {e}"
        ) from e
    data = resp.json()

    if not data:
        raise ValueError(f"No Polymarket market found for slug: {slug!r}")

    market = data[0]
    condition_id = market.get("conditionId") or market.get("condition_id", "")

    # The CLOB works on token IDs.  The "Yes" outcome is tokens[0].
    tokens = market.get("tokens") or market.get("clobTokenIds") or []
    if isinstance(tokens, list) and tokens:
        yes_token = tokens[0] if isinstance(tokens[0], str) else tokens[0].get("token_id", "")
    else:
        yes_token = ""

    return condition_id, yes_token


def _polymarket_fetch_timeseries(token_id: str, start_date: str, end_date: str, proxy: Optional[str] = None) -> pd.DataFrame:
    """Pull minute-level price history from the CLOB API for a Yes token.

    Returns a DataFrame with columns: [timestamp (UTC, minute), price_yes]
    """
    url = f"{POLYMARKET_CLOB_BASE}/prices-history"
    params = {
        "market":      token_id,
        "startTs":     int(datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()),
        "endTs":       int(datetime.strptime(end_date,   "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 86_399,
        "interval":    "1m",
        "fidelity":    60,
    }
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        resp = requests.get(url, params=params, timeout=60, proxies=proxies)
        resp.raise_for_status()
    except requests.exceptions.ProxyError as e:
        raise RuntimeError(f"Proxy dead or unreachable. Check proxy config.\nUnderlying error: {e}") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(
            "Failed to connect to Polymarket CLOB API. "
            "Polymarket actively geo-blocks US IPs, and local ISPs in countries like India also block the site. "
            "Please set your VPN/proxy to a supported region (e.g., Europe or Japan), or run with '--mock' for local testing.\n"
            f"Underlying error: {e}"
        ) from e
    history = resp.json().get("history", [])

    if not history:
        raise ValueError(f"Polymarket returned empty history for token {token_id!r}")

    df = pd.DataFrame(history)
    # Normalise column names across API versions
    df = df.rename(columns={"t": "timestamp", "p": "price_yes"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.floor("min")
    df = df[["timestamp", "price_yes"]].dropna()
    df["price_yes"] = df["price_yes"].astype(float)
    return df.sort_values("timestamp").reset_index(drop=True)


def fetch_polymarket(cfg: Config) -> pd.DataFrame:
    """Public entry point – fetch + clean Polymarket data.

    Returns DataFrame with columns: [timestamp, price_yes]
    Saves to CSV as a side-effect for inspection / caching.
    """
    print(f"[Polymarket] Resolving market: {cfg.polymarket_market_slug!r}")
    _cid, token_id = _polymarket_resolve_market_id(cfg.polymarket_market_slug, proxy=cfg.proxy_url)
    print(f"[Polymarket] Yes token: {token_id!r}")

    print(f"[Polymarket] Fetching price history {cfg.start_date} -> {cfg.end_date}")
    df = _polymarket_fetch_timeseries(token_id, cfg.start_date, cfg.end_date, proxy=cfg.proxy_url)
    print(f"[Polymarket] Rows fetched: {len(df)}")

    df.to_csv("polymarket_raw.csv", index=False)
    print("[Polymarket] Saved -> polymarket_raw.csv")
    return df


# ---------------------------------------------------------------------------
# ── SECTION 3: POLYGON FETCHER (Issue 003) ─────────────────────────────────
# ---------------------------------------------------------------------------
# We use the Polygon.io v2 aggregates endpoint:
#   GET /v2/aggs/ticker/{optionsTicker}/range/1/minute/{from}/{to}
# ---------------------------------------------------------------------------

POLYGON_BASE = "https://api.polygon.io"


def _polygon_fetch_minute_aggs(ticker: str, start: str, end: str, api_key: str, proxy: Optional[str] = None) -> pd.DataFrame:
    """Return minute OHLCV bars for an options contract from Polygon.io.

    Columns: [timestamp (UTC, minute), open, high, low, close, volume]
    """
    url = f"{POLYGON_BASE}/v2/aggs/ticker/{ticker}/range/1/minute/{start}/{end}"
    params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}
    proxies = {"http": proxy, "https": proxy} if proxy else None
    resp = requests.get(url, params=params, timeout=60, proxies=proxies)
    resp.raise_for_status()
    body = resp.json()

    if body.get("resultsCount", 0) == 0:
        raise ValueError(f"Polygon returned no data for {ticker!r} ({start} – {end})")

    df = pd.DataFrame(body["results"])
    df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True).dt.floor("min")
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].dropna()
    return df.sort_values("timestamp").reset_index(drop=True)


def _calculate_bear_put_spread(df_k1: pd.DataFrame, df_k2: pd.DataFrame) -> pd.DataFrame:
    """Merge K1/K2 bars and compute net premium of the Bear Put Spread.

    Net premium = mid_price(K1) - mid_price(K2)
    We use mid = (bid + ask) / 2; since Polygon aggs give OHLC (not bid/ask),
    we approximate mid ≈ close for historical minute bars.

    Returns DataFrame: [timestamp, spread_cost]
    """
    df_k1 = df_k1[["timestamp", "close"]].rename(columns={"close": "price_k1"})
    df_k2 = df_k2[["timestamp", "close"]].rename(columns={"close": "price_k2"})

    merged = pd.merge(df_k1, df_k2, on="timestamp", how="inner")
    merged["spread_cost"] = (merged["price_k1"] - merged["price_k2"]).clip(lower=0)
    return merged[["timestamp", "spread_cost"]]


def fetch_polygon(cfg: Config) -> pd.DataFrame:
    """Public entry point – fetch both legs and compute spread.

    Returns DataFrame with columns: [timestamp, spread_cost]
    """
    print(f"[Polygon] Fetching K1 leg: {cfg.option_k1}")
    df_k1 = _polygon_fetch_minute_aggs(cfg.option_k1, cfg.start_date, cfg.end_date, cfg.polygon_api_key, proxy=cfg.proxy_url)
    print(f"[Polygon] K1 rows: {len(df_k1)}")

    print(f"[Polygon] Fetching K2 leg: {cfg.option_k2}")
    df_k2 = _polygon_fetch_minute_aggs(cfg.option_k2, cfg.start_date, cfg.end_date, cfg.polygon_api_key, proxy=cfg.proxy_url)
    print(f"[Polygon] K2 rows: {len(df_k2)}")

    df_spread = _calculate_bear_put_spread(df_k1, df_k2)
    print(f"[Polygon] Spread rows after merge: {len(df_spread)}")

    df_spread.to_csv("polygon_raw.csv", index=False)
    print("[Polygon] Saved -> polygon_raw.csv")
    return df_spread


# ---------------------------------------------------------------------------
# ── SECTION 4: ANALYZER (Issues 003 + 004) ─────────────────────────────────
# ---------------------------------------------------------------------------

def align_timestamps(df_poly: pd.DataFrame, df_polygon: pd.DataFrame) -> pd.DataFrame:
    """Inner-join on UTC minute timestamp.

    Handles missing minutes (market closures, API gaps) by keeping only rows
    present in BOTH datasets (inner join).  The caller decides whether to
    forward-fill before passing in if gaps are acceptable.

    Returns DataFrame: [timestamp, price_yes, spread_cost, combined_cost]
    """
    merged = pd.merge(df_poly, df_polygon, on="timestamp", how="inner")
    merged = merged.sort_values("timestamp").reset_index(drop=True)
    merged["combined_cost"] = merged["price_yes"] + merged["spread_cost"]
    return merged


def flag_arbitrage_windows(df: pd.DataFrame, threshold: float = 1.00) -> pd.DataFrame:
    """Add a boolean column 'is_arbitrage' where combined_cost < threshold.

    Also adds 'window_id' to number contiguous arbitrage runs (useful for
    calculating duration of each window).
    """
    df = df.copy()
    df["is_arbitrage"] = df["combined_cost"] < threshold

    # Label each contiguous block of True with a unique integer, False = -1
    mask = df["is_arbitrage"]
    group_ids = (mask != mask.shift()).cumsum()
    df["window_id"] = group_ids.where(mask, other=-1)
    return df


def summarise_windows(df: pd.DataFrame) -> pd.DataFrame:
    """Return per-window statistics for every arbitrage run.

    Columns: [window_id, start, end, duration_min, min_cost, avg_cost]
    """
    arb = df[df["is_arbitrage"]].copy()
    if arb.empty:
        return pd.DataFrame(columns=["window_id", "start", "end", "duration_min", "min_cost", "avg_cost"])

    summary = (
        arb.groupby("window_id")
        .agg(
            start=("timestamp", "min"),
            end=("timestamp", "max"),
            duration_min=("timestamp", "count"),
            min_cost=("combined_cost", "min"),
            avg_cost=("combined_cost", "mean"),
        )
        .reset_index()
    )
    return summary


def run_analysis(df_poly: pd.DataFrame, df_polygon: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Full analysis pipeline: align → compute combined cost → flag windows.

    Returns the enriched DataFrame ready for visualisation.
    """
    print("[Analyzer] Aligning timestamps...")
    df = align_timestamps(df_poly, df_polygon)
    print(f"[Analyzer] Aligned rows: {len(df)}")

    df = flag_arbitrage_windows(df, cfg.arbitrage_threshold)

    n_arb = df["is_arbitrage"].sum()
    pct   = 100 * n_arb / len(df) if len(df) else 0
    print(f"[Analyzer] Arbitrage minutes: {n_arb} / {len(df)} ({pct:.1f}%)")

    summary = summarise_windows(df)
    if not summary.empty:
        print(f"[Analyzer] Distinct arbitrage windows: {len(summary)}")
        print(summary.to_string(index=False))
    else:
        print("[Analyzer] No arbitrage windows detected in this dataset.")

    df.to_csv(cfg.output_csv, index=False)
    print(f"[Analyzer] Combined data saved -> {cfg.output_csv}")
    return df


# ---------------------------------------------------------------------------
# ── SECTION 5: VISUALISER (Issues 001 + 004) ───────────────────────────────
# ---------------------------------------------------------------------------

def visualize(df: pd.DataFrame, cfg: Config) -> None:
    """Generate the time-series graph with arbitrage windows highlighted.

    Saves to cfg.output_plot (PNG) and also tries to display interactively.
    """
    fig, ax = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor("#0d1117")
    ax.set_facecolor("#0d1117")

    timestamps = df["timestamp"]

    # ── Price series ──────────────────────────────────────────────────────
    ax.plot(timestamps, df["price_yes"],    color="#58a6ff", linewidth=1.2, label="Polymarket Yes price")
    ax.plot(timestamps, df["spread_cost"],  color="#f0883e", linewidth=1.2, label="TradFi spread cost")
    ax.plot(timestamps, df["combined_cost"],color="#ffffff", linewidth=1.6, label="Combined cost", zorder=5)

    # ── Arbitrage threshold line ──────────────────────────────────────────
    ax.axhline(cfg.arbitrage_threshold, color="#ff7b72", linewidth=1.0,
               linestyle="--", label=f"Threshold ${cfg.arbitrage_threshold:.2f}")

    # ── Shade arbitrage windows ───────────────────────────────────────────
    arb_mask = df["is_arbitrage"].values
    in_window = False
    win_start  = None

    for i, flag in enumerate(arb_mask):
        ts = timestamps.iloc[i]
        if flag and not in_window:
            win_start = ts
            in_window = True
        elif not flag and in_window:
            ax.axvspan(win_start, ts, color="#238636", alpha=0.35, zorder=2)
            in_window = False
    # Close a window that runs to the end
    if in_window:
        ax.axvspan(win_start, timestamps.iloc[-1], color="#238636", alpha=0.35, zorder=2)

    # ── Styling ───────────────────────────────────────────────────────────
    arb_patch = mpatches.Patch(color="#238636", alpha=0.5, label="Arbitrage window")
    handles, labels = ax.get_legend_handles_labels()
    handles.append(arb_patch)
    labels.append("Arbitrage window")

    ax.legend(handles=handles, labels=labels, loc="upper right",
              facecolor="#161b22", edgecolor="#30363d", labelcolor="white", fontsize=9)

    ax.set_title(
        f"Cross-Market Arbitrage Analysis – {cfg.ticker}  "
        f"({cfg.start_date} -> {cfg.end_date})\n"
        f"Strikes K1=${cfg.strike_k1}  K2=${cfg.strike_k2}",
        color="white", fontsize=12, pad=12
    )
    ax.set_xlabel("Timestamp (UTC)", color="#8b949e", fontsize=9)
    ax.set_ylabel("Cost ($)", color="#8b949e", fontsize=9)
    ax.tick_params(colors="#8b949e", labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor("#30363d")
    plt.xticks(rotation=30, ha="right")

    plt.tight_layout()
    plt.savefig(cfg.output_plot, dpi=150, facecolor=fig.get_facecolor())
    print(f"[Visualize] Saved -> {cfg.output_plot}")
    try:
        plt.show()
    except Exception:
        pass  # headless environment – silently skip


# ---------------------------------------------------------------------------
# ── SECTION 6: MOCK DATA GENERATORS (Issue 001 / test seam) ────────────────
# ---------------------------------------------------------------------------

def _make_timestamps(n: int = 120, start: str = "2024-11-20 14:30") -> pd.Series:
    base = pd.Timestamp(start, tz="UTC")
    return pd.Series([base + pd.Timedelta(minutes=i) for i in range(n)])


def make_mock_polymarket(n: int = 120) -> pd.DataFrame:
    """Return synthetic Polymarket data with a known arbitrage dip."""
    import numpy as np
    rng = np.random.default_rng(42)
    ts  = _make_timestamps(n)

    # Mostly around 0.55, dips to ~0.40 in the middle window
    price = rng.normal(0.55, 0.03, n)
    # Inject a guaranteed arbitrage dip: minutes 50–70 price_yes ~0.40
    price[50:70] = rng.normal(0.40, 0.01, 20)
    price = price.clip(0.01, 0.99)

    return pd.DataFrame({"timestamp": ts, "price_yes": price})


def make_mock_polygon(n: int = 120) -> pd.DataFrame:
    """Return synthetic Bear Put Spread cost data."""
    import numpy as np
    rng = np.random.default_rng(7)
    ts  = _make_timestamps(n)

    # Mostly around 0.48, spikes down to ~0.40 in same window
    cost = rng.normal(0.48, 0.02, n)
    cost[50:70] = rng.normal(0.40, 0.01, 20)
    cost = cost.clip(0.01, 1.0)

    return pd.DataFrame({"timestamp": ts, "spread_cost": cost})


# ---------------------------------------------------------------------------
# ── SECTION 7: UNIT TESTS (all issues) ─────────────────────────────────────
# ---------------------------------------------------------------------------

class TestBearPutSpread(unittest.TestCase):
    """Issue 001 / 003: verify the spread math."""

    def _make_leg_df(self, timestamps, closes):
        return pd.DataFrame({"timestamp": timestamps, "close": closes})

    def test_spread_cost_is_k1_minus_k2(self):
        ts = pd.to_datetime(["2024-11-20 14:30", "2024-11-20 14:31"], utc=True)
        df_k1 = self._make_leg_df(ts, [3.50, 3.60])
        df_k2 = self._make_leg_df(ts, [2.50, 2.70])
        result = _calculate_bear_put_spread(df_k1, df_k2)
        self.assertAlmostEqual(result["spread_cost"].iloc[0], 1.00, places=6)
        self.assertAlmostEqual(result["spread_cost"].iloc[1], 0.90, places=6)

    def test_spread_cost_floored_at_zero(self):
        """If K2 is somehow more expensive than K1, spread_cost = 0."""
        ts = pd.to_datetime(["2024-11-20 14:30"], utc=True)
        df_k1 = self._make_leg_df(ts, [1.00])
        df_k2 = self._make_leg_df(ts, [2.00])
        result = _calculate_bear_put_spread(df_k1, df_k2)
        self.assertEqual(result["spread_cost"].iloc[0], 0.0)


class TestTimestampAlignment(unittest.TestCase):
    """Issue 003: inner join drops missing-minute rows cleanly."""

    def _poly_df(self, ts_list, prices):
        return pd.DataFrame({"timestamp": pd.to_datetime(ts_list, utc=True), "price_yes": prices})

    def _pg_df(self, ts_list, costs):
        return pd.DataFrame({"timestamp": pd.to_datetime(ts_list, utc=True), "spread_cost": costs})

    def test_inner_join_drops_unmatched_rows(self):
        poly = self._poly_df(["2024-11-20 14:30", "2024-11-20 14:31", "2024-11-20 14:32"], [0.5, 0.6, 0.7])
        pg   = self._pg_df( ["2024-11-20 14:30", "2024-11-20 14:32"],                       [0.4, 0.3])
        result = align_timestamps(poly, pg)
        self.assertEqual(len(result), 2)
        self.assertNotIn(pd.Timestamp("2024-11-20 14:31", tz="UTC"), result["timestamp"].values)

    def test_combined_cost_is_sum(self):
        poly = self._poly_df(["2024-11-20 14:30"], [0.50])
        pg   = self._pg_df( ["2024-11-20 14:30"], [0.45])
        result = align_timestamps(poly, pg)
        self.assertAlmostEqual(result["combined_cost"].iloc[0], 0.95, places=9)


class TestArbitrageDetection(unittest.TestCase):
    """Issue 004: flag_arbitrage_windows correctly identifies sub-$1.00 rows."""

    def _combined_df(self, costs):
        ts = pd.to_datetime(
            [f"2024-11-20 14:{30+i:02d}" for i in range(len(costs))], utc=True
        )
        return pd.DataFrame({
            "timestamp":    ts,
            "price_yes":    [c * 0.5 for c in costs],
            "spread_cost":  [c * 0.5 for c in costs],
            "combined_cost": costs,
        })

    def test_flags_below_threshold(self):
        df = self._combined_df([0.95, 1.05, 0.80, 1.10, 0.99])
        result = flag_arbitrage_windows(df, threshold=1.00)
        expected = [True, False, True, False, True]
        self.assertEqual(result["is_arbitrage"].tolist(), expected)

    def test_no_arbitrage_when_all_above(self):
        df = self._combined_df([1.01, 1.05, 1.10])
        result = flag_arbitrage_windows(df, threshold=1.00)
        self.assertFalse(result["is_arbitrage"].any())

    def test_all_arbitrage_when_all_below(self):
        df = self._combined_df([0.90, 0.85, 0.95])
        result = flag_arbitrage_windows(df, threshold=1.00)
        self.assertTrue(result["is_arbitrage"].all())

    def test_window_ids_are_contiguous(self):
        """Verify that contiguous runs share the same window_id."""
        df = self._combined_df([0.90, 0.91, 1.10, 0.80, 0.79])
        result = flag_arbitrage_windows(df, threshold=1.00)
        # Rows 0,1 → window A;  rows 3,4 → window B
        w0 = result.loc[0, "window_id"]
        w1 = result.loc[1, "window_id"]
        w3 = result.loc[3, "window_id"]
        self.assertEqual(w0, w1)
        self.assertNotEqual(w0, w3)

    def test_summarise_windows_count(self):
        df = self._combined_df([0.90, 0.91, 1.10, 0.80, 0.79])
        flagged = flag_arbitrage_windows(df, threshold=1.00)
        summary = summarise_windows(flagged)
        self.assertEqual(len(summary), 2)   # two distinct windows

    def test_summarise_duration(self):
        df = self._combined_df([0.90, 0.91, 0.85])
        flagged = flag_arbitrage_windows(df, threshold=1.00)
        summary = summarise_windows(flagged)
        self.assertEqual(summary["duration_min"].iloc[0], 3)

    def test_mock_data_produces_arbitrage_windows(self):
        """End-to-end smoke test using mock data generators (Issue 001 seam)."""
        poly = make_mock_polymarket(120)
        pg   = make_mock_polygon(120)
        df   = align_timestamps(poly, pg)
        df   = flag_arbitrage_windows(df, threshold=1.00)
        # Our synthetic data is designed to have arbitrage windows
        self.assertTrue(df["is_arbitrage"].any(), "Expected arbitrage windows in mock data")


def run_tests() -> None:
    """Run built-in unit tests and exit with appropriate code."""
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    for cls in (TestBearPutSpread, TestTimestampAlignment, TestArbitrageDetection):
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2, stream=sys.stdout)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


# ---------------------------------------------------------------------------
# ── SECTION 8: MAIN ENTRYPOINT (Issue 001) ─────────────────────────────────
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=textwrap.dedent("""\
            Cross-Market Arbitrage Backtester (Phase 1)
            -------------------------------------------
            Fetches Polymarket + Polygon data, aligns timestamps, computes
            the Bear Put Spread cost, and identifies arbitrage windows.
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mock",   action="store_true", help="Use synthetic mock data (no API keys needed)")
    parser.add_argument("--test",   action="store_true", help="Run built-in unit tests and exit")
    parser.add_argument("--ticker", default=None, help="Override ticker symbol")
    parser.add_argument("--start",  default=None, help="Override start date YYYY-MM-DD")
    parser.add_argument("--end",    default=None, help="Override end date   YYYY-MM-DD")
    parser.add_argument("--k1",     type=float, default=None, help="Override K1 strike")
    parser.add_argument("--k2",     type=float, default=None, help="Override K2 strike")
    parser.add_argument("--polygon-key", default=None, dest="polygon_key",
                        help="Polygon.io API key (overrides CONFIG.polygon_api_key)")
    parser.add_argument("--proxy",  default=None, help="HTTP/HTTPS proxy URL (e.g. http://127.0.0.1:8080)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # ── Test mode ─────────────────────────────────────────────────────────
    if args.test:
        run_tests()
        return  # run_tests calls sys.exit; this is unreachable

    # ── Apply CLI overrides to config ─────────────────────────────────────
    cfg = CONFIG
    if args.ticker:      cfg.ticker         = args.ticker
    if args.start:       cfg.start_date     = args.start
    if args.end:         cfg.end_date       = args.end
    if args.k1 is not None: cfg.strike_k1  = args.k1
    if args.k2 is not None: cfg.strike_k2  = args.k2
    if args.polygon_key: cfg.polygon_api_key = args.polygon_key
    if args.proxy:       cfg.proxy_url       = args.proxy

    print("=" * 60)
    print("  Cross-Market Arbitrage Backtester - Phase 1")
    print("=" * 60)
    print(f"  Ticker     : {cfg.ticker}")
    print(f"  Date range : {cfg.start_date} -> {cfg.end_date}")
    print(f"  Strikes    : K1=${cfg.strike_k1}  K2=${cfg.strike_k2}")
    print(f"  Mode       : {'MOCK' if args.mock else 'LIVE'}")
    print("=" * 60)

    # ── Data ingestion ────────────────────────────────────────────────────
    if args.mock:
        print("\n[Mock] Generating synthetic Polymarket data...")
        df_poly = make_mock_polymarket()

        print("[Mock] Generating synthetic Polygon spread data...")
        df_pg = make_mock_polygon()
    else:
        df_poly = fetch_polymarket(cfg)
        df_pg   = fetch_polygon(cfg)

    # ── Analysis ──────────────────────────────────────────────────────────
    print()
    df = run_analysis(df_poly, df_pg, cfg)

    # ── Visualisation ─────────────────────────────────────────────────────
    print()
    visualize(df, cfg)

    print("\nDone.")


if __name__ == "__main__":
    main()
