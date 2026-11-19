# Architecture and local API

WinForms hosts the HTML interface in WebView2. The interface talks to FastAPI on `127.0.0.1:8000`. The Windows engine entry point uses a single-instance guard. Its lifetime is independent of the Desktop window.

- `app.py` defines HTTP routes and lifespan workers. `quotes.py` retrieves public WhiteBIT spot data and marks stale cached quotes.
- `portfolio.py` and `ledger.py` store the simulated portfolio, operations, and risk settings in SQLite. Manual sale previews account for order book depth, fees, quantity precision, and USDT/UAH conversion. Partial fills leave the unsold position in the ledger.
- Background observations run at startup and approximately every five minutes. They store unavailable data as skips. The live “Why did my balance change?” comparison uses in-memory snapshots and starts over on restart.
- `algorithm.py` persists mode transitions and the emergency lock. `shadow.py` writes a separate signal log. `backtest.py` uses historical candles and a file cache independent of the portfolio database.

## API

See [interactive API docs](http://127.0.0.1:8000/docs) or `/openapi.json` while the server is running.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/status` | Engine readiness and quote connection |
| `GET /api/v1/quotes` | Spot quotes and freshness |
| `GET /api/v1/portfolio` | Demo allocation and initial capital |
| `GET /api/v1/paper` | Paper ledger, live valuation, and warnings |
| `GET /api/v1/paper/history`, `/api/v1/paper/observations` | Stored operations and observations |
| `GET /api/v1/paper/sale-preview?symbol=BTC` | Sale estimate for a symbol or `all` |
| `POST /api/v1/paper/start`, `/api/v1/paper/sell` | Manual simulated operations |
| `PUT /api/v1/paper/risk` | Manual purchase pause and alert thresholds |
| `GET /api/v1/algorithm`, `/api/v1/algorithm/history` | Mode and change log |
| `POST /api/v1/algorithm/{action}` | `activate`, `stop`, `emergency`, or `reset` with `confirmed=true` |
| `GET /api/v1/shadow/signals` | Recent signals and skipped analyses |

Mutations require `X-Crypto-Control: paper-start`. Sale confirmation includes the preview fingerprint and an operation ID. Use the same ID when retrying an uncertain response. A changed preview can produce HTTP 409. For the trust boundary, see [Security](security.md).
