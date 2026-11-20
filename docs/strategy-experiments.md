# Strategy experiment registry

This is the canonical registry for hypotheses, frozen rules, data windows,
costs, results, and decisions. Register a unique ID before any test. A date
window already examined in this project is never independent validation.

## Admission criteria

A candidate advances only if net return after declared costs is greater than
0% cash return on genuinely untouched validation data, maximum drawdown is at
most 10%, and genuinely independent or prospective forward evidence confirms
the result. Replaying previously studied dates does not qualify. Failure of
any criterion means reject and keep Shadow at `ABSTAIN`. Historical results are
diagnostics, not evidence of future profitability. Daily-close drawdown and
modeled costs may understate intraday losses and actual execution costs.

## Experiments

| ID | Frozen hypothesis | Data and provenance | Costs/method | Results | Verdict/evidence |
| --- | --- | --- | --- | --- | --- |
| CC-EXP-001 | BTC short pullback in a long trend: prior daily close above its close 90 days earlier and below its close 5 days earlier; buy 25% of initial capital at next UTC open; sell after five complete days; otherwise hold; no averaging. | WhiteBIT BTC/USDT daily candles: 2021–2025 development, 2026-01-01–11-18 previously examined, and separately loaded 2020 calendar year, now studied. The replay reads local cached candles; immutable response hashes/upstream snapshot were not recorded. | 0.1% fee per side, 10 bp full spread, 5 bp slippage per side. Stress: 0.2% fee and 25 bp slippage. Daily-close DD; no historical order book. | 2020 −4.29%, DD 17.21%, PF 0.82, 29 trades (15 wins). 2021–25 +16.04%, DD 12.82%, PF 1.23, 124 trades; stress return −3.68%. 2026 to Nov 18 −10.00%, DD 10.63%, PF 0.14, 14 trades. | **Rejected:** DD exceeded 10% in every window and stress erased the best-window gain. Script: `research/replay_pullback_fixed.py` (being added to Git in this update). |
| CC-EXP-002 | Monthly BTC trend/volatility target: on monthly UTC checks using closed daily bars, require close above 200-day mean and annualized 30-day volatility ≤80%; target `min(25%, 8% / volatility)`. Rebalance at next UTC open only when target differs by ≥5 percentage points; fully exit when filter fails; otherwise hold. | WhiteBIT BTC/USDT daily candles: 2020, 2021–25, and 2026-01-01–11-18. Every window has been examined; none is an untouched holdout. The replay reads local cached candles; immutable raw response hashes were not recorded. | 0.1% fee/side, 10 bp full spread, 5 bp slippage/side; stress 0.2% fee/side and 25 bp slippage/side. Daily-close DD; no historical order book. | 2020 +23.28%, DD 3.07%, one completed position. 2021–25 +11.41%, DD 13.65%, stress +10.20%, 29 operations. 2026 to Nov 18 −4.99%, DD 6.73%, stress −5.13%. | **Rejected:** 2021–25 DD exceeded limit and 2026 return was negative. 2020's positive result was one position. Script: `research/replay_monthly_trend.py` (being added to Git in this update). |
| CC-EXP-003 | Prospective BTC one-day range breakout with volatility-scaled risk and a predeclared time exit. Design only: exact thresholds, sizing, exit, cost model, and sample length must be frozen in a new registry entry before data collection/testing. | Prospective WhiteBIT BTC/USDT observations start only after protocol freeze. No 2020–2026 project window is independent. Record endpoint, retrieval time, interval, coverage/gaps, raw snapshot hash, and immutable evidence. | Freeze published fees, observed bid/ask, conservative slippage, and intraday mark-to-market DD before start. | Not tested. | **Research design only.** No profitability claim, no Shadow admission, no execution. |

## Other examined strategy

BTC daily SMA 10/30: 2021–2025 +27.22%, DD 64.64%; 2026-01-01–11-18 −0.09%, DD 14.47%; stress holdout return −3.04%; five holdout trades. Already documented in `docs/research.md`, so these periods are not independent validation.

## Procedure

1. Start from GitHub `main`, reconcile local commits and changes, and review this registry and existing evidence.
2. Assign a new `CC-EXP-NNN`. Freeze a materially distinct hypothesis, parameters, UTC windows, warm-up, costs, drawdown method, data source, and one-shot criteria. Commit the protocol before testing.
3. Record provenance: endpoint, retrieval time, requested interval, timezone, coverage, gaps, response hash, and immutable snapshot or reproducible retrieval instructions. Mark previously examined dates as known.
4. Run once on the registered split. Log command, code commit, data hash, metrics, limitations, and verdict. Do not tune on validation data; revisions require a new ID and untouched/prospective data.
5. Commit safe scripts, evidence, and results. Never commit credentials, private account records, personal identifiers, server addresses, SQLite journals, or secret-bearing configuration.
6. Check for duplicates before proposing work. A failed candidate is not renamed or silently retried. Shadow remains `ABSTAIN` until all admission criteria pass.
