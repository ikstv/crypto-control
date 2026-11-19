# Crypto Control

Crypto Control is a local Windows application for cryptocurrency market analysis, manual paper trading, and strategy research. Its desktop window displays a localhost FastAPI interface through WinForms and WebView2. The engine can run independently of the window.

## Project status

| Status | Capabilities |
| --- | --- |
| Implemented | Public WhiteBIT spot quotes for nine pairs, manual simulated portfolio operations, SQLite transaction history, background valuation observations, and a local JSON API |
| Experimental | BTC/USDT daily SMA 10/30 Shadow Trading, persistent emergency lock, signal history, and historical strategy comparisons with modeled execution costs |
| Planned | Configurable initial capital for new simulations, a packaged Windows installer, and an Android client with authenticated access |

New databases use **UAH 10,000** as the current demonstration portfolio configuration. The amount comes from a code default and has no user-facing configuration. Existing portfolios retain their stored state.

Shadow Trading records BUY, SELL, HOLD, and skipped analyses. Automated paper execution and real trading are not implemented. The historical evaluation did not support enabling automated execution for SMA 10/30. See [Strategy research](docs/research.md).

## Quick start on Windows

Install Python 3.12. From the repository root, run:

```powershell
.\start.cmd
```

This creates the local virtual environment if needed, installs dependencies, and starts the server at [127.0.0.1:8000](http://127.0.0.1:8000/). Internet access is needed for dependency installation and public market data. Ctrl+C stops this foreground server.

For the standalone window or optional background startup at sign-in, follow the [Windows Desktop guide](desktop/README.md).

## Documentation

- [Architecture and API](docs/architecture.md)
- [Windows Desktop, engine startup, and tests](desktop/README.md)
- [Security, backups, and repository hygiene](docs/security.md)
- [Strategy research and historical results](docs/research.md)

## Roadmap

1. Improve desktop packaging and validate engine recovery and valuation history across Windows sessions.
2. Add configurable starting capital for new simulations.
3. Research strategies with out-of-sample evaluation and explicit risk limits before deciding whether to build automated paper execution.
4. Define authentication and secure networking before introducing an Android client.
