"""Stand-ins for secmaster-svc and quote-svc (app/upstream.py)."""

from decimal import Decimal

from app.upstream import (
    AuctionRow,
    Bar,
    Bars,
    Identifier,
    Instrument,
    Latest,
    Note,
    NotFound,
    OnTheRun,
    Point,
    Security,
    SecurityList,
    SecuritySummary,
    Series,
)

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
                      aliases=("UST-6W-CMT",), type="cmt_yield")
# A Treasury security: priced, not on the curve.
NOTE = Instrument(500, "UST-4.25-2035-08-15", "", "US Treasury note 4.25% due 2035-08-15", type="ust_note",
                  aliases=("UST-10Y-OTR",), identifiers=(Identifier("CUSIP", "91282CNC1"),))
MATURED = SecuritySummary(501, "UST-B-2026-01-02", "912797AA1", "bill", maturity_date="2026-01-02", status="matured")
NOTE_ROW = SecuritySummary(500, "UST-4.25-2035-08-15", "91282CNC1", "note", term="10-Year", original_term="10-Year",
                           coupon_rate="0.0425", issue_date="2025-08-15", maturity_date="2035-08-15", status="active",
                           on_the_run=("UST-10Y-OTR",))
ALL = [SIX_WEEK, TWO, TEN, NOTE]

# sec_id -> date -> (value, source)
QUOTES = {
    2: {"2026-09-30": ("0.0361", "UST-PAR"), "2026-10-01": ("0.036", "UST-PAR"), "2026-10-02": ("0.0358", "UST-PAR")},
    10: {"2026-09-30": ("0.0415", "UST-PAR"), "2026-10-01": ("0.0412", "UST-PAR"),
         "2026-10-02": ("0.041", "H15-TCM")},
    1: {"2026-10-02": ("0.04", "UST-PAR")},
}
# FedInvest end-of-day prices, per 100: sec_id -> date -> (value, source)
PRICES = {500: {"2026-09-30": ("99.5", "TD-PRICES"), "2026-10-01": ("99.828125", "TD-PRICES")}}


class FakeSecurities:
    def __init__(self):
        self.calls = []

    def list_instruments(self):
        self.calls.append("list")
        return [Instrument(i.sec_id, i.short_name, i.tenor, i.description, aliases=i.aliases, type=i.type) for i in ALL]

    def get_instrument(self, name):
        self.calls.append(("get", name))
        for i in ALL:
            if name.upper() in (i.short_name.upper(), *(a.upper() for a in i.aliases)):
                return i
        raise NotFound(f"no instrument {name}")

    def search(self, query, limit):
        return [i for i in ALL if query.upper() in i.short_name.upper()][:limit]

    def list_securities(self, security_type="", include_inactive=False, maturing_from="", maturing_to="", as_of="",
                        limit=0):
        self.calls.append(("list_securities", security_type, include_inactive, maturing_from, maturing_to, limit))
        rows = [r for r in (MATURED, NOTE_ROW) if include_inactive or r.status == "active"]
        rows = [r for r in rows if not security_type or r.security_type == security_type]
        return SecurityList("2026-10-03", len(rows), rows[:limit or 1000])

    def list_auctions(self, start, end, limit=0):
        self.calls.append(("list_auctions", start, end))
        rows = [
            AuctionRow(500, "UST-4.25-2035-08-15", {"cusip": "91282CNC1", "security_type": "note", "term": "10-Year",
                                                   "reopening": "true", "auction_date": "2026-10-07",
                                                   "issue_date": "2026-10-15", "offering_amount": "42000000000",
                                                   "total_accepted": "", "high_yield": ""}),
            AuctionRow(501, "UST-B-2027-04-08", {"cusip": "912797AB9", "security_type": "bill", "term": "26-Week",
                                                "reopening": "false", "auction_date": "2026-10-05",
                                                "offering_amount": "73000000000", "total_accepted": "73000100000",
                                                "high_discount_rate": "0.03805", "bid_to_cover": "2.87"}),
        ]
        return sorted((r for r in rows if start <= r.fields["auction_date"] <= end), key=lambda r: r.fields["auction_date"])

    def get_security(self, name, as_of=""):
        if name.upper() not in ("UST-4.25-2035-08-15", "UST-10Y-OTR", "91282CNC1"):
            raise NotFound(f"no security {name}")
        return Security(instrument=NOTE, terms={"cusip": "91282CNC1", "coupon_rate": "0.0425"},
                        provenance={"coupon_rate": "published: TD-SECURITIES 91282CNC1/2025-08-15 interestRate"},
                        checks=[], auctions=[{"auction_date": "2025-08-06", "reopening": "false"}],
                        on_the_run=[OnTheRun("UST-10Y-OTR", "2025-08-06", "")], index_ratio={}, strip={})


class FakeQuotes:
    def __init__(self):
        self.calls = []

    def series(self, sec_ids, start, end, source="", field="yield"):
        self.calls.append(("series", tuple(sec_ids), start, end, source) + ((field,) if field != "yield" else ()))
        data = PRICES if field == "price" else QUOTES
        return [Series(i, [Point(d, v, s) for d, (v, s) in sorted(data.get(i, {}).items()) if start <= d <= end])
                for i in sec_ids]

    def bars(self, sec_ids, start, end, interval, source="", field="yield"):
        """As quote-svc's GetBars does it, using the same period rules as mkt-api's own (spread) bars."""
        from app.api import bars

        self.calls.append(("bars", tuple(sec_ids), start, end, interval, source) + ((field,) if field != "yield" else ()))
        data = PRICES if field == "price" else QUOTES
        out = []
        for i in sec_ids:
            daily = [(d, Decimal(v), s) for d, (v, s) in sorted(data.get(i, {}).items()) if start <= d <= end]
            out.append(Bars(i, [Bar(b["date"], b["last"], str(b["open"]), str(b["high"]), str(b["low"]),
                                    str(b["close"]), b["extra"]) for b in bars(daily, interval)]))
        return out

    def latest(self, sec_ids, field="yield"):
        self.calls.append(("latest", tuple(sec_ids), field))
        out = []
        for i in sec_ids:
            days = (PRICES if field == "price" else QUOTES).get(i, {})
            d = max(days, default="")
            out.append(Latest(i, d, *(days[d] if d else ("", ""))))
        return out
