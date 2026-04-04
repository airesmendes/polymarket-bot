# 🎯 Polymarket Copy Trader Bot

Monitor de carteiras lucrativas no Polymarket com copy trading automático via GitHub Actions.

## 🏗️ Arquitetura

```
GitHub Actions (a cada 15 min)
    │
    ├─ polymarket_tracker.py   → detecta novas posições
    ├─ copy_trader.py          → copia posições (se habilitado)
    │
    └─ Commit data/*.json
           │
           └─ GitHub Pages → dashboard/index.html
```

## ⚡ Setup em 5 passos

### 1. Fork e clone

```bash
git clone https://github.com/SEU_USER/polymarket-bot
cd polymarket-bot
```

### 2. Ativar GitHub Pages

`Settings → Pages → Source: Deploy from branch → Branch: main → Folder: /dashboard`

Acesse: `https://SEU_USER.github.io/polymarket-bot/`

### 3. Criar arquivos de dados iniciais

```bash
mkdir data
echo '[]' > data/alerts.json
echo '[]' > data/copy_trades.json
echo '{"wallets":[]}' > data/wallets.json
echo '{"results":[]}' > data/backtest.json
git add data/ && git commit -m "init data" && git push
```

### 4. Configurar GitHub Secrets

`Settings → Secrets and variables → Actions → New repository secret`

| Secret | Descrição |
|--------|-----------|
| `POLYMARKET_PRIVATE_KEY` | Chave privada da sua wallet Polygon (começa com `0x`) |
| `POLYMARKET_API_KEY` | API key do Polymarket (opcional — é derivada automaticamente) |
| `POLYMARKET_API_SECRET` | Opcional |
| `POLYMARKET_API_PASSPHRASE` | Opcional |

**⚠️ Nunca commite sua private key no código!**

### 5. Configurar Variables

`Settings → Secrets and variables → Actions → Variables`

| Variable | Valor padrão | Descrição |
|----------|-------------|-----------|
| `COPY_TRADING_ENABLED` | `false` | Mudar para `true` para ativar copy trades reais |
| `MAX_POSITION_USDC` | `10` | Teto por operação em USDC |
| `COPY_RATIO` | `0.1` | % do tamanho da posição do líder (0.1 = 10%) |
| `MIN_POSITION_USDC` | `1` | Mínimo para executar |

---

## 📊 Executar backtest

Via GitHub Actions:
1. Vá em `Actions → 📊 Polymarket Backtest`
2. Clique `Run workflow`
3. Defina o período (ex: 90 dias)
4. Opcionalmente, informe wallets específicas

Ou localmente:

```bash
pip install -r requirements.txt

# Backtest das top wallets (carrega de wallets.json)
python backtest.py --days 90

# Backtest de wallets específicas
python backtest.py --days 60 --wallets 0xABC123 0xDEF456
```

---

## 🖥️ Rodar localmente

```bash
pip install -r requirements.txt

# 1. Tracker
python polymarket_tracker.py

# 2. Backtest
python backtest.py

# 3. Servir dashboard local
cd dashboard && python -m http.server 8080
# Acesse: http://localhost:8080
# (O fetch de ../data/ funciona com server local)
```

---

## 🔒 Segurança

- O copy trading fica **desativado por padrão** (`COPY_TRADING_ENABLED=false`)
- Antes de ativar, rode em modo simulação e verifique os logs em `data/copy_trades.json`
- Recomendado: comece com `MAX_POSITION_USDC=5` e `COPY_RATIO=0.05`
- A private key fica apenas nos GitHub Secrets, nunca no código

---

## 📁 Estrutura

```
polymarket-bot/
├── polymarket_tracker.py   # Tracker principal
├── backtest.py             # Análise histórica
├── copy_trader.py          # Execução de copy trades
├── requirements.txt
├── data/
│   ├── wallets.json        # Snapshot das top wallets
│   ├── alerts.json         # Feed de novas posições
│   ├── backtest.json       # Resultados do backtest
│   ├── copy_trades.json    # Log de copy trades
│   └── positions_state.json# Estado interno (diff de posições)
├── dashboard/
│   └── index.html          # Dashboard (GitHub Pages)
└── .github/workflows/
    ├── monitor.yml         # Roda a cada 15 min
    └── backtest.yml        # Trigger manual
```

---

## ⚠️ Disclaimer

Este projeto é para fins educacionais e de pesquisa. Mercados de predição envolvem risco. Copy trading não garante retorno. Use com responsabilidade e só opere com capital que pode perder.
