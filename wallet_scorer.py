"""
Polymarket Wallet Scorer
Descobre e ranqueia as melhores wallets por consistência histórica.

Score composto:
  - ROI total
  - Win rate (% de mercados lucrativos)
  - Longevidade (meses ativos)
  - Volume (prova de comprometimento)
  - Consistência (desvio padrão dos retornos)
"""

import requests
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# ── API ────────────────────────────────────────────────────────────────────────

def _get(url, params=None, silent=False):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if not silent:
            print(f"  [{r.status_code}] {url.split('/')[-1]} {params or ''}")
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        if not silent:
            print(f"  [ERR] {e}")
        return None

# ── Discover wallets ───────────────────────────────────────────────────────────

def discover_wallets(target=80):
    """Coleta o máximo de wallets únicas via trades API."""
    print(f"\n🔍 Discovering wallets (target: {target})...")

    wallets = set()

    # Pega trades recentes com diferentes offsets
    offsets = [0, 100, 200, 300, 500, 800, 1000, 1500, 2000]
    for offset in offsets:
        data = _get("https://data-api.polymarket.com/trades",
                    {"limit": 100, "offset": offset}, silent=True)
        if not data or not isinstance(data, list):
            break
        for t in data:
            addr = t.get("proxyWallet", "")
            if addr and len(addr) == 42:
                wallets.add(addr)
        print(f"  offset={offset} → {len(wallets)} unique wallets so far")
        if len(wallets) >= target:
            break

    # Também descobre via mercados ativos
    markets = _get("https://gamma-api.polymarket.com/markets",
                   {"limit": 20, "active": "true"}, silent=True) or []
    markets = markets if isinstance(markets, list) else []

    for mkt in markets[:10]:
        cid = mkt.get("conditionId", "")
        if not cid:
            continue
        trades = _get("https://data-api.polymarket.com/trades",
                      {"market": cid, "limit": 100}, silent=True)
        if trades and isinstance(trades, list):
            for t in trades:
                addr = t.get("proxyWallet", "")
                if addr and len(addr) == 42:
                    wallets.add(addr)

    result = list(wallets)
    print(f"  ✅ {len(result)} unique wallets discovered")
    return result

# ── Per-wallet analysis ────────────────────────────────────────────────────────

def analyze_wallet(address):
    """
    Analisa histórico completo de uma wallet.
    Retorna métricas de performance ou None se dados insuficientes.
    """
    # Busca até 500 trades históricos
    activity = _get("https://data-api.polymarket.com/activity",
                    {"user": address, "limit": 500}, silent=True)
    if not activity or not isinstance(activity, list):
        return None

    if len(activity) < 3:  # mínimo de trades para análise
        return None

    # Posições abertas (para PnL não realizado)
    positions = _get("https://data-api.polymarket.com/positions",
                     {"user": address}, silent=True) or []
    if isinstance(positions, dict):
        positions = positions.get("data") or positions.get("positions") or []

    # ── Calcula métricas ───────────────────────────────────────────────────────

    # Agrupa trades por mercado
    markets = defaultdict(lambda: {
        "buy_usdc": 0.0, "sell_usdc": 0.0,
        "trades": 0, "title": "", "timestamps": []
    })

    timestamps_all = []
    total_buy = total_sell = total_volume = 0.0

    for t in activity:
        side  = (t.get("side") or t.get("type") or "").upper()
        usdc  = float(t.get("usdcSize") or t.get("size") or 0)
        cid   = t.get("conditionId") or t.get("market") or "?"
        ts    = t.get("timestamp") or 0
        title = t.get("title") or cid[:20]

        markets[cid]["title"]    = title
        markets[cid]["trades"]  += 1
        markets[cid]["timestamps"].append(ts)
        total_volume += usdc

        if side == "BUY":
            markets[cid]["buy_usdc"]  += usdc
            total_buy += usdc
        elif side == "SELL":
            markets[cid]["sell_usdc"] += usdc
            total_sell += usdc

        if ts:
            timestamps_all.append(ts)

    if total_buy < 1:
        return None

    # PnL por mercado (apenas mercados com pelo menos uma operação completa)
    market_results = []
    for cid, m in markets.items():
        if m["buy_usdc"] > 0:
            pnl = m["sell_usdc"] - m["buy_usdc"]
            roi = pnl / m["buy_usdc"] * 100
            market_results.append({
                "title":     m["title"],
                "pnl":       round(pnl, 2),
                "roi":       round(roi, 2),
                "buy":       round(m["buy_usdc"], 2),
                "sell":      round(m["sell_usdc"], 2),
                "trades":    m["trades"],
            })

    if not market_results:
        return None

    # Adiciona PnL das posições abertas (não realizadas)
    open_pnl = sum(float(p.get("cashPnl") or 0) for p in positions)
    realized_pnl = sum(float(p.get("realizedPnl") or 0) for p in positions)
    total_pnl = (total_sell - total_buy) + open_pnl

    # Win rate
    wins   = [m for m in market_results if m["pnl"] > 0]
    losses = [m for m in market_results if m["pnl"] < 0]
    win_rate = len(wins) / len(market_results) * 100 if market_results else 0

    # Longevidade
    if len(timestamps_all) >= 2:
        ts_sorted = sorted(timestamps_all)
        days_active = (ts_sorted[-1] - ts_sorted[0]) / 86400
        months_active = days_active / 30
    else:
        months_active = 0
        days_active   = 0

    # ROI total
    roi_total = total_pnl / total_buy * 100 if total_buy > 0 else 0

    # Consistência: desvio padrão dos ROIs por mercado (menor = mais consistente)
    rois = [m["roi"] for m in market_results if m["buy"] > 5]  # filtra trades pequenos
    if len(rois) >= 2:
        mean_roi = sum(rois) / len(rois)
        variance = sum((r - mean_roi) ** 2 for r in rois) / len(rois)
        std_dev  = math.sqrt(variance)
    else:
        std_dev = 999

    # Sharpe simplificado (ROI / desvio padrão)
    sharpe = roi_total / std_dev if std_dev > 0 else 0

    # ── Score composto ─────────────────────────────────────────────────────────
    # Normalizado 0-100
    score = 0.0
    score += min(roi_total,   50)  * 0.30   # ROI (cap 50%)
    score += min(win_rate,   100)  * 0.30   # Win rate
    score += min(months_active, 12) / 12 * 100 * 0.20  # Longevidade
    score += min(total_volume / 1000, 100) * 0.10  # Volume
    score += min(max(sharpe * 10, 0), 100)  * 0.10  # Consistência

    return {
        "address":        address,
        "score":          round(score, 2),
        "roi_pct":        round(roi_total, 2),
        "win_rate_pct":   round(win_rate, 1),
        "total_pnl":      round(total_pnl, 2),
        "realized_pnl":   round(realized_pnl, 2),
        "open_pnl":       round(open_pnl, 2),
        "total_buy":      round(total_buy, 2),
        "total_volume":   round(total_volume, 2),
        "months_active":  round(months_active, 1),
        "days_active":    round(days_active),
        "total_trades":   len(activity),
        "markets_traded": len(market_results),
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate_pct":   round(win_rate, 1),
        "std_dev":        round(std_dev, 2),
        "sharpe":         round(sharpe, 3),
        "best_markets":   sorted(market_results, key=lambda x: x["pnl"], reverse=True)[:3],
        "worst_markets":  sorted(market_results, key=lambda x: x["pnl"])[:2],
        "positions_open": len(positions),
    }

# ── Main ───────────────────────────────────────────────────────────────────────

def run_scorer(min_score=15, min_months=1, min_trades=5):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts} UTC] 🏆 Polymarket Wallet Scorer")
    print("=" * 60)

    # 1. Descobre wallets
    all_wallets = discover_wallets(target=100)

    # 2. Analisa cada wallet
    print(f"\n📊 Analyzing {len(all_wallets)} wallets...")
    results = []
    skipped = 0

    for i, addr in enumerate(all_wallets):
        r = analyze_wallet(addr)
        if r is None:
            skipped += 1
            continue

        # Filtros mínimos
        if r["total_trades"] < min_trades:
            skipped += 1
            continue
        if r["months_active"] < min_months:
            skipped += 1
            continue

        results.append(r)
        verdict = "✅" if r["score"] >= min_score else "  "
        print(f"  [{i+1:03d}] {addr[:12]}… "
              f"score={r['score']:5.1f} "
              f"roi={r['roi_pct']:+6.1f}% "
              f"win={r['win_rate_pct']:4.1f}% "
              f"months={r['months_active']:4.1f} "
              f"trades={r['total_trades']:3d} "
              f"{verdict}")

    # 3. Ranqueia e filtra
    results.sort(key=lambda x: x["score"], reverse=True)
    top_wallets = [r for r in results if r["score"] >= min_score]

    # 4. Salva
    output = {
        "generated_at":    datetime.utcnow().isoformat(),
        "total_analyzed":  len(results) + skipped,
        "total_qualified": len(top_wallets),
        "filters": {
            "min_score":  min_score,
            "min_months": min_months,
            "min_trades": min_trades,
        },
        "top_wallets": top_wallets,
        "all_results": results[:50],
    }
    (DATA_DIR / "wallet_scores.json").write_text(json.dumps(output, indent=2))

    # 5. Print ranking
    print(f"\n{'='*70}")
    print(f"{'#':<4} {'Wallet':<14} {'Score':>6} {'ROI':>7} {'WinRate':>8} "
          f"{'Months':>7} {'Trades':>7} {'PnL':>10}")
    print(f"{'─'*70}")
    for i, r in enumerate(top_wallets[:15]):
        print(f"{i+1:<4} {r['address'][:12]:<14} {r['score']:>6.1f} "
              f"{r['roi_pct']:>+6.1f}% {r['win_rate_pct']:>7.1f}% "
              f"{r['months_active']:>7.1f} {r['total_trades']:>7} "
              f"${r['total_pnl']:>9.2f}")
    print(f"{'='*70}")
    print(f"\n✅ {len(top_wallets)} qualified wallets saved to data/wallet_scores.json")

    # 6. Atualiza seed list no tracker
    if top_wallets:
        best_addrs = [w["address"] for w in top_wallets[:12]]
        seed_file  = DATA_DIR / "best_wallets.json"
        seed_file.write_text(json.dumps({
            "updated_at": datetime.utcnow().isoformat(),
            "wallets":    best_addrs,
        }, indent=2))
        print(f"💾 Top {len(best_addrs)} addresses saved to data/best_wallets.json")

    return top_wallets


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score",  type=float, default=15)
    ap.add_argument("--min-months", type=float, default=1)
    ap.add_argument("--min-trades", type=int,   default=5)
    args = ap.parse_args()
    run_scorer(args.min_score, args.min_months, args.min_trades)
