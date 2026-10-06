"""Stand-ins for secmaster-svc and quote-svc (app/upstream.py)."""

from decimal import Decimal

from app.upstream import Bar, Bars, Identifier, Instrument, Latest, Note, NotFound, Point, Series

TWO = Instrument(2, "UST-2Y-CMT", "P2Y", "US Treasury 2-year constant maturity yield", type="cmt_yield",
                 currency="USD", country="US", curve="UST", calendar="SIFMA-US",
                 identifiers=(Identifier("UST-PAR", "BC_2YEAR"), Identifier("H15-TCM", "RIFLGFCY02_N.B")),
                 notes=(Note("h15-first", "1976-06-01", "H.15 starts."), Note("par-curve-method-2021", "2021-12-06",
                                                                               "Method changed.")))
TEN = Instrument(10, "UST-10Y-CMT", "P10Y", "US Treasury 10-year constant maturity yield", type="cmt_yield",
                 currency="USD", country="US", curve="UST", calendar="SIFMA-US",
                 identifiers=(Identifier("UST-PAR", "BC_10YEAR"), Identifier("H15-TCM", "RIFLGFCY10_N.B")),
                 notes=(Note("par-curve-method-2021", "2021-12-06", "Method changed."),))
SIX_WEEK = Instrument(1, "UST-1.5M-CMT", "P6W", "US Treasury 1.5-month constant maturity yield",
                      aliases=("UST-6W-CMT",))
ALL = [SIX_WEEK, TWO, TEN]

# sec_id -> date -> (value, source)
QUOTES = {
    2: {"2026-09-30": ("0.0361", "UST-PAR"), "2026-10-01": ("0.036", "UST-PAR"), "2026-10-02": ("0.0358", "UST-PAR")},
    10: {"2026-09-30": ("0.0415", "UST-PAR"), "2026-10-01": ("0.0412", "UST-PAR"),
         "2026-10-02": ("0.041", "H15-TCM")},
    1: {"2026-10-02": ("0.04", "UST-PAR")},
}


class FakeSecurities:
    def __init__(self):
        self.calls = []

    def list_instruments(self):
        self.calls.append("list")
        return [Instrument(i.sec_id, i.short_name, i.tenor, i.description, aliases=i.aliases) for i in ALL]

    def get_instrument(self, name):
        self.calls.append(("get", name))
        for i in ALL:
            if name.upper() in (i.short_name.upper(), *(a.upper() for a in i.aliases)):
                return i
        raise NotFound(f"no instrument {name}")

    def search(self, query, limit):
        return [i for i in ALL if query.upper() in i.short_name.upper()][:limit]


class FakeQuotes:
    def __init__(self):
        self.calls = []

    def series(self, sec_ids, start, end, source=""):
        self.calls.append(("series", tuple(sec_ids), start, end, source))
        return [Series(i, [Point(d, v, s) for d, (v, s) in sorted(QUOTES.get(i, {}).items()) if start <= d <= end])
                for i in sec_ids]

    def bars(self, sec_ids, start, end, interval, source=""):
        """As quote-svc's GetBars does it, using the same period rules as mkt-api's own (spread) bars."""
        from app.api import bars

        self.calls.append(("bars", tuple(sec_ids), start, end, interval, source))
        out = []
        for i in sec_ids:
            daily = [(d, Decimal(v), s) for d, (v, s) in sorted(QUOTES.get(i, {}).items()) if start <= d <= end]
            out.append(Bars(i, [Bar(b["date"], b["last"], str(b["open"]), str(b["high"]), str(b["low"]),
                                    str(b["close"]), b["extra"]) for b in bars(daily, interval)]))
        return out

    def latest(self, sec_ids):
        out = []
        for i in sec_ids:
            days = QUOTES.get(i, {})
            d = max(days, default="")
            out.append(Latest(i, d, *(days[d] if d else ("", ""))))
        return out
