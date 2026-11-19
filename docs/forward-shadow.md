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
