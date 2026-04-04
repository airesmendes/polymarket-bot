"""
Polymarket Backtest v2
Usa campos confirmados pela API: usdcSize, side, proxyWallet, conditionId
"""

import requests
import json
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

def _get(url, params=None, silent=False):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if not silent:
            print(f"  [{r.status_code}] {url.split('/')[-1]}")
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        if not silent:
            print(f"  [ERR] {e}")
        return None

def get_activity(address, limit=500):
    data = _get(
        "https://data-api.polymarket.com/activity",
        {"user": address, "limit": limit},
        silent=True
    )
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data") or data.get("activities") or []
    return []

def get_positions(address):
    data = _get(
        "https://data-api.polymarket.com/positions",
        {"user": address},
        silent=True
    )
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data") or data.get("positions") or []
    return []

def parse_ts(val):
    """Converte timestamp (unix int ou ISO string) para datetime UTC."""
    if not val:
        return None
    try:
        if isinstance(val, (int, float)):
            return datetime.fromtimestamp(float(val), tz=timezone.utc).replace(tzinfo=None)
        val = str(val).replace("Z", "+00:00")
        dt  = datetime.fromisoformat(val)
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except Exception:
        return None

def analyze_wallet(address: str, days_back: int = 90) -> dict | None:
    print(f"  {address[:14]}…", end=" ", flush=True)

    activity = get_activity(address, limit=500)
    if not activity:
        print("no activity")
        return None

    positions = get_positions(address)

    cutoff = datetime.utcnow() - timedelta(days=days_back)

    # Agrupa trades por mercado
    markets: dict[str, dict] = {}
    total_trades = 0

    for t in activity:
        ts = parse_ts(t.get("timestamp") or t.get("createdAt"))
        if ts and ts < cutoff:
            continue  # fora do período

        # Campos confirmados pela API
        cid   = t.get("conditionId") or t.get("market") or t.get("asset") or "?"
        side  = (t.get("side") or t.get("type") or "").upper()
        usdc  = float(t.get("usdcSize") or t.get("size") or t.get("amount") or 0)
        title = t.get("title") or t.get("question") or cid[:40]
        outcome = t.get("outcome") or t.get("outcomeIndex") or ""

        if cid not in markets:
            markets[cid] = {
                "conditionId": cid,
                "title":       title,
                "outcome":     outcome,
                "buy_usdc":    0.0,
                "sell_usdc":   0.0,
                "trade_count": 0,
            }
        markets[cid]["trade_count"] += 1
        markets[cid]["title"]    = title  # atualiza com o mais recente
        markets[cid]["outcome"]  = outcome

        if side == "BUY":
            markets[cid]["buy_usdc"] += usdc
        elif side == "SELL":
            markets[cid]["sell_usdc"] += usdc

        total_trades += 1

    if not markets:
        print("no trades in period")
        return None

    # PnL das posições abertas (não realizadas)
    pos_map = {p.get("conditionId", ""): p for p in positions}
    open_pnl = sum(
        float(p.get("cashPnl") or 0) + float(p.get("realizedPnl") or 0)
        for p in positions
    )

    # Calcula PnL por mercado
    market_results = []
    total_invested = total_returned = 0.0

    for cid, m in markets.items():
        buy  = m["buy_usdc"]
        sell = m["sell_usdc"]
        if buy < 0.01:
            continue

        # Se tem posição aberta, adiciona valor atual
        pos       = pos_map.get(cid, {})
        cur_value = float(pos.get("currentValue") or 0)
        pnl       = (sell + cur_value) - buy
        roi       = pnl / buy * 100 if buy > 0 else 0

        total_invested += buy
        total_returned += sell + cur_value

        market_results.append({
            "conditionId": cid,
            "title":       m["title"][:70],
            "outcome":     m["outcome"],
            "buy_usdc":    round(buy, 2),
            "sell_usdc":   round(sell, 2),
            "cur_value":   round(cur_value, 2),
            "pnl":         round(pnl, 2),
            "roi_pct":     round(roi, 2),
            "trade_count": m["trade_count"],
        })

    if not market_results:
        print("no market results")
        return None

    market_results.sort(key=lambda x: x["pnl"], reverse=True)

    wins     = [m for m in market_results if m["pnl"] > 0]
    losses   = [m for m in market_results if m["pnl"] < 0]
    total_pnl = round(total_returned - total_invested, 2)
    roi_total = round(total_pnl / total_invested * 100, 2) if total_invested > 0 else 0
    win_rate  = round(len(wins) / len(market_results) * 100, 1) if market_results else 0
    avg_trade = round(total_invested / max(total_trades, 1), 2)

    print(f"roi={roi_total:+.1f}% win={win_rate:.0f}% trades={total_trades} pnl=${total_pnl:.0f}")

    return {
        "address":        address,
        "period_days":    days_back,
        "total_trades":   total_trades,
        "markets_traded": len(market_results),
        "total_invested": round(total_invested, 2),
        "total_returned": round(total_returned, 2),
        "total_pnl":      total_pnl,
        "open_pnl":       round(open_pnl, 2),
        "roi_pct":        roi_total,
        "win_rate_pct":   win_rate,
        "winning_markets":len(wins),
        "losing_markets": len(losses),
        "avg_trade_size": avg_trade,
        "best_markets":   market_results[:5],
        "worst_markets":  market_results[-3:],
        "generated_at":   datetime.utcnow().isoformat(),
    }

def run_backtest(wallets=None, days_back=90):
    print(f"\n📊 Backtest v2 — {days_back} dias")
    print("=" * 60)

    # Carrega wallets do tracker se não especificado
    if not wallets:
        wf = DATA_DIR / "wallets.json"
        bf = DATA_DIR / "best_wallets.json"
        if bf.exists():
            data    = json.loads(bf.read_text())
            wallets = data.get("wallets", [])
            print(f"Using {len(wallets)} wallets from best_wallets.json")
        elif wf.exists():
            data    = json.loads(wf.read_text())
            wallets = [w["address"] for w in data.get("wallets", [])]
            print(f"Using {len(wallets)} wallets from wallets.json")
        else:
            print("No wallet source found.")
            return []

    print(f"Analyzing {len(wallets)} wallets...\n")

    results = [r for addr in wallets if (r := analyze_wallet(addr, days_back))]
    results.sort(key=lambda x: x["roi_pct"], reverse=True)

    output = {
        "generated_at":     datetime.utcnow().isoformat(),
        "period_days":      days_back,
        "wallets_analyzed": len(results),
        "results":          results,
    }
    (DATA_DIR / "backtest.json").write_text(json.dumps(output, indent=2))

    # Print ranking
    print(f"\n{'─'*75}")
    print(f"{'Wallet':<14} {'ROI':>7} {'Win%':>6} {'PnL':>11} "
          f"{'Trades':>7} {'Mercados':>9}")
    print(f"{'─'*75}")
    for r in results[:15]:
        flag = "🟢" if r["roi_pct"] > 0 else "🔴"
        print(f"{r['address'][:12]:<14} {r['roi_pct']:>+6.1f}% "
              f"{r['win_rate_pct']:>5.1f}% "
              f"${r['total_pnl']:>10.2f} "
              f"{r['total_trades']:>7} "
              f"{r['markets_traded']:>9} {flag}")
    print(f"{'─'*75}")
    print(f"\n✅ {len(results)} wallets analisadas → data/backtest.json\n")
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days",    type=int, default=90)
    ap.add_argument("--wallets", nargs="*")
    args = ap.parse_args()
    run_backtest(wallets=args.wallets, days_back=args.days)
