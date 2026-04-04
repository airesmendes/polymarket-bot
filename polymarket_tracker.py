"""
Polymarket Wallet Tracker
Monitora carteiras lucrativas e detecta novas posições.
"""

import requests
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

GAMMA_API  = "https://gamma-api.polymarket.com"
DATA_API   = "https://data-api.polymarket.com"
CLOB_API   = "https://clob.polymarket.com"

HEADERS = {"User-Agent": "PolymarketBot/1.0"}

# ── Helpers ────────────────────────────────────────────────────────────────────

def _get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=12)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [WARN] GET {url} → {e}")
        return None

# ── Polymarket API ─────────────────────────────────────────────────────────────

def get_leaderboard(limit=30):
    """Top traders por lucro total."""
    data = _get(f"{DATA_API}/leaderboard", {"limit": limit, "window": "all"})
    if isinstance(data, dict):
        return data.get("data") or data.get("leaderboard") or []
    return data or []

def get_wallet_profile(address):
    data = _get(f"{DATA_API}/profiles", {"address": address})
    if isinstance(data, list) and data:
        return data[0]
    return data or {}

def get_wallet_positions(address):
    data = _get(f"{DATA_API}/positions", {"user": address, "sizeThreshold": "0.01"})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data") or data.get("positions") or []
    return []

def get_wallet_activity(address, limit=30):
    data = _get(f"{DATA_API}/activity", {"user": address, "limit": limit})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data") or data.get("activities") or []
    return []

# ── State management ───────────────────────────────────────────────────────────

def load_state():
    f = DATA_DIR / "positions_state.json"
    return json.loads(f.read_text()) if f.exists() else {}

def save_state(state):
    (DATA_DIR / "positions_state.json").write_text(json.dumps(state, indent=2))

def pos_key(pos):
    return pos.get("conditionId") or pos.get("market") or pos.get("asset") or pos.get("id") or ""

def detect_new_positions(address, current, prev_state):
    prev_keys = set(prev_state.get(address, {}).keys())
    return [p for p in current if pos_key(p) and pos_key(p) not in prev_keys]

# ── Main ───────────────────────────────────────────────────────────────────────

def run_tracker():
    print(f"\n[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] 🔍 Starting tracker...")

    prev_state = load_state()
    leaderboard = get_leaderboard(limit=30)

    if not leaderboard:
        print("  [ERROR] Empty leaderboard. Check Polymarket API.")
        return

    tracked_wallets = []
    new_alerts      = []
    current_state   = {}

    for entry in leaderboard[:20]:
        address = (entry.get("proxy_wallet") or entry.get("address")
                   or entry.get("proxyWallet") or entry.get("wallet") or "")
        if not address:
            continue

        print(f"  → {address[:12]}...")

        profile   = get_wallet_profile(address)
        positions = get_wallet_positions(address)
        activity  = get_wallet_activity(address, limit=15)

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
            print(f"    🚨 NEW POSITION: {pos_key(pos)[:30]}")

        # Build position map for state
        current_state[address] = {pos_key(p): p for p in positions if pos_key(p)}

        # Aggregate metrics from multiple possible field names
        pnl    = float(entry.get("profit") or entry.get("pnl") or profile.get("profit") or profile.get("pnl") or 0)
        volume = float(entry.get("volume") or profile.get("volume") or 0)
        roi    = float(entry.get("roi") or profile.get("roi") or 0)

        tracked_wallets.append({
            "address":         address,
            "profile":         profile,
            "positions":       positions,
            "recent_activity": activity[:8],
            "new_positions":   new_pos,
            "last_updated":    datetime.utcnow().isoformat(),
            "metrics": {
                "pnl":              pnl,
                "volume":           volume,
                "roi":              roi,
                "positions_count":  len(positions),
                "rank":             entry.get("rank") or (len(tracked_wallets) + 1),
            },
        })

    save_state(current_state)

    # Merge alerts (newest first, cap 300)
    alerts_file = DATA_DIR / "alerts.json"
    old_alerts  = json.loads(alerts_file.read_text()) if alerts_file.exists() else []
    all_alerts  = (new_alerts + old_alerts)[:300]
    alerts_file.write_text(json.dumps(all_alerts, indent=2))

    # Save wallets snapshot
    output = {
        "generated_at":      datetime.utcnow().isoformat(),
        "total_wallets":     len(tracked_wallets),
        "new_alerts_count":  len(new_alerts),
        "wallets":           tracked_wallets,
    }
    (DATA_DIR / "wallets.json").write_text(json.dumps(output, indent=2))

    print(f"\n✅ Done — {len(tracked_wallets)} wallets | {len(new_alerts)} new alerts\n")
    return output


if __name__ == "__main__":
    run_tracker()
