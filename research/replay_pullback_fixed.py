import json
from datetime import date, datetime, timezone
from pathlib import Path
from decimal import Decimal as D
from backtest import DAY, Costs, validate

def ts(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())

def replay(candles, start, fee, impact, hold=False):
    cash, peak = D(1000), D(1000)
    units = dd = D(0)
    gains, losses = [], []
    trades = 0
    for i, c in enumerate(candles):
        if c.timestamp < start:
            continue
        enter = not units and (hold or (i >= 91 and candles[i-1].close > candles[i-91].close
                                        and candles[i-1].close < candles[i-6].close))
        if units and not hold and i == entry+5:
            proceeds = units*c.open*(1-impact)*(1-fee)
            cash += proceeds
            gains.append(max(D(0), proceeds-cost)); losses.append(max(D(0), cost-proceeds))
            trades += 1
            units = D(0)
        elif enter:
            cost = cash if hold else cash/4
            units = cost*(1-fee)/(c.open*(1+impact))
            cash -= cost
            entry = i
        equity = cash+units*c.close*(1-impact)*(1-fee)
        peak = max(peak,equity)
        dd = max(dd,100*(1-equity/peak))
    if units:
        profit = equity-cash-cost
        gains.append(max(D(0),profit)); losses.append(max(D(0),-profit)); trades+=1
    pf = sum(gains)/sum(losses) if sum(losses) else None
    print(f"return={((equity/1000-1)*100):.2f}% DD={dd:.2f}% trades={trades} PF={round(pf,2) if pf is not None else 'n/a'} wins={sum(g>0 for g in gains)}")

raw=json.loads(Path("data/whitebit_btc_usdt_1d.json").read_text())
early={}
for f in Path("research").glob("btc_precontrol_*.json"):
    early.update({str(r[0]):r for r in json.loads(f.read_text())})
for name,src,start,end in [
    ("known development",raw,"2021-01-01","2026-01-01"),
    ("known control",raw,"2026-01-01","2026-11-19"),
    ("withheld historic",early,"2020-01-01","2021-01-01")]:
    s,e=ts(start),ts(end)
    warm=s
    while warm>s-91*DAY and str(warm-DAY) in src:
        warm-=DAY
    candles=validate([src[str(t)] for t in range(warm,e,DAY)],warm,e)
    print(name,len(candles),(s-warm)//DAY)
    for fee,spread,slip,hold in [(".1",10,5,False),(".2",10,25,False),(".1",10,5,True)]:
        replay(candles,s,*Costs(D(fee),D(spread),D(slip)).rates(),hold=hold)
