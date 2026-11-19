"""Isolated BTC spot simulator. Each entry lot reuses a single-start Ledger."""
import argparse
import json
import sqlite3
import time
from contextlib import closing, contextmanager
from decimal import Decimal as D
from pathlib import Path
import msvcrt

from algorithm import Algorithm
from backtest import DAY, fetch_candles
from ledger import Ledger, MarketUnavailable, OperationConflict
from portfolio import create_portfolio, initialize, migrate_ledger_schema, replace_allocations
from shadow import daily_record

ROOT = Path(__file__).resolve().parent / "research" / "experimental-paper"
CAPITAL = D(10000)
LIMIT = D(1000)


@contextmanager
def exclusive(path):
    with path.with_suffix(".lock").open("a+b") as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def schema_ready(path):
    with closing(sqlite3.connect(path)) as db:
        return any(row[1] == "portfolio_id" for row in db.execute("PRAGMA table_info(settings)"))


def setup(path):
    if not path.exists() or not schema_ready(path):
        initialize(path)
        Ledger(path).initialize()
        migrate_ledger_schema(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS signals(slot INTEGER PRIMARY KEY, decision TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS marks(equity TEXT NOT NULL)")
        for table in ("paper_operations", "paper_sales"):
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS guard_{table} BEFORE INSERT ON {table}
                WHEN COALESCE((SELECT mode FROM algorithm_log ORDER BY id DESC LIMIT 1),'stopped')!='active'
                BEGIN SELECT RAISE(ABORT,'experiment stopped'); END""")


def state(path, fetch):
    cash = CAPITAL
    realized = D(0)
    unrealized = D(0)
    active = None
    trades = []
    holdings = []
    with closing(sqlite3.connect(path)) as db:
        ids = db.execute("SELECT id FROM portfolios WHERE id>1 ORDER BY id").fetchall()
    for (pid,) in ids:
        ledger = Ledger(path, fetch, portfolio_id=pid)
        journal = ledger.history()
        realized += D(journal["realized_pnl_uah"])
        cash += D(journal["cash_uah"]) - LIMIT
        trades.extend(journal["operations"])
        if journal["holdings"]:
            quantity = D(journal["holdings"][0]["quantity"])
            if not any(op.get("side") == "sell" for op in journal["operations"]):
                active = pid
            else:
                market = ledger.markets()["BTC_USDT"]
                step_size = max(D(1).scaleb(-int(market["stockPrec"])), D(market.get("stepSize") or "0"))
                if quantity >= max(step_size, D(market["minAmount"])):
                    active = pid
            holdings.extend(journal["holdings"])
            mark = ledger.state()["valuation"]
            unrealized = unrealized + D(mark["unrealized_pnl_uah"]) if mark and unrealized is not None else None
    equity = None if unrealized is None else CAPITAL + realized + unrealized
    with closing(sqlite3.connect(path)) as db:
        values = [D(row[0]) for row in db.execute("SELECT equity FROM marks ORDER BY rowid")]
    peak, dd = CAPITAL, D(0)
    for value in values + ([] if equity is None else [equity]):
        peak = max(peak,value)
        dd = max(dd,100*(peak-value)/peak)
    return dict(cash=cash, realized=realized, unrealized=unrealized, equity=equity,
                drawdown_percent=dd, active=active, holdings=holdings, trades=trades)


def step(path, slot, decision, fetch):
    algo = Algorithm(path)
    if algo.status() != "active":
        return
    summary = state(path,fetch)
    if summary["equity"] is None:
        return
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT INTO marks VALUES(?)",(str(summary["equity"]),))
        done = db.execute("SELECT 1 FROM signals WHERE slot=?",(slot,)).fetchone()
    if summary["drawdown_percent"] >= 3:
        algo.change("emergency")
        return
    if done:
        return
    pid = summary["active"]
    budget = min(summary["cash"], LIMIT - sum(D(h["basis_uah"]) for h in summary["holdings"]))
    if decision == "BUY" and pid is None and budget > 0:
        lot = create_portfolio(path,f"BTC {slot}","UAH",LIMIT,str(slot),migrate=False)["id"]
        ledger = Ledger(path,fetch,portfolio_id=lot)
        if not ledger.history()["started"]:
            replace_allocations(path,lot,"UAH",[("BTC",budget)])
            ledger.start()
    if decision == "SELL" and pid is not None:
        ledger = Ledger(path,fetch,portfolio_id=pid)
        quote = ledger.preview("BTC")
        ledger.sell("BTC",f"sell-{slot}",quote["fingerprint"])
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("INSERT OR IGNORE INTO signals VALUES(?,?)",(slot,decision))


def main():
    from quotes import fetch_public
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["run","technical","status","emergency","reset"])
    args = parser.parse_args()
    ROOT.mkdir(parents=True,exist_ok=True)
    path = ROOT / ("technical.sqlite3" if args.action=="technical" else "experiment.sqlite3")
    algorithm = Algorithm(path)
    if args.action == "emergency":
        print(algorithm.change("emergency"))
        return
    if args.action == "status" and schema_ready(path):
        print(json.dumps(dict(mode=algorithm.status(), **state(path,fetch_public)),default=str))
        return
    with exclusive(path):
        setup(path)
        if args.action=="reset":
            print(algorithm.change("reset",confirmed=True))
            return
        if args.action=="status":
            print(json.dumps(state(path,fetch_public),default=str))
            return
        algorithm.change("activate")
        if args.action=="technical":
            for slot,decision in enumerate(("BUY","SELL")):
                step(path,slot,decision,fetch_public)
            print(json.dumps(state(path,fetch_public),default=str))
            return
        while algorithm.status()=="active":
            end=int(time.time())//DAY*DAY
            kind,decision,*_ = daily_record(end,fetch_candles)
            if kind=="signal":
                try:
                    step(path,end,decision,fetch_public)
                except (MarketUnavailable,OperationConflict) as exc:
                    print(exc,flush=True)
            time.sleep(300)


if __name__=="__main__":
    main()
