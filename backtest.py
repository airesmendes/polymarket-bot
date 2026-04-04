"""
Polymarket Backtest
Analisa performance histórica das carteiras rastreadas.

Uso:
  python backtest.py                        # usa wallets.json
  python backtest.py --days 30
  python backtest.py --wallets 0xABC 0xDEF --days 60
"""

import json
import argparse
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_API = "https://data-api.polymarket.com"
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {"User-Agent": "PolymarketBot/1.0"}

# ── API helpers ────────────────────────────────────────────────────────────────

def _get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [WARN] {url} → {e}")
        return None

def get_activity(address, limit=500):
    data = _get(f"{DATA_API}/activity", {"user": address, "limit": limit})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data") or data.get("activities") or []
    return []

def parse_ts(ts_str):
    """Parse ISO timestamp string → datetime (UTC, naive)."""
    if not ts_str:
        return None
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None

# ── Performance calculation ────────────────────────────────────────────────────

def analyze_wallet(address: str, days_back: int = 90) -> dict | None:
    print(f"  Analyzing {address[:12]}... ({days_back}d)")

    trades = get_activity(address, limit=500)
    if not trades:
        print(f"    No activity found.")
        return None

    cutoff = datetime.utcnow() - timedelta(days=days_back)

    filtered = []
    for t in trades:
        ts = parse_ts(t.get("timestamp") or t.get("createdAt") or t.get("time"))
        if ts is None or ts >= cutoff:
            filtered.append(t)

    if not filtered:
        return None

    # Group by market
    markets: dict[str, dict] = {}
    for t in filtered:
        mid = (t.get("conditionId") or t.get("market") or
               t.get("asset_id") or t.get("assetId") or "unknown")
        if mid not in markets:
            markets[mid] = {
                "market_id": mid,
                "question":  (t.get("title") or t.get("question") or t.get("market") or mid)[:80],
                "trades":    [],
                "buy_usdc":  0.0,
                "sell_usdc": 0.0,
            }
        markets[mid]["trades"].append(t)

    total_invested = 0.0
    total_returned = 0.0
    market_results = []

    for mid, mdata in markets.items():
        for t in mdata["trades"]:
            side = (t.get("type") or t.get("side") or "").upper()
            usdc = float(t.get("usdcSize") or t.get("cost") or t.get("amount") or 0)
            if side in ("BUY", "LONG", "YES"):
                mdata["buy_usdc"] += usdc
            elif side in ("SELL", "SHORT", "NO"):
                mdata["sell_usdc"] += usdc

        pnl = mdata["sell_usdc"] - mdata["buy_usdc"]
        mdata["pnl"]         = round(pnl, 4)
        mdata["trade_count"] = len(mdata["trades"])
        total_invested      += mdata["buy_usdc"]
        total_returned      += mdata["sell_usdc"]
        market_results.append(mdata)

    market_results.sort(key=lambda x: x["pnl"], reverse=True)

    wins      = [m for m in market_results if m["pnl"] > 0]
    losses    = [m for m in market_results if m["pnl"] < 0]
    total_pnl = round(total_returned - total_invested, 4)
    roi       = round(total_pnl / total_invested * 100, 2) if total_invested > 0 else 0
    win_rate  = round(len(wins) / max(len(market_results), 1) * 100, 1)

    return {
        "address":            address,
        "period_days":        days_back,
        "total_trades":       len(filtered),
        "markets_traded":     len(market_results),
        "total_invested":     round(total_invested, 2),
        "total_returned":     round(total_returned, 2),
        "total_pnl":          total_pnl,
        "roi_pct":            roi,
        "win_rate_pct":       win_rate,
        "winning_markets":    len(wins),
        "losing_markets":     len(losses),
        "best_markets":       market_results[:5],
        "worst_markets":      market_results[-3:],
        "avg_trade_size":     round(total_invested / max(len(filtered), 1), 2),
        "generated_at":       datetime.utcnow().isoformat(),
    }

# ── Main ───────────────────────────────────────────────────────────────────────

def run_backtest(wallets: list[str] | None = None, days_back: int = 90):
    if not wallets:
        wf = DATA_DIR / "wallets.json"
        if not wf.exists():
            print("No wallets provided and wallets.json not found. Run polymarket_tracker.py first.")
            return []
        data     = json.loads(wf.read_text())
        wallets  = [w["address"] for w in data.get("wallets", [])[:15]]

    print(f"\n📊 Backtest: {len(wallets)} wallets | {days_back} days\n")

    results = [r for addr in wallets if (r := analyze_wallet(addr, days_back))]
    results.sort(key=lambda x: x["roi_pct"], reverse=True)

    output = {
        "generated_at":     datetime.utcnow().isoformat(),
        "period_days":      days_back,
        "wallets_analyzed": len(results),
        "results":          results,
    }
    (DATA_DIR / "backtest.json").write_text(json.dumps(output, indent=2))

    print(f"\n{'─'*70}")
    print(f"{'Wallet':<14} {'ROI':>7} {'Win%':>6} {'PnL':>10} {'Trades':>7}")
    print(f"{'─'*70}")
    for r in results[:10]:
        print(f"{r['address'][:12]:<14} {r['roi_pct']:>6.1f}% {r['win_rate_pct']:>5.1f}%"
              f" ${r['total_pnl']:>9.2f} {r['total_trades']:>7}")
    print(f"{'─'*70}")
    print(f"\n✅ Saved to data/backtest.json\n")
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90, help="Período em dias (default: 90)")
    ap.add_argument("--wallets", nargs="*", help="Endereços específicos (opcional)")
    args = ap.parse_args()
    run_backtest(wallets=args.wallets, days_back=args.days)
