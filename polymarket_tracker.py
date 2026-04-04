"""
Polymarket Wallet Tracker — v4
- Usa data-api /trades (confirmado 200)
- Usa gamma-api /markets para buscar trades por mercado
- Fallback: seed list de wallets conhecidas + enriquece via /activity?user=
"""

import requests
import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# ── Seed wallets (top traders públicos do Polymarket) ─────────────────────────
# Wallets publicadas em leaderboards históricos e artigos sobre Polymarket
SEED_WALLETS = [
    "0x492442eab586f242b53bda933fd5de859c8a3782",
    "0x02227b8f5a9636e895607edd3185ed6ee5598ff7",
    "0xefbc5fec8d7b0acdc8911bdd9a98d6964308f9a2",
    "0xc2e7800b5af46e6093872b177b7a5e7f0563be51",
    "0x019782cab5d844f02bafb71f512758be78579f3c",
    "0xb45a797faa52b0fd8adc56d30382022b7b12192c",
    "0xbddf61af533ff524d27154e589d2d7a81510c684",
    "0x2a2c53bd278c04da9962fcf96490e17f3dfb9bc1",
]

def _get(url, params=None, label=""):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        print(f"  [{r.status_code}] {label or url.split('/')[-1][:50]}")
        if r.status_code == 200:
            return r.json()
        print(f"    → {r.text[:100]}")
        return None
    except Exception as e:
        print(f"  [ERR] {label}: {e}")
        return None

# ── Strategy 1: data-api /trades (retornou 200!) ──────────────────────────────

def get_wallets_from_trades_api(limit=30):
    print("\n  [TradesAPI] data-api.polymarket.com/trades...")

    # Variações de params para o endpoint /trades
    attempts = [
        {"limit": 200},
        {"limit": 200, "sortBy": "VOLUME"},
        {"limit": 200, "type": "BUY"},
        {"size": 200},
        {"count": 200},
        {},
    ]

    for params in attempts:
        data = _get("https://data-api.polymarket.com/trades", params,
                    f"trades {params}")
        if not data:
            continue

        items = data if isinstance(data, list) else (
            data.get("data") or data.get("trades") or data.get("results") or [])

        print(f"    response type={type(data).__name__}, "
              f"items={len(items) if isinstance(items, list) else 'n/a'}")

        if isinstance(data, dict) and not items:
            print(f"    dict keys: {list(data.keys())}")

        if not items:
            continue

        # Extrai traders únicos — campo confirmado: proxyWallet
        traders = {}
        for t in items:
            addr = (t.get("proxyWallet") or t.get("maker") or t.get("maker_address") or
                    t.get("user") or t.get("trader") or "")
            if addr and len(addr) > 10:
                if addr not in traders:
                    traders[addr] = {"address": addr, "volume": 0.0, "trades": 0, "rank": 0}
                traders[addr]["volume"] += float(t.get("size") or t.get("amount") or
                                                  t.get("usdcSize") or 0)
                traders[addr]["trades"] += 1

        if traders:
            result = sorted(traders.values(), key=lambda x: x["volume"], reverse=True)[:limit]
            for i, r in enumerate(result):
                r["rank"] = i + 1
            print(f"    ✅ {len(result)} unique traders")
            return result
        else:
            # Show sample keys to debug
            if items:
                print(f"    [DEBUG] trade keys: {list(items[0].keys())[:12]}")
                print(f"    [DEBUG] trade sample: { {k: items[0][k] for k in list(items[0].keys())[:6]} }")

    return []

# ── Strategy 2: gamma-api markets → trades por mercado ───────────────────────

def get_wallets_from_markets(limit=25):
    print("\n  [Markets] gamma-api markets + trades...")

    # Busca mercados ativos com maior volume
    markets_data = _get(
        "https://gamma-api.polymarket.com/markets",
        {"limit": 20, "active": "true", "closed": "false",
         "_sort": "volume", "_order": "DESC"},
        "gamma markets"
    )

    if not markets_data:
        markets_data = _get(
            "https://gamma-api.polymarket.com/markets",
            {"limit": 20},
            "gamma markets v2"
        )

    markets = markets_data if isinstance(markets_data, list) else (
        (markets_data or {}).get("data") or (markets_data or {}).get("markets") or [])

    if not markets:
        print("    no markets found")
        return []

    print(f"    found {len(markets)} markets")
    if markets:
        print(f"    market keys sample: {list(markets[0].keys())[:10]}")

    traders = {}
    for market in markets[:8]:
        cid = (market.get("conditionId") or market.get("condition_id") or
               market.get("id") or "")
        if not cid:
            continue

        # Tenta buscar trades/positions deste mercado
        for url, params in [
            ("https://data-api.polymarket.com/trades",
             {"market": cid, "limit": 50}),
            ("https://data-api.polymarket.com/trades",
             {"conditionId": cid, "limit": 50}),
            ("https://data-api.polymarket.com/positions",
             {"market": cid, "limit": 50}),
        ]:
            d = _get(url, params, f"{url.split('/')[-1]} {cid[:12]}")
            items = d if isinstance(d, list) else ((d or {}).get("data") or [])
            if not items:
                continue
            for item in items:
                addr = (item.get("user") or item.get("maker") or
                        item.get("taker") or item.get("address") or "")
                if addr and len(addr) > 10:
                    if addr not in traders:
                        traders[addr] = {"address": addr, "volume": 0.0,
                                        "trades": 0, "rank": 0}
                    traders[addr]["volume"] += float(
                        item.get("size") or item.get("usdcSize") or
                        item.get("amount") or 0)
                    traders[addr]["trades"] += 1
            if traders:
                break

    if traders:
        result = sorted(traders.values(), key=lambda x: x["volume"], reverse=True)[:limit]
        for i, r in enumerate(result):
            r["rank"] = i + 1
        print(f"    ✅ {len(result)} traders from markets")
        return result

    return []

# ── Strategy 3: Seed wallets ─────────────────────────────────────────────────

def get_wallets_from_seed():
    print("\n  [Seed] Using known top wallets...")
    wallets = [{"address": a, "volume": 0.0, "trades": 0, "rank": i+1}
               for i, a in enumerate(SEED_WALLETS)]
    print(f"    ✅ {len(wallets)} seed wallets loaded")
    return wallets

# ── Per-wallet enrichment ─────────────────────────────────────────────────────

def get_wallet_positions(address):
    for url, params in [
        ("https://data-api.polymarket.com/positions",
         {"user": address, "sizeThreshold": "0.01"}),
        ("https://data-api.polymarket.com/positions",
         {"address": address}),
    ]:
        d = _get(url, params, f"pos {address[:10]}")
        if d:
            items = d if isinstance(d, list) else (
                d.get("data") or d.get("positions") or [])
            if items:
                return items
    return []

def get_wallet_activity(address, limit=20):
    # Este endpoint FUNCIONA com ?user=
    for url, params in [
        ("https://data-api.polymarket.com/activity",
         {"user": address, "limit": limit}),
    ]:
        d = _get(url, params, f"act {address[:10]}")
        if d:
            items = d if isinstance(d, list) else (
                d.get("data") or d.get("activities") or [])
            if items:
                return items
    return []

def get_wallet_pnl(address):
    """Tenta buscar PNL da wallet via profile endpoint."""
    for url, params in [
        ("https://data-api.polymarket.com/profiles", {"user": address}),
        ("https://data-api.polymarket.com/profiles", {"address": address}),
        ("https://data-api.polymarket.com/users",    {"user": address}),
    ]:
        d = _get(url, params, f"profile {address[:10]}")
        if d:
            item = d[0] if isinstance(d, list) and d else d
            if isinstance(item, dict) and any(
                    item.get(k) for k in ("profit","pnl","volume")):
                return {
                    "pnl":    float(item.get("profit") or item.get("pnl") or 0),
                    "volume": float(item.get("volume") or 0),
                    "roi":    float(item.get("roi") or 0),
                }
    return {"pnl": 0.0, "volume": 0.0, "roi": 0.0}

def calc_pnl_from_activity(activity):
    """Calcula PNL e volume a partir da atividade. Campos confirmados: usdcSize, side."""
    buy_total = sell_total = volume = 0.0
    for a in activity:
        # Campo confirmado: 'side' (BUY/SELL)
        side = (a.get("side") or a.get("type") or "").upper()
        usdc = float(a.get("usdcSize") or a.get("size") or 0)
        volume += usdc
        if side == "BUY":
            buy_total += usdc
        elif side == "SELL":
            sell_total += usdc
    pnl = sell_total - buy_total
    roi = (pnl / buy_total * 100) if buy_total > 0 else 0.0
    return {"pnl": round(pnl, 2), "volume": round(volume, 2), "roi": round(roi, 2)}

def calc_pnl_from_positions(positions):
    """Calcula PNL direto das posições abertas. Campos confirmados: cashPnl, realizedPnl, currentValue."""
    cash_pnl     = sum(float(p.get("cashPnl")     or 0) for p in positions)
    realized_pnl = sum(float(p.get("realizedPnl") or 0) for p in positions)
    current_val  = sum(float(p.get("currentValue") or 0) for p in positions)
    total_bought = sum(float(p.get("totalBought")  or 0) for p in positions)
    total_pnl    = cash_pnl + realized_pnl
    roi = (total_pnl / total_bought * 100) if total_bought > 0 else 0.0
    return {
        "pnl":          round(total_pnl, 2),
        "realized_pnl": round(realized_pnl, 2),
        "unrealized":   round(cash_pnl, 2),
        "current_val":  round(current_val, 2),
        "total_bought": round(total_bought, 2),
        "roi":          round(roi, 2),
    }

# ── State ─────────────────────────────────────────────────────────────────────

def load_state():
    f = DATA_DIR / "positions_state.json"
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}

def save_state(s):
    (DATA_DIR / "positions_state.json").write_text(json.dumps(s, indent=2))

def pos_key(pos):
    return (pos.get("conditionId") or pos.get("market") or
            pos.get("asset") or pos.get("id") or pos.get("tokenId") or "")

# ── Main ───────────────────────────────────────────────────────────────────────

def run_tracker():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts} UTC] 🔍 Polymarket Tracker v4")
    print("=" * 60)

    leaderboard = []
    for fn in [get_wallets_from_trades_api,
               get_wallets_from_markets,
               get_wallets_from_seed]:
        leaderboard = fn()
        if leaderboard:
            print(f"\n  ✅ Strategy succeeded: {fn.__name__}")
            break

    if not leaderboard:
        output = {
            "generated_at": datetime.utcnow().isoformat(),
            "total_wallets": 0,
            "new_alerts_count": 0,
            "error": "All strategies failed",
            "wallets": [],
        }
        (DATA_DIR / "wallets.json").write_text(json.dumps(output, indent=2))
        return output

    print(f"\n🔄 Enriching up to {min(len(leaderboard), 15)} wallets...")

    prev_state    = load_state()
    tracked       = []
    new_alerts    = []
    current_state = {}

    for i, entry in enumerate(leaderboard[:15]):
        address = entry.get("address", "")
        if not address or len(address) < 10:
            continue

        print(f"\n  [{i+1}] {address[:14]}...")

        positions = get_wallet_positions(address)
        activity  = get_wallet_activity(address, limit=100)

        pnl_data  = {"pnl": 0.0, "volume": 0.0, "roi": 0.0}  # profile=404

        # Detect new positions vs previous run
        prev_keys = set(prev_state.get(address, {}).keys())
        n_new = 0
        for pos in positions:
            k = pos_key(pos)
            if k and k not in prev_keys:
                new_alerts.append({
                    "wallet":     address,
                    "position":   pos,
                    "timestamp":  datetime.utcnow().isoformat(),
                    "alert_type": "NEW_POSITION",
                })
                n_new += 1

        if n_new:
            print(f"    🚨 {n_new} new positions!")

        current_state[address] = {pos_key(p): p for p in positions if pos_key(p)}

        # PNL das posições (mais preciso) + volume da atividade
        pos_data = calc_pnl_from_positions(positions)
        act_data = calc_pnl_from_activity(activity)

        final_pnl    = pos_data["pnl"]    or act_data["pnl"]
        final_roi    = pos_data["roi"]    or act_data["roi"]
        final_volume = act_data["volume"] or pos_data["total_bought"]

        print(f"    PnL=${final_pnl:.0f} | Vol=${final_volume:.0f} | ROI={final_roi:.1f}%")

        tracked.append({
            "address":         address,
            "positions":       positions,
            "recent_activity": activity[:8],
            "new_positions":   [],
            "last_updated":    datetime.utcnow().isoformat(),
            "metrics": {
                "pnl":             final_pnl,
                "volume":          final_volume,
                "roi":             final_roi,
                "realized_pnl":    pos_data["realized_pnl"],
                "unrealized_pnl":  pos_data["unrealized"],
                "positions_count": len(positions),
                "activity_count":  len(activity),
                "rank":            entry.get("rank", i + 1),
            },
        })

    save_state(current_state)

    alerts_file = DATA_DIR / "alerts.json"
    try:
        old = json.loads(alerts_file.read_text()) if alerts_file.exists() else []
    except Exception:
        old = []
    alerts_file.write_text(json.dumps((new_alerts + old)[:300], indent=2))

    output = {
        "generated_at":     datetime.utcnow().isoformat(),
        "total_wallets":    len(tracked),
        "new_alerts_count": len(new_alerts),
        "wallets":          tracked,
    }
    (DATA_DIR / "wallets.json").write_text(json.dumps(output, indent=2))

    print(f"\n{'='*60}")
    print(f"✅ {len(tracked)} wallets | {len(new_alerts)} alerts")
    print(f"{'='*60}\n")
    return output


if __name__ == "__main__":
    run_tracker()
