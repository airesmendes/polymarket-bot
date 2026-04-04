"""
Polymarket Wallet Tracker — v2
Múltiplos endpoints de fallback + debug detalhado.
"""

import requests
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def _get(url, params=None, label=""):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        print(f"  [{r.status_code}] {label or url[:80]}")
        if r.status_code != 200:
            print(f"    body: {r.text[:200]}")
            return None
        return r.json()
    except Exception as e:
        print(f"  [ERR] {label or url[:60]}: {e}")
        return None

# ── Leaderboard with multiple fallbacks ───────────────────────────────────────

def get_leaderboard(limit=25):
    """Tenta múltiplos endpoints e formatos até encontrar dados."""

    strategies = [
        # 1. Data API com window
        ("data-api window=all",
         "https://data-api.polymarket.com/leaderboard",
         {"limit": limit, "window": "all"}),

        # 2. Data API sem window
        ("data-api no window",
         "https://data-api.polymarket.com/leaderboard",
         {"limit": limit}),

        # 3. Data API window=1m
        ("data-api window=1m",
         "https://data-api.polymarket.com/leaderboard",
         {"limit": limit, "window": "1m"}),

        # 4. Gamma API
        ("gamma-api leaderboard",
         "https://gamma-api.polymarket.com/leaderboard",
         {"limit": limit}),

        # 5. Data API profiles sorted by profit
        ("data-api profiles",
         "https://data-api.polymarket.com/profiles",
         {"limit": limit, "sortBy": "profit", "order": "DESC"}),

        # 6. CLOB API
        ("clob leaderboard",
         "https://clob.polymarket.com/leaderboard",
         {"limit": limit}),
    ]

    for label, url, params in strategies:
        data = _get(url, params, label)
        if not data:
            continue

        # Normaliza diferentes formatos de resposta
        wallets = []
        if isinstance(data, list):
            wallets = data
        elif isinstance(data, dict):
            for key in ("data", "leaderboard", "profiles", "results", "traders"):
                if isinstance(data.get(key), list) and data[key]:
                    wallets = data[key]
                    break

        if wallets:
            print(f"  ✅ Got {len(wallets)} wallets from [{label}]")
            print(f"     Sample keys: {list(wallets[0].keys())[:10]}")
            return wallets

        print(f"    empty response from [{label}]")

    print("  [WARN] All leaderboard endpoints returned empty")
    return []

# ── Per-wallet data ────────────────────────────────────────────────────────────

def get_wallet_positions(address):
    for url, params in [
        ("https://data-api.polymarket.com/positions", {"user": address}),
        ("https://data-api.polymarket.com/positions", {"address": address}),
    ]:
        d = _get(url, params, f"positions {address[:10]}")
        if d:
            return d if isinstance(d, list) else (
                d.get("data") or d.get("positions") or [])
    return []

def get_wallet_activity(address, limit=20):
    for url, params in [
        ("https://data-api.polymarket.com/activity",
         {"user": address, "limit": limit}),
        ("https://data-api.polymarket.com/activity",
         {"address": address, "limit": limit}),
    ]:
        d = _get(url, params, f"activity {address[:10]}")
        if d:
            return d if isinstance(d, list) else (
                d.get("data") or d.get("activities") or [])
    return []

# ── State management ───────────────────────────────────────────────────────────

def load_state():
    f = DATA_DIR / "positions_state.json"
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        return {}

def save_state(state):
    (DATA_DIR / "positions_state.json").write_text(json.dumps(state, indent=2))

def pos_key(pos):
    return (pos.get("conditionId") or pos.get("market") or
            pos.get("asset") or pos.get("id") or pos.get("tokenId") or "")

def detect_new_positions(address, current, prev_state):
    prev_keys = set(prev_state.get(address, {}).keys())
    return [p for p in current if pos_key(p) and pos_key(p) not in prev_keys]

# ── Extract metrics safely ─────────────────────────────────────────────────────

def extract_float(obj, *keys):
    for k in keys:
        v = obj.get(k)
        if v is not None:
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return 0.0

def extract_address(entry):
    for k in ("proxy_wallet", "proxyWallet", "address", "wallet",
              "user", "userId", "account"):
        v = entry.get(k)
        if v and isinstance(v, str) and len(v) > 10:
            return v
    return ""

# ── Main ───────────────────────────────────────────────────────────────────────

def run_tracker():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{ts} UTC] 🔍 Polymarket Tracker v2")
    print("=" * 60)

    prev_state   = load_state()
    leaderboard  = get_leaderboard(limit=25)

    if not leaderboard:
        print("\n❌ Could not fetch leaderboard from any endpoint.")
        # Salva estado vazio para não quebrar o dashboard
        output = {
            "generated_at":     datetime.utcnow().isoformat(),
            "total_wallets":    0,
            "new_alerts_count": 0,
            "error":            "leaderboard empty — API may have changed",
            "wallets":          [],
        }
        (DATA_DIR / "wallets.json").write_text(json.dumps(output, indent=2))
        return output

    tracked_wallets = []
    new_alerts      = []
    current_state   = {}

    for i, entry in enumerate(leaderboard[:20]):
        address = extract_address(entry)
        if not address:
            print(f"  [SKIP] entry {i}: no address field")
            continue

        print(f"\n  → [{i+1}] {address[:14]}...")

        positions = get_wallet_positions(address)
        activity  = get_wallet_activity(address, limit=10)

        # Detect new positions
        new_pos = detect_new_positions(address, positions, prev_state)
        for pos in new_pos:
            new_alerts.append({
                "wallet":     address,
                "position":   pos,
                "timestamp":  datetime.utcnow().isoformat(),
                "alert_type": "NEW_POSITION",
                "processed":  False,
            })
            print(f"    🚨 NEW: {pos_key(pos)[:30]}")

        current_state[address] = {
            pos_key(p): p for p in positions if pos_key(p)
        }

        pnl    = extract_float(entry, "profit", "pnl", "totalProfit",
                               "total_profit", "netProfit")
        volume = extract_float(entry, "volume", "totalVolume",
                               "total_volume", "volumeTraded")
        roi    = extract_float(entry, "roi", "return", "returnPct",
                               "return_pct", "profitPct")
        rank   = entry.get("rank") or entry.get("position") or (i + 1)

        tracked_wallets.append({
            "address":         address,
            "positions":       positions,
            "recent_activity": activity[:8],
            "new_positions":   new_pos,
            "last_updated":    datetime.utcnow().isoformat(),
            "metrics": {
                "pnl":             pnl,
                "volume":          volume,
                "roi":             roi,
                "positions_count": len(positions),
                "rank":            rank,
            },
        })

    save_state(current_state)

    # Merge alerts
    alerts_file = DATA_DIR / "alerts.json"
    try:
        old_alerts = json.loads(alerts_file.read_text()) if alerts_file.exists() else []
    except Exception:
        old_alerts = []
    alerts_file.write_text(json.dumps((new_alerts + old_alerts)[:300], indent=2))

    output = {
        "generated_at":     datetime.utcnow().isoformat(),
        "total_wallets":    len(tracked_wallets),
        "new_alerts_count": len(new_alerts),
        "wallets":          tracked_wallets,
    }
    (DATA_DIR / "wallets.json").write_text(json.dumps(output, indent=2))

    print(f"\n{'='*60}")
    print(f"✅ {len(tracked_wallets)} wallets | {len(new_alerts)} new alerts")
    print(f"{'='*60}\n")
    return output


if __name__ == "__main__":
    run_tracker()
