"""
Polymarket Copy Trader
Copia posições de carteiras monitoradas para sua wallet.

Variáveis de ambiente necessárias (GitHub Secrets):
  POLYMARKET_PRIVATE_KEY   — chave privada da sua wallet Polygon
  POLYMARKET_API_KEY       — API key do Polymarket (opcional, derivada automaticamente)
  POLYMARKET_API_SECRET    — (opcional)
  POLYMARKET_API_PASSPHRASE— (opcional)

Variáveis de configuração (GitHub Variables):
  MAX_POSITION_USDC  — teto por operação (default: 10)
  COPY_RATIO         — % do tamanho do líder (default: 0.1 = 10%)
  MIN_POSITION_USDC  — mínimo para executar (default: 1)
  COPY_TRADING_ENABLED — 'true' para ativar execução real
"""

import os
import json
import time
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# Tenta importar SDK oficial. Se não instalado, roda em modo simulação.
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, MarketOrderArgs, OrderType
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False

COPY_ENABLED = os.environ.get("COPY_TRADING_ENABLED", "false").lower() == "true"


class PolymarketCopyTrader:

    def __init__(self):
        self.private_key    = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
        self.api_key        = os.environ.get("POLYMARKET_API_KEY", "")
        self.api_secret     = os.environ.get("POLYMARKET_API_SECRET", "")
        self.api_passphrase = os.environ.get("POLYMARKET_API_PASSPHRASE", "")

        self.max_usdc  = float(os.environ.get("MAX_POSITION_USDC", "10"))
        self.ratio     = float(os.environ.get("COPY_RATIO", "0.1"))
        self.min_usdc  = float(os.environ.get("MIN_POSITION_USDC", "1"))

        self.client = None
        if CLOB_AVAILABLE and self.private_key and COPY_ENABLED:
            self._init_client()

    # ── Init ──────────────────────────────────────────────────────────────────

    def _init_client(self):
        try:
            host     = "https://clob.polymarket.com"
            chain_id = 137  # Polygon mainnet

            if self.api_key:
                creds = ApiCreds(
                    api_key=self.api_key,
                    api_secret=self.api_secret,
                    api_passphrase=self.api_passphrase,
                )
                self.client = ClobClient(host, key=self.private_key,
                                         chain_id=chain_id, creds=creds)
            else:
                self.client = ClobClient(host, key=self.private_key, chain_id=chain_id)
                self.client.set_api_creds(self.client.create_or_derive_api_creds())

            print("✅ ClobClient initialized.")
        except Exception as e:
            print(f"❌ ClobClient error: {e}")
            self.client = None

    # ── Trade execution ───────────────────────────────────────────────────────

    def _token_id(self, pos: dict) -> str:
        return (pos.get("asset") or pos.get("tokenId") or pos.get("token_id")
                or pos.get("conditionId") or pos.get("market") or "")

    def copy_position(self, pos: dict, leader: str) -> dict:
        token_id    = self._token_id(pos)
        leader_size = float(pos.get("size") or pos.get("amount") or pos.get("usdcSize") or 0)
        outcome     = pos.get("outcome") or pos.get("side") or "YES"

        if not token_id:
            return self._log_trade(leader, token_id, 0, "SKIP", "no token_id", pos)

        our_size = min(leader_size * self.ratio, self.max_usdc)

        if our_size < self.min_usdc:
            msg = f"size ${our_size:.2f} < min ${self.min_usdc}"
            print(f"  ⏭️  Skip: {msg}")
            return self._log_trade(leader, token_id, our_size, "SKIP", msg, pos)

        print(f"  💸 Copy: {token_id[:20]}... | ${our_size:.2f} | {outcome}")

        # Simulação quando copy trading desativado
        if not COPY_ENABLED or not self.client:
            print("  [SIMULATION] Trade not executed (COPY_TRADING_ENABLED != true)")
            return self._log_trade(leader, token_id, our_size, "SIMULATED", "dry run", pos)

        try:
            order      = MarketOrderArgs(token_id=token_id, amount=our_size)
            signed     = self.client.create_market_order(order)
            resp       = self.client.post_order(signed, OrderType.FOK)
            print(f"  ✅ Executed!")
            return self._log_trade(leader, token_id, our_size, "EXECUTED", str(resp), pos)
        except Exception as e:
            print(f"  ❌ Failed: {e}")
            return self._log_trade(leader, token_id, our_size, "FAILED", str(e), pos)

    def _log_trade(self, leader, token_id, size, status, note, pos):
        entry = {
            "timestamp":  datetime.utcnow().isoformat(),
            "leader":     leader,
            "token_id":   token_id,
            "size_usdc":  round(size, 4),
            "status":     status,
            "note":       note,
            "position":   pos,
        }
        log_file = DATA_DIR / "copy_trades.json"
        logs     = json.loads(log_file.read_text()) if log_file.exists() else []
        logs.insert(0, entry)
        log_file.write_text(json.dumps(logs[:500], indent=2))
        return entry

    # ── Process alerts ────────────────────────────────────────────────────────

    def process_alerts(self):
        alerts_file = DATA_DIR / "alerts.json"
        if not alerts_file.exists():
            print("No alerts.json found.")
            return

        alerts      = json.loads(alerts_file.read_text())
        proc_file   = DATA_DIR / "processed_alerts.json"
        processed   = set(json.loads(proc_file.read_text()) if proc_file.exists() else [])
        new_proc    = []
        count       = 0

        for alert in alerts:
            aid = f"{alert.get('wallet','?')}_{alert.get('timestamp','?')}"
            if aid in processed:
                continue
            if alert.get("alert_type") == "NEW_POSITION":
                self.copy_position(alert.get("position", {}), alert.get("wallet", ""))
                count += 1
                time.sleep(0.5)
            new_proc.append(aid)
            processed.add(aid)

        proc_file.write_text(json.dumps(list(processed)[-1000:]))
        print(f"\n✅ Processed {count} copy trades ({len(new_proc)} alerts handled).")


if __name__ == "__main__":
    ct = PolymarketCopyTrader()
    ct.process_alerts()
