# Crypto Control

Crypto Control is a local cryptocurrency research and manual paper-trading
application. The Windows desktop interface uses FastAPI, WinForms, and WebView2;
the engine can run independently. The repository also contains a read-only
WhiteBIT account-status client, a mobile status panel, and forward-only Shadow
observation tooling. It does not place real exchange trades.

## Current state

| Area | Status |
| --- | --- |
| Local app | Public WhiteBIT spot quotes, manual simulated portfolios, SQLite paper ledger, valuation history, JSON API, Windows engine/Desktop |
| WhiteBIT account status | Read-only v4 access limited to balance, order history, and executed history; no order, transfer, or withdrawal methods |
| Mobile | Authenticated status panel served on Ubuntu loopback; user reported successful iPhone LTE access over Tailscale HTTPS on 2026-11-20 |
| Shadow | Five-minute BTC/USDT midpoint observations; currently `ABSTAIN`; quote age is recorded, bid/ask/spread are not |
| Paper Trading | Off for the Shadow observation service; manual local simulation exists separately |
| Strategy research | SMA 10/30, fixed pullback, and monthly trend/volatility candidates have been rejected against risk/validation criteria; no demonstrated profitable strategy |

New local simulation databases default to UAH 10,000. This is a code default,
not a real balance or user-configurable setting. Existing databases preserve
their state. WhiteBIT Demo Grid Stock/Money and bot-specific PnL are not
available through the current API integration.

## Research decision

Shadow remains `ABSTAIN`. A candidate must achieve net return above 0% cash
after costs, maximum drawdown no greater than 10%, and confirmation on genuinely
independent or prospective forward data. Previously studied 2020–2026 windows
are not independent. Results and negative findings are in the
[experiment registry](docs/strategy-experiments.md) and
[2026-11-20 project log](docs/project-log-2026-11-20.md). These historical
results do not establish future profitability.

## Run locally on Windows

Install Python 3.12, then from the repository root:

```powershell
.\start.cmd
```

The script prepares a local virtual environment and starts the app at
`http://127.0.0.1:8000/`. Internet access is needed for dependency installation
and public market data. Ctrl+C stops the foreground server. For the standalone
window and optional engine startup, see the [Windows Desktop guide](desktop/README.md).

## Documentation

- [Architecture and API](docs/architecture.md)
- [Windows Desktop and engine](desktop/README.md)
- [Security, backups, and repository hygiene](docs/security.md)
- [WhiteBIT read-only API boundary](docs/whitebit-demo-api.md)
- [Mobile panel](docs/crypto-control-mobile.md)
- [Shadow observation procedure](docs/forward-shadow.md)
- [Existing SMA research](docs/research.md)
- [Strategy registry and research SOP](docs/strategy-experiments.md)
- [Project log — 2026-11-20](docs/project-log-2026-11-20.md)

## Research workflow

Start from GitHub `main`, reconcile local state, and inspect the registry.
Register and freeze a distinct hypothesis before data collection or testing;
run it on the registered split; log provenance, costs, metrics, and the
decision; then commit and push safe evidence. Do not repeat failed or already
studied strategies under a new name. Never commit credentials, private account
data, server details, or local SQLite journals.
