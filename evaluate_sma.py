"""Reproduce the fixed-window WhiteBIT SMA 10/30 historical evaluation."""

import argparse
import hashlib
import json

from backtest import Costs, D, DEFAULT_CACHE, SOURCE, load_candles, simulate, utc_day


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--end", required=True, type=utc_day, help="Exclusive UTC date")
    args = parser.parse_args()
    start, split = map(utc_day, ("2021-01-01", "2026-01-01"))
    if args.end <= split:
        parser.error("--end must be after 2026-01-01")
    candles = load_candles(DEFAULT_CACHE, start, args.end)
    print(SOURCE, "BTC_USDT 1d", len(candles),
          hashlib.sha256(repr(candles).encode()).hexdigest())
    scenarios = {
        "base": Costs(D("0.1"), D(10), D(5)),
        "fee_stress": Costs(D("0.2"), D(10), D(5)),
        "slippage_stress": Costs(D("0.1"), D(10), D(25)),
        "combined_stress": Costs(D("0.2"), D(10), D(25)),
    }
    development = [c for c in candles if c.timestamp < split]
    for period, segment, history in (
        ("development", development, ()),
        ("holdout", [c for c in candles if c.timestamp >= split], development[-30:]),
    ):
        for scenario, costs in scenarios.items():
            for strategy in ("ma", "hold", "cash"):
                warmup = history if strategy == "ma" else ()
                result = simulate(segment, D(1000), costs, strategy, warmup=warmup)
                print(json.dumps(dict(period=period, days=len(segment), scenario=scenario,
                                      strategy=strategy, **result), default=str))


if __name__ == "__main__":
    main()
