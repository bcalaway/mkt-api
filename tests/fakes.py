"""Stand-ins for secmaster-svc, quote-svc, mkt-data and calendar-svc (app/upstream.py)."""

from decimal import Decimal

from app.upstream import (
    AuctionRow,
    Bar,
    Bars,
    CalendarInfo,
    CalendarSource,
    CaptureText,
    Close,
    DayAnswer,
    DayVersion,
    Disagreement,
    Identifier,
    Instrument,
    Latest,
    Note,
    NoText,
    NotFound,
    OnTheRun,
    OutOfRange,
    PeriodYear,
    Point,
    Security,
    SecurityList,
    SecuritySummary,
    Series,
    SourceCheck,
    SourceDetail,
    SourceState,
    YearCoverage,
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
# Fixings (phase 4, step 4): a rate in percent, an FX rate as printed.
SOFR = Instrument(600, "SOFR", "", "Secured Overnight Financing Rate", type="rate_fixing", currency="USD",
                  calendar="SIFMA-US", identifiers=(Identifier("NYFED-SOFR", "SOFR"),))
USDJPY = Instrument(601, "USDJPY-H10", "", "H.10 noon buying rate: yen per dollar", type="fx_fixing", currency="JPY",
                    calendar="FED", identifiers=(Identifier("FRB-H10-RATES", "RXI_N.B.JA"),))
# A futures product (phase 4, step 7), with the CFTC's code for it.
TY = Instrument(700, "TY", "", "10-Year T-Note Futures", type="fut_product", currency="USD",
                identifiers=(Identifier("CME", "ZN"), Identifier("CFTC", "043602")))
ALL = [SIX_WEEK, TWO, TEN, NOTE, SOFR, USDJPY, TY]

# sec_id -> date -> (value, source)
QUOTES = {
    2: {"2026-09-30": ("0.0361", "UST-PAR"), "2026-10-01": ("0.036", "UST-PAR"), "2026-10-02": ("0.0358", "UST-PAR")},
    10: {"2026-09-30": ("0.0415", "UST-PAR"), "2026-10-01": ("0.0412", "UST-PAR"),
         "2026-10-02": ("0.041", "H15-TCM")},
    1: {"2026-10-02": ("0.04", "UST-PAR")},
}
# FedInvest end-of-day prices, per 100: sec_id -> date -> (value, source)
PRICES = {500: {"2026-09-30": ("99.5", "TD-PRICES"), "2026-10-01": ("99.828125", "TD-PRICES")}}
RATES = {600: {"2026-10-01": ("0.0388", "NYFED-SOFR"), "2026-10-02": ("0.039", "NYFED-SOFR")},
         601: {"2026-10-01": ("157.8100", "FRB-H10-RATES"), "2026-10-02": ("158.0200", "FRB-H10-RATES")}}
# The CFTC's TFF report, in contracts: field -> sec_id -> date -> (value, source)
TARGETS = {"target_low": {600: {"2026-10-02": ("0.04", "NYFED-SOFR")}},
           "target_high": {600: {"2026-10-02": ("0.0425", "NYFED-SOFR")}},
           "volume_bn": {600: {"2026-10-02": ("2650", "NYFED-SOFR")}}}
POSITIONS = {"oi": {700: {"2026-09-22": ("5000000", "CFTC-TFF"), "2026-09-29": ("5100000", "CFTC-TFF")}},
             "dealer_long": {700: {"2026-09-29": ("400000", "CFTC-TFF")}}}
BY_FIELD = {"price": PRICES, "rate": RATES, **POSITIONS, **TARGETS}
TY_SUMMARY = {"root": "TY", "cme_code": "ZN", "name": "10-Year T-Note Futures", "kind": "treasury", "currency": "USD",
              "cftc_code": "043602", "front": "TYZ26", "status": "listed"}
DATES = dict.fromkeys(("first_trade_date", "last_trade_date", "first_intention_date", "first_notice_date",
                       "first_delivery_date", "last_delivery_date", "reference_start", "reference_end",
                       "final_settlement_date", "settlement_date"), "")


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

    def search(self, query, limit, offset=0):
        hits = [i for i in ALL if query.upper() in i.short_name.upper()]
        return hits[offset:offset + limit], len(hits)

    def list_securities(self, security_type="", include_inactive=False, maturing_from="", maturing_to="", as_of="",
                        limit=0, offset=0):
        self.calls.append(("list_securities", security_type, include_inactive, maturing_from, maturing_to, limit))
        rows = [r for r in (MATURED, NOTE_ROW) if include_inactive or r.status == "active"]
        rows = [r for r in rows if not security_type or r.security_type == security_type]
        return SecurityList("2026-10-03", len(rows), rows[offset:offset + (limit or 1000)])

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

    def futures_products(self):
        return [TY_SUMMARY]

    def futures_product(self, root, include_expired=False):
        if root.upper() != "TY":
            raise NotFound(f"no futures product {root}")
        contracts = [{"name": "TYZ26", "cme_code": "ZNZ6", "month": "2026-12", "status": "listed", "basket_size": 14,
                      **DATES, "last_trade_date": "2026-12-19"}]
        if include_expired:
            contracts.insert(0, {"name": "TYU26", "cme_code": "ZNU6", "month": "2026-09", "status": "expired",
                                 "basket_size": 15, **DATES})
        return {**TY_SUMMARY, "rules": {"last_trade_date": "last_bd_minus:7"},
                "rule_sources": {"last_trade_date": "CBOT Rulebook 19102.D."}, "basket_rule": "6.5y-10y",
                "basket_source": "CBOT Rulebook 19104.", "generics": [{"generic": "TY1", "contract": "TYZ26"}],
                "contracts": contracts}

    def basket(self, contract):
        if contract.upper() != "TYZ26":
            raise NotFound(f"no futures contract {contract}")
        return {"contract": "TYZ26", "product": "TY", "month": "2026-12", "status": "listed", "rule": "6.5y-10y",
                "deliverables": [{"security": "UST-4.25-2035-08-15", "cusip": "91282CNC1", "coupon_rate": "0.0425",
                                  "maturity_date": "2035-08-15", "issue_date": "2025-08-15",
                                  "conversion_factor": "0.8732", "remaining_months": 104, "valid_from": "2025-08-15"}]}

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
        data = BY_FIELD.get(field, QUOTES)
        return [Series(i, [Point(d, v, s) for d, (v, s) in sorted(data.get(i, {}).items()) if start <= d <= end])
                for i in sec_ids]

    def bars(self, sec_ids, start, end, interval, source="", field="yield"):
        """As quote-svc's GetBars does it, using the same period rules as mkt-api's own (spread) bars."""
        from app.api import bars

        self.calls.append(("bars", tuple(sec_ids), start, end, interval, source) + ((field,) if field != "yield" else ()))
        data = BY_FIELD.get(field, QUOTES)
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
            days = BY_FIELD.get(field, QUOTES).get(i, {})
            d = max(days, default="")
            out.append(Latest(i, d, *(days[d] if d else ("", ""))))
        return out


PRICES_SRC = SourceState("TD-PRICES", "securities", "SIFMA-US", "published", "day", "https://www.treasurydirect.gov/",
                         "FedInvest prices", True, captures=4800, capture_bytes=520_000_000, latest_capture_id=8100,
                         latest_capture_at="2026-10-07T23:16:00+00:00", last_success_at="2026-10-06T23:16:00+00:00",
                         last_check_at="2026-10-07T23:16:00+00:00", last_outcome="error",
                         last_error="HTTP 503 from FedInvest", checks_7d=40, errors_7d=1, periods=4700,
                         first_period="2008-01-02", last_period="2026-10-07", pulls="FedInvest's prices. quote-svc reads them.",
                         dag="mkt_data__treasury_securities_capture", schedule="Weekdays 7:15 p.m. New York",
                         late_after_hours=96)
FED_SRC = SourceState("FED-K8", "calendars", "FED", "published", "", "https://www.federalreserve.gov/", "K.8", True,
                      captures=12, capture_bytes=600_000, latest_capture_id=40, latest_capture_at="2026-10-01T00:00:00+00:00",
                      last_success_at="2026-10-07T00:00:00+00:00", last_check_at="2026-10-07T00:00:00+00:00",
                      last_outcome="unchanged", last_parse_outcome="ok", checks_7d=1, late_after_hours=192, late=True)
NEVER_SRC = SourceState("NYSE-RULES", "calendars", "NYSE", "rules", "", "", "rules", False)


class FakeSources:
    def __init__(self):
        self.calls = []

    def list_sources(self):
        return [FED_SRC, PRICES_SRC, NEVER_SRC]

    def get_source(self, name, checks=0):
        self.calls.append(("get", name, checks))
        if name != "TD-PRICES":
            raise NotFound(name)
        return SourceDetail(PRICES_SRC, [
            SourceCheck(9, "2026-10-07T23:16:00+00:00", "error", 0, "2026-10-07", "HTTP 503 from FedInvest"),
            SourceCheck(8, "2026-10-06T23:16:00+00:00", "new", 8099, "2026-10-06", "", "ok")],
            [PeriodYear("2025", 250, 252, 27_000_000), PeriodYear("2026", 190, 200, 21_000_000)])

    def capture_text(self, capture_id, contains="", context=0, offset=0, limit=200):
        self.calls.append(("text", capture_id, contains, context, offset, limit))
        if capture_id == 1:
            raise NoText("capture 1 is application/pdf; no text view")
        if capture_id != 8099:
            raise NotFound(str(capture_id))
        return CaptureText(8099, "TD-PRICES", "2026-10-06", "2026-10-06T23:16:00+00:00", "visible", 3020,
                           1 if contains else -1, offset, 3020, [(48, "Prices For: October 6, 2026")])



# Two calendars from 2026-10-03 (the tests' today, a Saturday): Columbus Day closes SIFMA-US but not FED... and both
# close on Veterans Day; SIFMA-US closes early the day after Thanksgiving.
CLOSES = {
    "SIFMA-US": [Close("2026-10-12", "closed", "Columbus Day", source="SIFMA-US-HOLIDAYS"),
                 Close("2026-11-11", "closed", "Veterans Day", source="SIFMA-US-HOLIDAYS"),
                 Close("2026-11-27", "early_close", "Day after Thanksgiving", "14:00", source="SIFMA-US-HOLIDAYS")],
    "FED": [Close("2026-11-11", "closed", "Veterans Day"), Close("2027-10-11", "closed", "Columbus Day", projected=True)],
}
COVER = {
    "SIFMA-US": [YearCoverage(2025, "SIFMA-US-ARCHIVE", "published"), YearCoverage(2026, "SIFMA-US", "published"),
                 YearCoverage(2027, "SIFMA-US-RULES", "rules")],
    "FED": [YearCoverage(2026, "FED-K8", "published"), YearCoverage(2027, "FED-RULES", "projected")],
}


class FakeCalendars:
    def __init__(self):
        self.calls = []

    def list_calendars(self):
        return [CalendarInfo("FED", "Federal Reserve holidays", "America/New_York", 2026, 2027),
                CalendarInfo("SIFMA-US", "SIFMA US bond market", "America/New_York", 2025, 2027)]

    def closes(self, calendar, start, end):
        self.calls.append(("closes", calendar, start, end))
        return [c for c in CLOSES[calendar] if start <= c.date <= end]

    def coverage(self, calendar):
        return COVER[calendar]

    def business_day(self, calendar, on):
        if on.startswith("2025") and calendar == "FED":
            raise OutOfRange("FED covers 2026 to 2027")
        c = next((c for c in CLOSES[calendar] if c.date == on), None)
        if c:
            return DayAnswer(calendar, on, c.status == "early_close", c.status, c.holiday, c.close_time, c.projected)
        return DayAnswer(calendar, on, True, "open")

    def sources(self, calendar):
        if calendar not in COVER:
            raise NotFound(calendar)
        return [CalendarSource("SIFMA-US-HOLIDAYS", "published", 1, 2, 2026, 2027),
                CalendarSource("SIFMA-US-PROJECTED", "projected", 2, 73, 2028, 2100)]

    def day_history(self, calendar, on):
        return [DayVersion(on, "early_close", "Good Friday", "12:00", "SIFMA-US-HOLIDAYS", 7, "2026-01-02T00:00:00+00:00",
                           "2026-03-01T00:00:00+00:00"),
                DayVersion(on, "closed", "Good Friday", "", "SIFMA-US-HOLIDAYS", 9, "2026-03-01T00:00:00+00:00")]

    def disagreements(self, calendar):
        return [Disagreement("2026-11-27", "SIFMA-US-HOLIDAYS", "status", "open", "", "early_close 14:00",
                             "Day after Thanksgiving", "SIFMA-US-RULES", False)]
