"""Frozen monthly BTC trend hypothesis, offline WhiteBIT daily bars only."""
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backtest import DAY, validate


def timestamp(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


def simulate(bars, start, end, fee, impact, hold=False):
    cash, btc, peak, dd = 1000., 0., 1000., 0.
    operations, paid, leg_basis = 0, 0., None
    legs = []
    closes = []
    buy = (1 + impact) / (1 - fee)
    sell = (1 - impact) * (1 - fee)
    for b in bars:
        if b.timestamp >= end:
            break
        p, close = float(b.open), float(b.close)
        if b.timestamp < start:
            closes.append(close)
            continue
        equity = cash + btc*p
        target = None
        if hold and b.timestamp == start:
            target = 1.
        elif not hold and len(closes) >= 200 and datetime.fromtimestamp(b.timestamp, timezone.utc).day == 1:
            returns = [math.log(x/y) for x,y in zip(closes[-30:],closes[-31:-1])]
            vol = statistics.stdev(returns)*math.sqrt(365)
            target = min(.25, .08/vol) if 0 < vol <= .80 and closes[-1] > statistics.mean(closes[-200:]) else 0.
        if target is not None and (abs(target-btc*p/equity) >= .05 or (target == 0 and btc > 0)):
            delta = target*equity-btc*p
            if delta > 0:
                if not btc:
                    leg_basis = cash
                amount = min(cash/buy, delta/(1+target*(buy-1)))
                btc += amount/p
                cash -= amount*buy
                paid += amount*(buy-1)
            else:
                amount = -delta/(1-target*(1-sell))
                btc = 0. if target == 0 else btc-amount/p
                cash += amount*sell
                paid += amount*(1-sell)
                if not btc:
                    legs.append(cash-leg_basis)
            operations += 1
        liquidation = cash+btc*close*sell
        peak = max(peak, liquidation)
        dd = max(dd, 1-liquidation/peak)
        closes.append(close)
    if btc:
        legs.append(liquidation-leg_basis)
        operations += 1
        paid += btc*close*(1-sell)
    loss = sum(-g for g in legs if g < 0)
    pf = round(sum(max(g,0) for g in legs)/loss,2) if loss else None
    return round((liquidation/1000-1)*100,2), round(dd*100,2), operations,len(legs),pf,round(paid,2)


raw=json.loads((ROOT/"data/whitebit_btc_usdt_1d.json").read_text())
for path in (ROOT/"research").glob("btc_precontrol_*.json"):
    raw.update({str(r[0]):r for r in json.loads(path.read_text())})
start=min(map(int,raw)); end=timestamp("2026-11-19")
bars=validate([raw[str(t)] for t in range(start,end,DAY)],start,end)
for a,b in [("2020-01-01","2021-01-01"),("2021-01-01","2026-01-01"),("2026-01-01","2026-11-19")]:
    for label,fee,impact,hold in [("base",.001,.001,False),("stress",.002,.003,False),("hold",.001,.001,True)]:
        print(a,b,label,simulate(bars,timestamp(a),timestamp(b),fee,impact,hold))
