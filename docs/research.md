# Strategy research

Shadow Trading evaluates the last completed BTC/USDT daily candle against SMA 10/30 using public [WhiteBIT candle data](https://whitebit.com/trading-data/history/kline). It checks approximately every five minutes while active, records at most one signal per candle and configuration, and logs unavailable or incomplete inputs separately. Each decision includes the candle timestamp, close, indicators, and reason.

## Fixed SMA 10/30 evaluation

```powershell
.\.venv\Scripts\python.exe .\evaluate_sma.py --end 2026-11-19
```

The study validated 2,148 completed UTC candles. Development used 2021–2025. Holdout used January 1 through November 18, 2026. Each segment starts with 1,000 USDT and an empty position. Signals use the shared crossover formula and are modeled at the next open. Predeclared closing liquidation applies at the end of each segment. Drawdown is calculated from daily valuations.

| Strategy | Development return | Development drawdown | Holdout return | Holdout drawdown |
| --- | ---: | ---: | ---: | ---: |
| SMA 10/30 | +27.22% | 64.64% | −0.09% | 14.47% |
| Buy & Hold | +201.81% | 76.63% | −28.34% | 39.52% |
| No Trade | 0.00% | 0.00% | 0.00% | 0.00% |

The baseline applies WhiteBIT's published standard [spot fee](https://whitebit.com/trading-data/trading-fees) of 0.10% per side. A 10 bp full spread and 5 bp slippage per side are modeled assumptions. Doubling the fee and raising slippage to 25 bp reduced SMA holdout return to −3.04%. The SMA holdout had five completed trades. This sample and cost sensitivity support continued observation only.
