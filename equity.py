"""Reconstruct a paper portfolio's risk statistics from its actual UTC equity marks."""

from bisect import bisect_left
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from itertools import accumulate


def utc_time(text):
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


class EquityCurve:
    def __init__(self, journal):
        currency = journal.get("currency", "UAH").lower()
        self.peak = Decimal(journal.get(f"initial_{currency}", journal[f"cash_{currency}"]))
        self.maximum = Decimal(0)
        self.open_at = self.open_equity = self.open_realized = None
        # Older receipts can lack an execution time. Never substitute quote time.
        dated = []
        self.undated_sales = False
        for sale in journal["operations"]:
            if sale.get("side") == "sell":
                stamp = utc_time(sale.get("time"))
                if stamp is None:
                    self.undated_sales = True
                else:
                    pnl = sale["realized_pnl"] if "realized_pnl" in sale else sale["realized_pnl_uah"]
                    dated.append((stamp, Decimal(pnl)))
        dated.sort()
        self.sale_times = [stamp for stamp, _ in dated]
        self.realized = list(accumulate((pnl for _, pnl in dated), initial=Decimal(0)))

    def mark(self, stamp, equity, *, recorded):
        self.peak = max(self.peak, equity)
        drawdown = (self.peak - equity) / self.peak * 100 if self.peak else Decimal(0)
        self.maximum = max(self.maximum, drawdown)
        i = bisect_left(self.sale_times, stamp)
        # A historical mark and a fill at the very same instant have no common sequence.
        realized = None if self.undated_sales or stamp in self.sale_times[i:i + 1] else self.realized[i]
        if self.open_at is None or self.open_at.date() != stamp.date():
            self.open_at = self.open_equity = self.open_realized = None
            if recorded:
                self.open_at, self.open_equity, self.open_realized = stamp, equity, realized
        daily = equity - self.open_equity if self.open_equity is not None else None
        daily_realized = None
        if realized is not None and self.open_realized is not None:
            daily_realized = realized - self.open_realized
        return dict(high_water_mark=self.peak, current_drawdown_percent=drawdown,
                    max_drawdown_percent=self.maximum, daily_open_equity=self.open_equity,
                    daily_open_at=self.open_at.isoformat() if self.open_at else None,
                    daily_pnl=daily, daily_pnl_percent=daily / self.open_equity * 100 if self.open_equity else None,
                    daily_realized_pnl=daily_realized,
                    daily_unrealized_pnl=daily - daily_realized if daily_realized is not None else None)


def equity_history(db, portfolio_id, journal, *, until=None, include_rows=False):
    """Merge observation-only legacy history and equity-only older samples in time order."""
    currency = journal.get("currency", "UAH")
    where = " WHERE portfolio_id=?" if portfolio_id is not None else ""
    args = (portfolio_id,) if where else ()
    rows = db.execute("SELECT observed_at, slot, status, reason, equity_uah FROM paper_observations" +
                      where + " ORDER BY rowid", args)
    events = []
    recorded_marks = set()
    for at, slot, status, reason, raw in rows:
        stamp = utc_time(at)
        amount = None
        if status == "recorded":
            try:
                value = Decimal(raw) if raw is not None else None
            except InvalidOperation:
                value = None
            if stamp is not None and value is not None and value.is_finite() and value >= 0:
                amount = value
                recorded_marks.add((stamp, amount))
            else:
                status, reason = "unavailable", reason or "Invalid historical equity mark"
        events.append((stamp, amount, (at, slot, status, reason)))
    if where:
        where += " AND equity_currency=?"
        args += (currency,)
    for at, raw in db.execute("SELECT observed_at, equity_uah FROM paper_equity" + where + " ORDER BY id", args):
        stamp = utc_time(at)
        amount = Decimal(raw)
        if stamp is not None and amount.is_finite() and amount >= 0 and (stamp, amount) not in recorded_marks:
            events.append((stamp, amount, None))

    curve = EquityCurve(journal)
    history = []
    first = datetime.min.replace(tzinfo=timezone.utc)
    for stamp, equity, observation in sorted(events, key=lambda event: event[0] or first):
        if until is not None and stamp is not None and stamp > until:
            break
        stats = curve.mark(stamp, equity, recorded=True) if equity is not None else {}
        if include_rows and observation is not None:
            at, slot, status, reason = observation
            history.append(dict(slot=slot, observed_at=at, status=status, reason=reason, currency=currency,
                                equity=equity, equity_uah=equity if currency == "UAH" else None,
                                **{k: stats.get(k) for k in ("high_water_mark", "current_drawdown_percent",
                                   "max_drawdown_percent", "daily_open_at", "daily_open_equity", "daily_pnl",
                                   "daily_pnl_percent", "daily_realized_pnl", "daily_unrealized_pnl")}))
    return curve, history
