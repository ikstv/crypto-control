"""Attribute a change in net liquidation value using two observed snapshots."""

from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal(0)
CENT = Decimal("0.01")


def snapshot(journal, value, observed_at):
    fx = value["fx"]
    bid = fx["bid"] if fx else ZERO
    mid = (bid + fx["ask"]) / 2 if fx else ZERO
    positions = value["positions"]
    assets = sum((p["available_quantity"] * (p["bid"] + p["ask"]) / 2
                  for p in positions), ZERO)
    bid_assets = value["assets_usdt"]
    proceeds = sum((p["gross_usdt"] for p in positions), ZERO)
    fees = sum((p["fee_usdt"] * bid + p["fx_fee_uah"] for p in positions), ZERO)
    return dict(at=observed_at, revision=journal["revision"], net=value["net_uah"],
                assets=assets, mid=mid, spread=bid_assets * bid - assets * mid,
                liquidity=(proceeds - bid_assets) * bid, fees=-fees)


def explain(before, after):
    total = after["net"] - before["net"]
    result = dict(from_time=before["at"], to_time=after["at"], total_uah=total)
    if before["revision"] != after["revision"]:
        return dict(result, operations_between=True, effects=None)

    def cents(value):
        return value.quantize(CENT, rounding=ROUND_HALF_UP)

    effects = {
        "crypto": cents((after["assets"] - before["assets"]) * before["mid"]),
        "fx": cents(after["assets"] * (after["mid"] - before["mid"])),
        **{key: cents(after[key] - before[key]) for key in ("spread", "liquidity", "fees")},
    }
    effects["rounding"] = total - sum(effects.values(), ZERO)
    return dict(result, operations_between=False, effects=effects)
