from decimal import Decimal as D
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock

from backtest import Candle, Costs, DAY, load_candles, simulate, validate


def candles(prices):
    return [Candle(i * DAY, D(open_), D(close)) for i, (open_, close) in enumerate(prices)]


class BacktestTests(TestCase):
    def test_equal_averages_do_not_exit_and_final_signal_cannot_fill(self):
        prices = [(100, 100), (100, 90), (90, 110), (200, 110), (50, 90)]
        result = simulate(candles(prices), D(1000), Costs(D(0), D(0), D(0)),
                          "ma", fast=1, slow=2)
        self.assertEqual(result["balance"], D(450))
        result = simulate(candles(prices[:3]), D(1000), Costs(D(0), D(0), D(0)),
                          "ma", fast=1, slow=2)
        self.assertEqual(result["trades"], 0)

    def test_fees_are_charged_on_both_sides(self):
        result = simulate(candles([(100, 100), (100, 100)]), D(1000),
                          Costs(D(1), D(0), D(0)), "hold")
        self.assertEqual(result["balance"], D("980.1000"))
        self.assertEqual(result["trades"], 1)
        self.assertEqual(result["win_percent"], 0)

    def test_tp_signal_waits_for_next_open_even_on_price_gap(self):
        result = simulate(candles([(100, 110), (80, 80)]), D(1000),
                          Costs(D(0), D(0), D(0)), "tp_sl")
        self.assertEqual(result["balance"], D(800))

    def test_ma_crossover_fills_at_next_open(self):
        result = simulate(candles([(100, 100), (100, 90), (90, 110), (200, 120)]),
                          D(1000), Costs(D(0), D(0), D(0)), "ma", fast=1, slow=2)
        self.assertEqual(result["balance"], D(600))

    def test_validation_and_cache(self):
        row = lambda t: [t, "100", "101", "102", "99", "1", "100"]
        for bad in ([row(0), row(0)], [row(0)], [row(0), row(DAY + 1)]):
            with self.assertRaises(ValueError):
                validate(bad, 0, 2 * DAY)
        fetch = Mock(return_value=[row(DAY), row(0)])
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            first = load_candles(path, 0, 2 * DAY, fetch)
            self.assertEqual([c.timestamp for c in first], [0, DAY])
            self.assertEqual(load_candles(path, DAY, 2 * DAY, fetch), first[1:])
        fetch.assert_called_once()
