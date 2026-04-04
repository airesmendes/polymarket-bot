"""
Polymarket Wallet Tracker — v3
Usa The Graph (subgraph) + API alternativas que realmente funcionam.
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
    "Content-Type": "application/json",
}

# ── Known high-volume wallets (seed list para quando API falha) ────────────────
# Estas são wallets publicamente conhecidas como top traders do Polymarket
SEED_WALLETS = [
    "0x0000000000000000000000000000000000000001",  # placeholder - será substituído
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def _get(url, params=None, label="", extra_headers=None):
    h = {**HEADERS, **(extra_headers or {})}
    try:
        r = requests.get(url, params=params, headers=h, timeout=15)
        print(f"  [{r.status_code}] {label or url[:70]}")
        if r.status_code == 200:
            return r.json()
        print(f"    → {r.text[:120]}")
        return None
    except Exception as e:
        print(f"  [ERR] {label}: {e}")
        return None

def _post(url, payload, label=""):
    try:
        r = requests.post(url, json=payload, headers=HEADERS, timeout=20)
        print(f"  [{r.status_code}] {label or url[:70]}")
        if r.status_code == 200:
            return r.json()
        print(f"    → {r.text[:120]}")
        return None
    except Exception as e:
        print(f"  [ERR] {label}: {e}")
        return None

# ── Strategy 1: The Graph subgraph ────────────────────────────────────────────

def get_wallets_from_subgraph(limit=30):
    """
    Usa The Graph para buscar top traders por volume de apostas no Polymarket.
    Endpoint oficial do subgraph do Polymarket na Polygon.
    """
    print("\n  [Subgraph] Querying The Graph...")

    # Query para top traders por collateral amount
    query = """
    {
      fpmmTrades(
        first: %d
        orderBy: collateralAmount
        orderDirection: desc
        where: { type: Buy }
      ) {
        trader { id }
        collateralAmount
        outcomeTokensTraded
        fpmm { id title }
        timestamp
      }
    }
    """ % (limit * 5)

    endpoints = [
        "https://api.thegraph.com/subgraphs/name/polymarket/matic-markets-5",
        "https://api.thegraph.com/subgraphs/name/polymarket/polymarket-matic-markets",
        "https://api.thegraph.com/subgraphs/name/polymarket/matic-markets",
    ]

    for url in endpoints:
        data = _post(url, {"query": query}, f"subgraph {url.split('/')[-1]}")
        if not data:
            continue
        trades = data.get("data", {}).get("fpmmTrades", [])
        if not trades:
            print(f"    empty trades from subgraph")
            continue

        # Agrupa por trader e calcula volume
        traders = {}
        for t in trades:
            addr = t.get("trader", {}).get("id", "")
            if not addr or addr == "0x0000000000000000000000000000000000000000":
                continue
            if addr not in traders:
                traders[addr] = {"address": addr, "volume": 0, "trades": 0, "rank": 0}
            traders[addr]["volume"] += float(t.get("collateralAmount", 0)) / 1e6
            traders[addr]["trades"] += 1

        result = sorted(traders.values(), key=lambda x: x["volume"], reverse=True)[:limit]
        if result:
            print(f"    ✅ {len(result)} traders from subgraph")
            return result

    return []

# ── Strategy 2: Polymarket Activity API ──────────────────────────────────────

def get_wallets_from_activity(limit=20):
    """
    Busca atividade recente via data-api e extrai traders únicos.
    """
    print("\n  [Activity] Querying recent activity...")

    # Tenta buscar atividade geral (sem filtro de usuário)
    endpoints = [
        ("https://data-api.polymarket.com/activity", {"limit": 200}),
        ("https://data-api.polymarket.com/trades",   {"limit": 200}),
        ("https://gamma-api.polymarket.com/trades",  {"limit": 200}),
    ]

    for url, params in endpoints:
        data = _get(url, params, url.split("/")[-1])
        if not data:
            continue

        items = data if isinstance(data, list) else (
            data.get("data") or data.get("trades") or data.get("activities") or [])

        if not items:
            continue

        # Extrai endereços únicos
        seen = {}
        for item in items:
            addr = (item.get("user") or item.get("maker") or
                    item.get("trader") or item.get("address") or "")
            if addr and len(addr) > 10 and addr not in seen:
                seen[addr] = {
                    "address": addr,
                    "volume": float(item.get("usdcSize") or item.get("amount") or 0),
                    "trades": 1,
                    "rank": len(seen) + 1,
                }

        if seen:
            result = list(seen.values())[:limit]
            print(f"    ✅ {len(result)} traders from activity")
            return result

    return []

# ── Strategy 3: CLOB recent orders ───────────────────────────────────────────

def get_wallets_from_clob(limit=20):
    """
    Usa o CLOB API para buscar trades recentes e extrair makers.
    """
    print("\n  [CLOB] Querying recent trades...")

    # Primeiro pega alguns mercados ativos
    markets_data = _get("https://clob.polymarket.com/markets",
                        {"next_cursor": "", "limit": "10"},
                        "clob markets")

    if not markets_data:
        return []

    markets = markets_data if isinstance(markets_data, list) else (
        markets_data.get("data") or markets_data.get("markets") or [])

    traders = {}
    for market in markets[:5]:
        condition_id = (market.get("condition_id") or market.get("conditionId")
                       or market.get("id") or "")
        if not condition_id:
            continue

        trades = _get(f"https://clob.polymarket.com/last-trades-and-bids",
                      {"market": condition_id, "limit": "20"},
                      f"trades {condition_id[:12]}")
        if not trades:
            trades = _get(f"https://clob.polymarket.com/trades",
                          {"market": condition_id, "limit": "20"},
                          f"trades2 {condition_id[:12]}")

        items = trades if isinstance(trades, list) else (
            (trades or {}).get("data") or (trades or {}).get("trades") or [])

        for t in (items or []):
            addr = (t.get("maker_address") or t.get("makerAddress") or
                    t.get("taker_address") or t.get("maker") or "")
            if addr and len(addr) > 10:
                if addr not in traders:
                    traders[addr] = {"address": addr, "volume": 0, "trades": 0, "rank": 0}
                traders[addr]["volume"] += float(t.get("size") or t.get("amount") or 0)
                traders[addr]["trades"] += 1

    result = sorted(traders.values(), key=lambda x: x["volume"], reverse=True)[:limit]
    if result:
        print(f"    ✅ {len(result)} traders from CLOB")
    return result

# ── Strategy 4: Polymarket.com internal API ──────────────────────────────────

def get_wallets_from_website_api(limit=20):
    """
    Tenta endpoints da API interna do site polymarket.com.
    """
    print("\n  [WebAPI] Querying polymarket.com API...")

    endpoints = [
        ("https://polymarket.com/api/users/leaderboard", {"period": "all", "limit": limit}),
        ("https://polymarket.com/api/leaderboard", {"limit": limit}),
        ("https://strapi.polymarket.com/leaderboard-entries",
         {"_sort": "profit:DESC", "_limit": limit}),
    ]

    for url, params in endpoints:
        data = _get(url, params, url.replace("https://", "")[:50],
                    extra_headers={"Referer": "https://polymarket.com/"})
        if not data:
            continue

        items = data if isinstance(data, list) else (
            data.get("data") or data.get("users") or data.get("entries") or [])

        if items:
            wallets = []
            for item in items[:limit]:
                addr = (item.get("proxyWallet") or item.get("proxy_wallet") or
                        item.get("address") or item.get("wallet") or "")
                if addr:
                    wallets.append({
                        "address": addr,
                        "volume": float(item.get("volume") or 0),
                        "pnl":    float(item.get("profit") or item.get("pnl") or 0),
                        "rank":   item.get("rank") or len(wallets) + 1,
                    })
            if wallets:
                print(f"    ✅ {len(wallets)} wallets from website API")
                return wallets

    return []

# ── Per-wallet enrichment ─────────────────────────────────────────────────────

def get_wallet_positions(address):
    for url, params in [
        ("https://data-api.polymarket.com/positions", {"user": address}),
        ("https://data-api.polymarket.com/positions", {"address": address}),
        ("https://gamma-api.polymarket.com/positions", {"user": address}),
    ]:
        d = _get(url, params, f"pos {address[:10]}")
        if d:
            return d if isinstance(d, list) else (
                d.get("data") or d.get("positions") or [])
    return []

def get_wallet_activity(address, limit=15):
    for url, params in [
        ("https://data-api.polymarket.com/activity",
         {"user": address, "limit": limit}),
        ("https://data-api.polymarket.com/activity",
         {"address": address, "limit": limit}),
    ]:
        d = _get(url, params, f"act {address[:10]}")
        if d:
            return d if isinstance(d, list) else (
                d.get("data") or d.get("activities") or [])
    return []

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
    print(f"\n[{ts} UTC] 🔍 Polymarket Tracker v3")
    print("=" * 60)

    # Tenta cada estratégia em ordem
    leaderboard = []
    for strategy_fn in [
        get_wallets_from_website_api,
        get_wallets_from_subgraph,
        get_wallets_from_activity,
        get_wallets_from_clob,
    ]:
        leaderboard = strategy_fn()
        if leaderboard:
            break

    if not leaderboard:
        print("\n❌ All strategies returned empty. Saving error state.")
        output = {
            "generated_at": datetime.utcnow().isoformat(),
            "total_wallets": 0,
            "new_alerts_count": 0,
            "error": "All API strategies failed",
            "wallets": [],
        }
        (DATA_DIR / "wallets.json").write_text(json.dumps(output, indent=2))
        return output

    print(f"\n🔄 Enriching {min(len(leaderboard), 15)} wallets...")

    prev_state     = load_state()
    tracked        = []
    new_alerts     = []
    current_state  = {}

    for i, entry in enumerate(leaderboard[:15]):
        address = entry.get("address", "")
        if not address or len(address) < 10:
            continue

        print(f"\n  [{i+1}/{min(len(leaderboard),15)}] {address[:14]}...")

        positions = get_wallet_positions(address)
        activity  = get_wallet_activity(address)

        # Detect new positions
        prev_keys = set(prev_state.get(address, {}).keys())
        for pos in positions:
            k = pos_key(pos)
            if k and k not in prev_keys:
                new_alerts.append({
                    "wallet":     address,
                    "position":   pos,
                    "timestamp":  datetime.utcnow().isoformat(),
                    "alert_type": "NEW_POSITION",
                })
                print(f"    🚨 NEW: {k[:30]}")

        current_state[address] = {pos_key(p): p for p in positions if pos_key(p)}

        tracked.append({
            "address":         address,
            "positions":       positions,
            "recent_activity": activity[:8],
            "new_positions":   [],
            "last_updated":    datetime.utcnow().isoformat(),
            "metrics": {
                "pnl":             entry.get("pnl", 0),
                "volume":          entry.get("volume", 0),
                "roi":             entry.get("roi", 0),
                "positions_count": len(positions),
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
    print(f"✅ {len(tracked)} wallets | {len(new_alerts)} new alerts")
    print(f"{'='*60}\n")
    return output


if __name__ == "__main__":
    run_tracker()
