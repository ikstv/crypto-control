# Project log — 2026-11-20

This records reported observations and research decisions; it does not
authorize exchange orders, server/network changes, or automated execution.

## WhiteBIT read-only account check

- Three allowlisted API v4 read endpoints were used: trade balance, order
  history, and executed history. No order was placed or cancelled.
- The observed account had zero DBTC and zero DUSDT. This is not a profit/loss
  measurement.
- WhiteBIT Demo Grid balance, Stock/Money allocation, and PnL were unavailable
  through these API endpoints. Account-wide trade history cannot establish
  those bot-specific values.
- API access was reported successful, but no sanitized immutable response
  artifact with exact query timestamps is present in the repository. No
  credentials or private account records are committed.

## Mobile and Shadow

- The user confirmed Crypto Control Mobile from iPhone over LTE through
  Tailscale HTTPS and reported Tailscale userspace networking without
  netfilter. Sensitive addresses, device identifiers, and filter details are
  intentionally omitted.
- Shadow records BTC/USDT midpoint and quote age every five minutes. Its record
  does not retain bid, ask, or spread, so it cannot establish execution spread
  or liquidity.
- No candidate is admitted: Shadow remains `ABSTAIN`, and Paper Trading is off.
  The separate Shadow journal does not require access to the Paper Trading DB.
- Phone access and service state are user-reported observations, not an
  independent server audit or packet capture.

## Research decisions

See [the registry](strategy-experiments.md) for candidate rules, metrics,
costs, provenance limitations, and reproduction paths.

- CC-EXP-001, fixed BTC pullback: rejected; drawdown exceeded the 10% limit in
  all reported windows and stress costs erased the best-window return. 2020 is
  now studied, not independent.
- CC-EXP-002, monthly trend/volatility target: rejected; 2021–2025 drawdown was
  13.65% against the 10% limit and 2026 return was −4.99%. The 2020 positive
  result comprised one completed position. All three windows are studied.
- The earlier SMA 10/30 candidate also failed the stated risk and holdout
  criteria; see `docs/research.md`.
- No strategy has demonstrated an admitted edge. Do not repeat studied
  2020–2026 windows as independent. CC-EXP-003 is a prospective design only,
  not a tested or profitable strategy.

## Provenance and canonical log

The replay scripts and three 2020 candle excerpts used by the separate
historical check are committed. The full raw candle caches and SQLite journals
remain local and ignored. Exact raw-response hashes and immutable upstream
snapshots were not recorded; preserve this reproducibility limitation in later
summaries. GitHub `main` is the canonical project journal as of commit
`7b270dd`.
