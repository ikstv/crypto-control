# Forward SMA observation

From the repository root in PowerShell, start manually with
`.\.venv\Scripts\python.exe forward_shadow.py run` and stop with Ctrl+C.
In another shell use `.\.venv\Scripts\python.exe forward_shadow.py report`
for one JSON record per completed eligible UTC candle. Run report after the first
initialization. Restart run in the same checkout to resume. Keep the computer
awake and the runner open for continuous monitoring.

The research/forward-sma-v1.sqlite3 database stores the first run time.
Earlier candles only warm the 10/30 crossover. Polls use the latest completed
day, with no historical backfill. During that day's observation window a valid
signal can supersede a skip. The first accepted WhiteBIT BTC/USDT book midpoint
must have exchange time after signal observation, age at most 60 seconds and
receipt before the next UTC boundary. Report timestamps are Unix seconds UTC.
Missing days and skips make cumulative exposure uncertain, so subsequent
cumulative PnL stays null. Missing marks also produce null for that day.

Replay starts with 1000 USDT. BUY sets full BTC exposure, SELL clears it,
HOLD keeps it. The model uses 0.1% fee per side and 0.1% price impact per
side (half the 10 bp spread plus 5 bp slippage). Daily PnL is the cumulative
change from initial capital at estimated liquidation value at the recorded
midpoint. No exchange orders or Ledger writes. Count valid signals, skips and
missing days separately when assessing 30-day coverage.

On Ubuntu, `crypto-control-shadow.service` uses the same runner's
`observe` action. With no admitted strategy it logs one BTC/USDT book midpoint
or a skip per five-minute UTC slot. ABSTAIN leaves all 1,000 model USDT
in cash. No position changes, fees or slippage accrue. The private SQLite
stores the full journal across restarts. The mobile panel reads only the
atomically published latest 30 entries under /run. Missing slots count gaps
between recorded observations; stale status also covers an ongoing outage.
Use `systemctl status crypto-control-shadow.service` to inspect it and
`sudo systemctl disable --now crypto-control-shadow.service` to stop it.

The current observation record saves the midpoint and quote age, not bid, ask,
or spread. It is monitoring evidence, not a fill or profitability measure.
With no admitted candidate the only decision is `ABSTAIN`. See the
[experiment registry](strategy-experiments.md) before proposing a strategy
test; already studied windows must not be relabeled as independent.
