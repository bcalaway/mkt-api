"""The JSON API mkt-ui's server calls (mkt-data's docs/phase-2.md, Part B step 9).

Every answer names instruments by short name (`UST-10Y-CMT`), never by
secmaster-svc's internal sec_id. Values stay decimal strings end to end and
are always decimals, as stored below (a rate of 4.10% is "0.041", a spread of
52 bp is "0.0052"; Bill, 2026-10-06). Each comes with a `display` string in
the series' unit (percent "4.10" for a yield, basis points "52" for a
spread), made here with Decimal so the UI shows it as given and never does
arithmetic on what it shows.

The schema (`openapi.json`, committed; tests check it's current) is what
mkt-ui's typed client is generated from: a change here is an API change, so
update mkt-ui in step.
"""

import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel

from app import blocks
from app.upstream import (
    CalendarInfo,
    Calendars,
    Close,
    GrpcCalendars,
    GrpcQuotes,
    GrpcSecurities,
    GrpcSources,
    Instrument,
    NotFound,
    OutOfRange,
    Quotes,
    Securities,
    SecuritySummary,
    SourceDetail,
    Sources,
    SourceState,
    UpstreamError,
)

router = APIRouter(prefix="/api")
NEW_YORK = ZoneInfo("America/New_York")
# How far back a curve date looks for the last business day on or before it.
CURVE_LOOKBACK_DAYS = 10
# The curve is the constant-maturity yields; Treasury securities (ust_note, ...) are priced, not on it.
CURVE_TYPE = "cmt_yield"
INSTRUMENTS_CACHE_SECONDS = 300
OFFSET = re.compile(r"^(\d{1,2})([DWMY])$")


# --- Dependencies (tests override these) ---


def securities() -> Securities:
    return GrpcSecurities()


def quotes() -> Quotes:
    return GrpcQuotes()


def sources() -> Sources:
    return GrpcSources()


def calendars() -> Calendars:
    return GrpcCalendars()


_instruments_cache: dict = {}


def _instruments(sec: Securities) -> list[Instrument]:
    hit = _instruments_cache.get("all")
    if hit and time.monotonic() - hit[0] < INSTRUMENTS_CACHE_SECONDS:
        return hit[1]
    out = sec.list_instruments()
    _instruments_cache["all"] = (time.monotonic(), out)
    return out


def _resolve(sec: Securities, name: str) -> Instrument:
    """A short name or alias (any case) to its instrument; 404 if there's none."""
    wanted = name.strip().upper()
    for i in _instruments(sec):
        if i.short_name.upper() == wanted or wanted in (a.upper() for a in i.aliases):
            return i
    try:
        return sec.get_instrument(name.strip())
    except NotFound:
        raise HTTPException(404, f"no instrument named {name!r}") from None


def _today() -> date:
    return datetime.now(NEW_YORK).date()


# --- Values ---


def bp(value: Decimal | str) -> str:
    """A decimal spread in basis points, exactly: "0.0052" -> "52", "-0.00025" -> "-2.5"."""
    return _s(Decimal(value) * 10000)


def display(value: Decimal | str, unit: str) -> str:
    """A decimal in its unit's display form: percent ("4.10"), basis points ("52") or a price per 100 ("99.875")."""
    if unit == "price":
        return price(str(value))
    return percent(str(value)) if unit == "%" else bp(value)


def price(value: str) -> str:
    """A price per 100 as printed, with at least three places: "100" -> "100.000", "99.828125" stays."""
    p = Decimal(value).normalize()
    if p.as_tuple().exponent > -3:
        p = p.quantize(Decimal("0.001"))
    return format(p, "f")


# Treasury securities (types ust_bill, ust_note, ...) have prices, not yields: FedInvest's end of day.
SECURITY_PREFIX = "ust_"


def field_of(i: Instrument) -> str:
    """The quote field an instrument's chart and latest value use."""
    return "price" if i.type.startswith(SECURITY_PREFIX) else "yield"


def percent(value: str) -> str:
    """A decimal rate as percent, exactly, with at least two places: "0.041" -> "4.10"."""
    p = (Decimal(value) * 100).normalize()
    if p.as_tuple().exponent > -2:
        p = p.quantize(Decimal("0.01"))
    return format(p, "f")


def offset_date(base: date, offset: str) -> date:
    """`1W`, `1M`, `1Y`, `10D` before base (a month back from the 31st lands on the month's last day)."""
    m = OFFSET.match(offset.upper())
    if not m:
        raise HTTPException(422, f"compare offset {offset!r} isn't like 1W, 1M, 3M or 1Y")
    n, unit = int(m.group(1)), m.group(2)
    if unit == "D":
        return base - timedelta(days=n)
    if unit == "W":
        return base - timedelta(weeks=n)
    months = n * (12 if unit == "Y" else 1)
    y, mo = divmod(base.year * 12 + base.month - 1 - months, 12)
    mo += 1
    last = (date(y + mo // 12, mo % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, mo, min(base.day, last))


# --- Bars: a series summed up per week, month, quarter or year ---

Interval = blocks.Interval


def period_start(d: date, interval: Interval) -> date:
    """The first calendar day of d's period: the Monday of its week, the 1st of its month, quarter or year."""
    if interval == "week":
        return d - timedelta(days=d.weekday())
    if interval == "month":
        return d.replace(day=1)
    if interval == "quarter":
        return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    if interval == "year":
        return date(d.year, 1, 1)
    return d


def bars(points: list[tuple[str, Decimal, object]], interval: Interval) -> list[dict]:
    """Open, high, low and close per period, from (date, value, extra) in date order; `extra` is the close's.

    `date` is the period's start (for a day, the day), `last` the close's own date.
    """
    out: list[dict] = []
    for d, v, extra in points:
        start = period_start(date.fromisoformat(d), interval).isoformat()
        if out and out[-1]["date"] == start:
            b = out[-1]
            b["high"], b["low"] = max(b["high"], v), min(b["low"], v)
            b["close"], b["last"], b["extra"] = v, d, extra
        else:
            out.append({"date": start, "open": v, "high": v, "low": v, "close": v, "last": d, "extra": extra})
    return out


def _s(v: Decimal) -> str:
    return format(v.normalize(), "f")


# --- Models (the OpenAPI schema) ---


class InstrumentSummary(BaseModel):
    name: str
    aliases: list[str]
    tenor: str  # ISO 8601: P10Y, P6W
    description: str
    status: str
    type: str = ""  # cmt_yield, ust_bill, ust_note, ust_bond, ust_tips, ust_frn, ust_strip_principal, ...


class IdentifierOut(BaseModel):
    scheme: str  # UST-PAR, H15-TCM, FRED
    value: str
    valid_from: str | None
    valid_to: str | None


class NoteOut(BaseModel):
    key: str
    date: str
    text: str


class LatestOut(BaseModel):
    date: str
    value: str  # the rate as a decimal: "0.041"; a Treasury security's price per 100: "99.828125"
    display: str  # in percent: "4.10"; a price with at least three places: "99.828125", "100.000"
    source: str


class InstrumentDetail(InstrumentSummary):
    type: str
    currency: str
    country: str
    curve: str
    calendar: str
    identifiers: list[IdentifierOut]
    notes: list[NoteOut]
    latest: LatestOut | None


class CurvePointOut(BaseModel):
    name: str
    tenor: str
    value: str  # the rate as a decimal: "0.041"
    display: str  # in percent: "4.10"
    source: str


class CurveOut(BaseModel):
    label: str  # "latest", "1W", "1M", ...
    requested: str  # the date asked for
    date: str | None  # the business day on or before it that has values; None if none within 10 days
    points: list[CurvePointOut]
    missing: list[str]  # instruments with no value that day (not yet published then, or a gap)


class CurveResponse(BaseModel):
    curves: list[CurveOut]


class BarOut(BaseModel):
    date: str  # the period's first calendar day (for a day, the day)
    last_date: str  # the day the close is from
    open: str  # decimals: a yield's rate ("0.041"), a spread's or fly's difference of rates ("0.0052"), a price per 100
    high: str
    low: str
    close: str
    open_display: str  # the same in the series' unit: percent for a yield ("4.10"), bp for a spread or fly ("52")
    high_display: str
    low_display: str
    close_display: str
    source: str  # a yield's close: UST-PAR or H15-TCM; "" for a spread or fly
    inputs: list[str]  # a spread's or fly's instruments' rates (decimals) on last_date, in order; [] for a yield
    inputs_display: list[str]  # the same in percent


class BarSeriesOut(BaseModel):
    key: str  # the series as asked for, with short names: "UST-10Y-CMT", "spread(UST-10Y-CMT,UST-2Y-CMT)"
    label: str  # "UST-10Y-CMT - UST-2Y-CMT"
    unit: Literal["%", "bp", "price"]  # what the *_display fields are in: a Treasury security's price per 100
    inputs: list[str]  # the instruments it's made from, by short name
    bars: list[BarOut]


class BarsResponse(BaseModel):
    interval: Interval
    block: str  # "2026" (day: a year), "2020" (week, month, quarter, year: a decade)
    start: str  # the days the block covers (whole periods)
    end: str
    final: bool  # the block is over and settled: it won't change, and browsers keep it a day
    series: list[BarSeriesOut]


def _summary(i: Instrument) -> InstrumentSummary:
    return InstrumentSummary(name=i.short_name, aliases=list(i.aliases), tenor=i.tenor, description=i.description,
                             status=i.status, type=i.type)


Sec = Annotated[Securities, Depends(securities)]
Quo = Annotated[Quotes, Depends(quotes)]


# --- Routes ---


@router.get("/instruments", operation_id="listInstruments", response_model=list[InstrumentSummary])
def list_instruments(sec: Sec, type_: Annotated[str | None, Query(alias="type", max_length=40)] = None):
    """Every instrument, or those of one type (`cmt_yield` for the curve's tenors); shortest tenor first.

    There are thousands of Treasury securities, so a screen that wants the tenors asks for `type=cmt_yield`.
    """
    return [_summary(i) for i in _instruments(sec) if not type_ or i.type == type_]


@router.get("/instruments/{name}", operation_id="getInstrument", response_model=InstrumentDetail)
def get_instrument(name: str, sec: Sec, quo: Quo):
    """One instrument by short name or alias, with its identifiers, notes and latest golden value."""
    i = _resolve(sec, name)
    if not i.identifiers and not i.notes:
        i = sec.get_instrument(i.short_name)  # the list leaves identifiers and notes out
    fld = field_of(i)
    got = [x for x in quo.latest([i.sec_id], field=fld) if x.value]
    latest = LatestOut(date=got[0].as_of, value=got[0].value,
                       display=price(got[0].value) if fld == "price" else percent(got[0].value),
                       source=got[0].source) if got else None
    return InstrumentDetail(
        **_summary(i).model_dump(), currency=i.currency, country=i.country, curve=i.curve,
        calendar=i.calendar,
        identifiers=[IdentifierOut(scheme=x.scheme, value=x.value, valid_from=x.valid_from or None,
                                   valid_to=x.valid_to or None) for x in i.identifiers],
        notes=[NoteOut(key=n.key, date=n.date, text=n.text) for n in i.notes],
        latest=latest,
    )


@router.get("/search", operation_id="searchInstruments", response_model=list[InstrumentSummary])
def search(sec: Sec, q: Annotated[str, Query(min_length=1, max_length=60)],
           limit: Annotated[int, Query(ge=1, le=100)] = 20):
    """Instruments whose name, alias, identifier or description contains q."""
    return [_summary(i) for i in sec.search(q, limit)]


@router.get("/curve", operation_id="getCurves", response_model=CurveResponse)
def curve(sec: Sec, quo: Quo, date_: Annotated[date | None, Query(alias="date")] = None,
          compare: Annotated[list[str] | None, Query(max_length=6)] = None):
    """The curve on a date (default: the latest), plus the same curve `compare` before it (1W, 1M, 1Y).

    Each uses the last business day on or before its date that has values.
    """
    base = date_ or _today()
    wanted = [("latest" if date_ is None else "date", base)] + [(c.upper(), offset_date(base, c)) for c in compare or []]
    insts = [i for i in _instruments(sec) if i.status == "active" and i.type == CURVE_TYPE]
    curves = []
    for label, day in wanted:
        got = quo.series([i.sec_id for i in insts], (day - timedelta(days=CURVE_LOOKBACK_DAYS)).isoformat(),
                         day.isoformat())
        by_id = {s.sec_id: {p.as_of: p for p in s.points} for s in got}
        found = max((d for pts in by_id.values() for d in pts), default=None)
        points, missing = [], []
        for i in insts:
            p = by_id.get(i.sec_id, {}).get(found) if found else None
            if p is None:
                missing.append(i.short_name)
            else:
                points.append(CurvePointOut(name=i.short_name, tenor=i.tenor, value=p.value,
                                            display=percent(p.value), source=p.source))
        curves.append(CurveOut(label=label, requested=day.isoformat(), date=found, points=points, missing=missing))
    return CurveResponse(curves=curves)


# --- Treasury securities (mkt-data's docs/phase-3.md, step 9) ---

SecurityType = Literal["bill", "note", "bond", "tips", "frn"]


class PriceOut(BaseModel):
    date: str  # the business day FedInvest priced it
    value: str  # end of day, per 100 of face, as printed: "99.828125"
    display: str  # at least three places: "99.828125", "100.000"
    source: str  # TD-PRICES


class SecurityRow(BaseModel):
    name: str  # UST-4.25-2035-08-15
    cusip: str
    type: SecurityType
    cmb: bool  # a cash management bill
    term: str  # the auction program: 10-Year, 26-Week
    original_term: str  # exact: 5-Year 2-Month
    coupon: str  # decimal: "0.0425"; "" for bills and FRNs
    coupon_display: str  # percent: "4.25"; ""
    frn_spread: str  # decimal; FRNs only
    issue_date: str
    maturity_date: str
    status: str  # active, matured, called, withdrawn
    on_the_run: list[str]  # UST-10Y-OTR, UST-10Y-OTR-ISSUED
    price: PriceOut | None  # the latest end-of-day price (outstanding securities)


class SecurityListResponse(BaseModel):
    as_of: str  # the day the on-the-run aliases are for
    total: int  # every security that matches; at most `limit` are listed
    securities: list[SecurityRow]


class OnTheRunOut(BaseModel):
    alias: str
    since: str
    until: str | None


class SecurityDetail(BaseModel):
    name: str
    aliases: list[str]
    description: str
    status: str
    type: str  # ust_note, ust_tips, ust_strip_principal, ...
    identifiers: list[IdentifierOut]  # CUSIP, ISIN, FIGI, COMPOSITE-FIGI, TICKER, OTR
    terms: dict[str, str]  # every stored term, dates ISO, decimals as stored; "" for none
    provenance: dict[str, str]  # term -> "published: TD-SECURITIES <key> <field>" or "derived: <rule>"
    checks: list[str]
    auctions: list[dict[str, str]]  # by issue date: auction_date, issue_date, reopening, offering_amount, high_yield, ...
    on_the_run: list[OnTheRunOut]
    index_ratio: dict[str, str] | None  # a TIPS on as_of: ref_cpi, base_cpi, index_ratio, method
    strip: dict[str, str] | None  # a STRIPS: kind, maturity, underlying
    price: PriceOut | None


def _price(x) -> PriceOut | None:
    return PriceOut(date=x.as_of, value=x.value, display=price(x.value), source=x.source) if x and x.value else None


def _row(x: SecuritySummary, last) -> SecurityRow:
    return SecurityRow(
        name=x.short_name, cusip=x.cusip, type=x.security_type, cmb=x.cmb, term=x.term,
        original_term=x.original_term, coupon=x.coupon_rate,
        coupon_display=percent(x.coupon_rate) if x.coupon_rate else "", frn_spread=x.frn_spread,
        issue_date=x.issue_date, maturity_date=x.maturity_date, status=x.status, on_the_run=list(x.on_the_run),
        price=_price(last),
    )


@router.get("/securities", operation_id="listSecurities", response_model=SecurityListResponse)
def list_securities(sec: Sec, quo: Quo, type_: Annotated[SecurityType | None, Query(alias="type")] = None,
                    include_inactive: bool = False, maturing_from: date | None = None,
                    maturing_to: date | None = None, limit: Annotated[int, Query(ge=1, le=6000)] = 1000):
    """Treasury securities by maturity: outstanding ones, or all since 1980 with `include_inactive`.

    With their CUSIP, coupon, dates, on-the-run aliases (today) and, for outstanding ones, the latest
    FedInvest end-of-day price.
    """
    got = sec.list_securities(type_ or "", include_inactive, maturing_from.isoformat() if maturing_from else "",
                              maturing_to.isoformat() if maturing_to else "", "", limit)
    active = [x.sec_id for x in got.securities if x.status == "active"]
    last = {x.sec_id: x for x in quo.latest(active, field="price")} if active else {}
    return SecurityListResponse(as_of=got.as_of, total=got.total,
                                securities=[_row(x, last.get(x.sec_id)) for x in got.securities])


@router.get("/securities/{name}", operation_id="getSecurity", response_model=SecurityDetail)
def get_security(name: str, sec: Sec, quo: Quo):
    """One Treasury security by short name, alias, CUSIP or on-the-run name: terms with where each came from,
    every auction, identifiers, on-the-run aliases, a TIPS's index ratio today, and the latest price."""
    try:
        d = sec.get_security(name.strip())
    except NotFound:
        raise HTTPException(404, f"no Treasury security named {name!r}") from None
    i = d.instrument
    last = quo.latest([i.sec_id], field="price")
    return SecurityDetail(
        name=i.short_name, aliases=list(i.aliases), description=i.description, status=i.status, type=i.type,
        identifiers=[IdentifierOut(scheme=x.scheme, value=x.value, valid_from=x.valid_from or None,
                                   valid_to=x.valid_to or None) for x in i.identifiers],
        terms=d.terms, provenance=d.provenance, checks=d.checks, auctions=d.auctions,
        on_the_run=[OnTheRunOut(alias=o.alias, since=o.since, until=o.until or None) for o in d.on_the_run],
        index_ratio=d.index_ratio or None, strip=d.strip or None, price=_price(last[0] if last else None),
    )


class AuctionOut(BaseModel):
    security: str  # short name: UST-4.25-2035-08-15
    cusip: str
    type: str  # bill, note, bond, tips, frn
    term: str  # the program auctioned: 10-Year, 13-Week
    reopening: bool
    announcement_date: str
    auction_date: str
    issue_date: str
    offering_amount: str  # dollars
    held: bool  # results are in
    total_accepted: str  # dollars; "" until held
    bid_to_cover: str
    high_yield: str  # decimal (notes, bonds, TIPS); ""
    high_yield_display: str  # percent: "4.125"
    high_discount_rate: str  # decimal (bills); ""
    high_discount_rate_display: str
    high_discount_margin: str  # decimal (FRNs); ""
    price_per_100: str


class AuctionsResponse(BaseModel):
    start: str
    end: str
    auctions: list[AuctionOut]  # by auction date


def _pct_or_empty(v: str) -> str:
    return percent(v) if v else ""


@router.get("/auctions", operation_id="listAuctions", response_model=AuctionsResponse)
def list_auctions(sec: Sec, start: date | None = None, end: date | None = None):
    """The Treasury auction calendar: auctions held or announced from `start` to `end` (default: this week,
    Monday to Friday), announced ones without results yet."""
    today = _today()
    start = start or today - timedelta(days=today.weekday())
    end = end or start + timedelta(days=4)
    if end < start or (end - start).days > 366:
        raise HTTPException(422, "end must be on or after start, at most a year later")
    rows = sec.list_auctions(start.isoformat(), end.isoformat())
    out = []
    for r in rows:
        f = r.fields
        out.append(AuctionOut(
            security=r.short_name, cusip=f.get("cusip", ""), type=f.get("security_type", ""), term=f.get("term", ""),
            reopening=f.get("reopening") == "true", announcement_date=f.get("announcement_date", ""),
            auction_date=f.get("auction_date", ""), issue_date=f.get("issue_date", ""),
            offering_amount=f.get("offering_amount", ""), held=bool(f.get("total_accepted")),
            total_accepted=f.get("total_accepted", ""), bid_to_cover=f.get("bid_to_cover", ""),
            high_yield=f.get("high_yield", ""), high_yield_display=_pct_or_empty(f.get("high_yield", "")),
            high_discount_rate=f.get("high_discount_rate", ""),
            high_discount_rate_display=_pct_or_empty(f.get("high_discount_rate", "")),
            high_discount_margin=f.get("high_discount_margin", ""), price_per_100=f.get("price_per_100", ""),
        ))
    return AuctionsResponse(start=start.isoformat(), end=end.isoformat(), auctions=out)


class EventOut(BaseModel):
    date: str
    key: str  # the note's key in secmaster-svc ("gap-2002-2006")
    title: str  # a short label for a chart marker: "Gap", "Method change"
    text: str  # the note, in full
    series: list[str]  # the instruments it's about, by short name (one note can apply to several)


class EventsResponse(BaseModel):
    events: list[EventOut]  # in date order


# Short marker labels for secmaster-svc's note keys (by prefix); anything else shows its key.
EVENT_TITLES = [
    ("first-published", "First published"),
    ("h15-first", "H.15 starts"),
    ("gap-", "Gap"),
    ("par-curve-method", "Method change"),
    ("composite-before", "20Y reissued"),
]
DETAIL_CACHE_SECONDS = INSTRUMENTS_CACHE_SECONDS
_detail_cache: dict = {}


# --- Sources: mkt-data's sources and how their captures are going (phase 3, step 8) ---


class SourceOut(BaseModel):
    name: str  # FED-K8, UST-PAR, TD-PRICES
    group: str  # calendars | rates | securities
    calendar: str
    kind: str  # published | rules | projected
    period_kind: str  # day | month | year; "" for a one-page source
    url: str
    description: str
    parsed: bool  # false: kept raw, no parser yet
    status: str  # error (the last fetch or parse failed) | never (nothing captured) | raw (no parser) | ok
    captures: int
    capture_bytes: int
    latest_capture_id: int
    latest_capture_at: str
    last_success_at: str
    last_check_at: str
    last_outcome: str  # new | unchanged | error | reparse
    last_parse_outcome: str  # ok | error | ""
    last_error: str
    checks_7d: int
    errors_7d: int
    periods: int
    first_period: str
    last_period: str


class SourcesResponse(BaseModel):
    sources: list[SourceOut]


class SourceCheckOut(BaseModel):
    id: int
    checked_at: str
    outcome: str
    capture_id: int  # 0: none (a fetch error)
    period: str
    detail: str
    parse_outcome: str
    parse_detail: str


class PeriodYearOut(BaseModel):
    year: str
    periods: int
    captures: int
    capture_bytes: int


class SourceDetailOut(BaseModel):
    source: SourceOut
    checks: list[SourceCheckOut]
    years: list[PeriodYearOut]


Src = Annotated[Sources, Depends(sources)]


def source_status(x: SourceState) -> str:
    if x.last_outcome == "error" or x.last_parse_outcome == "error":
        return "error"
    if not x.latest_capture_id:
        return "never"
    return "ok" if x.parsed else "raw"


def _source(x: SourceState) -> SourceOut:
    return SourceOut(**vars(x), status=source_status(x))


@router.get("/sources", operation_id="listSources", response_model=SourcesResponse)
def list_sources(src: Src):
    """Every source mkt-data captures (calendar pages, CMT yields, Treasury securities), with how its captures
    are going: the latest fetch and parse, errors in the last week, what's stored and the periods it covers."""
    return SourcesResponse(sources=[_source(x) for x in src.list_sources()])


@router.get("/sources/{name}", operation_id="getSource", response_model=SourceDetailOut)
def get_source(name: str, src: Src, checks: Annotated[int, Query(ge=1, le=500)] = 50):
    """One source: its status, its newest fetch attempts and reparses, and (fetched by period) its periods by year."""
    try:
        d: SourceDetail = src.get_source(name.strip().upper(), checks)
    except NotFound:
        raise HTTPException(404, f"no source named {name!r}") from None
    return SourceDetailOut(source=_source(d.source), checks=[SourceCheckOut(**vars(c)) for c in d.checks],
                           years=[PeriodYearOut(**vars(y)) for y in d.years])


# --- Calendars: calendar-svc's golden holiday calendars (phase 3, step 8) ---

NEXT_CLOSE_DAYS = 400  # how far ahead the list looks for each calendar's next close and early close


class CloseOut(BaseModel):
    date: str
    status: str  # closed | early_close
    holiday: str
    close_time: str  # HH:MM local to the calendar, early closes only
    projected: bool  # from a year no publisher covers yet: a best guess
    source: str  # the mkt-data source that decided the day


class CalendarSourceOut(BaseModel):
    name: str
    kind: str  # published | rules | projected
    rank: int  # 1 is the highest precedence
    years: int  # years credited to it
    first_year: int
    last_year: int


class CalendarSourcesResponse(BaseModel):
    calendar: str
    sources: list[CalendarSourceOut]


class DayVersionOut(BaseModel):
    status: str
    holiday: str
    close_time: str
    source: str
    capture_id: int  # mkt-data's capture
    valid_from: str
    valid_to: str  # "": current


class DayHistoryResponse(BaseModel):
    calendar: str
    date: str
    versions: list[DayVersionOut]  # oldest first; empty: always open


class DisagreementOut(BaseModel):
    date: str
    source: str  # the source that disagrees
    differs: str  # status | time | name
    source_says: str  # open | closed | early_close 13:00
    source_holiday: str
    calendar_says: str
    calendar_holiday: str
    decided_by: str  # "" when the calendar is open that day
    decided_by_higher: bool  # false: a lower source decided a day a higher one covers


class DisagreementsResponse(BaseModel):
    calendar: str
    days: list[DisagreementOut]


class CoverageOut(BaseModel):
    published: int  # years from a publisher's page
    rules: int  # years from a rules file
    projected: int  # years run forward from the rules
    last_published_year: int  # 0 if none


class CalendarOut(BaseModel):
    name: str
    description: str
    timezone: str
    first_year: int
    last_year: int
    coverage: CoverageOut
    next_close: CloseOut | None
    next_early_close: CloseOut | None


class CalendarsResponse(BaseModel):
    as_of: str  # today in New York: "next" is from here
    calendars: list[CalendarOut]


class CalendarYearOut(BaseModel):
    calendar: str
    timezone: str
    year: int
    source: str  # the mkt-data source that decided the year
    kind: str  # published | rules | projected
    closes: list[CloseOut]


class UpcomingDay(BaseModel):
    date: str
    calendars: dict[str, CloseOut]  # only the calendars that close or close early that day


class UpcomingResponse(BaseModel):
    start: str
    end: str
    calendars: list[str]
    days: list[UpcomingDay]


class DayOut(BaseModel):
    calendar: str
    covered: bool  # false: no source covers the year, so the rest is empty
    business_day: bool
    status: str  # open | closed | early_close | weekend; "" if not covered
    holiday: str
    close_time: str
    projected: bool


class DayLookupResponse(BaseModel):
    date: str
    weekday: str
    calendars: list[DayOut]


Cal = Annotated[Calendars, Depends(calendars)]


def _close(c: Close) -> CloseOut:
    return CloseOut(date=c.date, status=c.status, holiday=c.holiday, close_time=c.close_time, projected=c.projected,
                    source=c.source)


def _calendar(cal: Calendars, c: CalendarInfo, today: date) -> CalendarOut:
    years = cal.coverage(c.name)
    kinds = {k: sum(1 for y in years if y.kind == k) for k in ("published", "rules", "projected")}
    published = [y.year for y in years if y.kind == "published"]
    ahead = cal.closes(c.name, today.isoformat(), (today + timedelta(days=NEXT_CLOSE_DAYS)).isoformat())
    closed = next((x for x in ahead if x.status == "closed"), None)
    early = next((x for x in ahead if x.status == "early_close"), None)
    return CalendarOut(name=c.name, description=c.description, timezone=c.timezone, first_year=c.first_year,
                       last_year=c.last_year, coverage=CoverageOut(**kinds, last_published_year=max(published, default=0)),
                       next_close=_close(closed) if closed else None, next_early_close=_close(early) if early else None)


@router.get("/calendars", operation_id="listCalendars", response_model=CalendarsResponse)
def list_calendars(cal: Cal):
    """Every holiday calendar (FED, SIFMA-US, NYSE): its time zone, the years covered by kind of source, the furthest
    published year, and the next close and early close."""
    today = _today()
    return CalendarsResponse(as_of=today.isoformat(), calendars=[_calendar(cal, c, today) for c in cal.list_calendars()])


@router.get("/calendars/upcoming", operation_id="upcomingCloses", response_model=UpcomingResponse)
def upcoming_closes(cal: Cal, days: Annotated[int, Query(ge=1, le=730)] = 180):
    """The closes and early closes in the next `days` days across every calendar, a row per date, so the days where
    FED, SIFMA-US and NYSE differ stand side by side."""
    today = _today()
    end = today + timedelta(days=days)
    names = [c.name for c in cal.list_calendars()]
    by_date: dict[str, dict[str, CloseOut]] = {}
    for n in names:
        for c in cal.closes(n, today.isoformat(), end.isoformat()):
            by_date.setdefault(c.date, {})[n] = _close(c)
    return UpcomingResponse(start=today.isoformat(), end=end.isoformat(), calendars=names,
                            days=[UpcomingDay(date=d, calendars=v) for d, v in sorted(by_date.items())])


@router.get("/calendars/day", operation_id="calendarDay", response_model=DayLookupResponse)
def calendar_day(cal: Cal, date_: Annotated[date | None, Query(alias="date")] = None):
    """Is a date (default today) a business day on each calendar, and if not, why."""
    on = date_ or _today()
    out = []
    for c in cal.list_calendars():
        try:
            a = cal.business_day(c.name, on.isoformat())
        except OutOfRange:
            out.append(DayOut(calendar=c.name, covered=False, business_day=False, status="", holiday="", close_time="",
                              projected=False))
            continue
        out.append(DayOut(calendar=c.name, covered=True, business_day=a.business_day, status=a.status,
                          holiday=a.holiday, close_time=a.close_time, projected=a.projected))
    return DayLookupResponse(date=on.isoformat(), weekday=on.strftime("%A"), calendars=out)


def _calendar_name(cal: Calendars, name: str) -> CalendarInfo:
    wanted = name.strip().upper()
    info = next((c for c in cal.list_calendars() if c.name.upper() == wanted), None)
    if info is None:
        raise HTTPException(404, f"no calendar named {name!r}")
    return info


# Declared before /calendars/{name}/{year}, which would otherwise take "sources" for a year.
@router.get("/calendars/{name}/sources", operation_id="calendarSources", response_model=CalendarSourcesResponse)
def calendar_sources(name: str, cal: Cal):
    """A calendar's sources in precedence order, with the years each is credited for."""
    info = _calendar_name(cal, name)
    return CalendarSourcesResponse(calendar=info.name, sources=[CalendarSourceOut(**vars(x)) for x in cal.sources(info.name)])


@router.get("/calendars/{name}/disagreements", operation_id="calendarDisagreements", response_model=DisagreementsResponse)
def calendar_disagreements(name: str, cal: Cal):
    """Days where a source says something other than the calendar, because another source decided them. calendar-svc
    reads mkt-data's current rows for this, so it takes a few seconds."""
    info = _calendar_name(cal, name)
    return DisagreementsResponse(calendar=info.name, days=[DisagreementOut(**vars(d)) for d in cal.disagreements(info.name)])


@router.get("/calendars/{name}/days/{day}", operation_id="calendarDayHistory", response_model=DayHistoryResponse)
def calendar_day_history(name: str, day: date, cal: Cal):
    """Every version of a day on one calendar, oldest first: how its status changed and which source said so."""
    info = _calendar_name(cal, name)
    rows = cal.day_history(info.name, day.isoformat())
    return DayHistoryResponse(calendar=info.name, date=day.isoformat(), versions=[DayVersionOut(
        status=v.status, holiday=v.holiday, close_time=v.close_time, source=v.source, capture_id=v.capture_id,
        valid_from=v.valid_from, valid_to=v.valid_to) for v in rows])


@router.get("/calendars/{name}/{year}", operation_id="calendarYear", response_model=CalendarYearOut)
def calendar_year(name: str, year: Annotated[int, Path(ge=1800, le=2200)], cal: Cal):
    """One calendar's year: its closes and early closes, and the source that decided the year."""
    info = _calendar_name(cal, name)
    cover = next((y for y in cal.coverage(info.name) if y.year == year), None)
    if cover is None:
        raise HTTPException(404, f"{info.name} doesn't cover {year} (it covers {info.first_year} to {info.last_year})")
    closes = cal.closes(info.name, f"{year}-01-01", f"{year}-12-31")
    return CalendarYearOut(calendar=info.name, timezone=info.timezone, year=year, source=cover.source, kind=cover.kind,
                           closes=[_close(c) for c in closes])


def event_title(key: str) -> str:
    for prefix, title in EVENT_TITLES:
        if key.startswith(prefix):
            return title
    return key.replace("-", " ").capitalize()


def _detail(sec: Securities, i: Instrument) -> Instrument:
    """An instrument with its notes (the list leaves them out), cached like the list."""
    if i.notes or i.identifiers:
        return i
    hit = _detail_cache.get(i.short_name)
    if hit and time.monotonic() - hit[0] < DETAIL_CACHE_SECONDS:
        return hit[1]
    out = sec.get_instrument(i.short_name)
    _detail_cache[i.short_name] = (time.monotonic(), out)
    return out


@router.get("/events", operation_id="getEvents", response_model=EventsResponse)
def get_events(sec: Sec, series: Annotated[list[str], Query(min_length=1, max_length=14)]):
    """What a chart of these series should mark: the security master's notes on their instruments (first
    published, where H.15 starts, gaps, method changes), over all of history. A note on several instruments
    (the 2021 method change is on every CMT) comes once, naming them all. Series as for /api/bars.
    """
    try:
        specs = [blocks.parse(x) for x in series]
    except blocks.BadRequest as e:
        raise HTTPException(422, str(e)) from None
    insts = list({i.short_name: i for spec in specs for i in (_resolve(sec, n) for n in spec.names)}.values())
    merged: dict[tuple[str, str, str], list[str]] = {}
    for i in insts:
        for n in _detail(sec, i).notes:
            merged.setdefault((n.date, n.key, n.text), []).append(i.short_name)
    return EventsResponse(events=[
        EventOut(date=d, key=k, title=event_title(k), text=t, series=names)
        for (d, k, t), names in sorted(merged.items(), key=lambda x: (x[0][0], x[0][1]))
    ])


FINAL_CACHE = "private, max-age=86400"
OPEN_CACHE = "private, max-age=300"


@router.get("/bars", operation_id="getBars", response_model=BarsResponse)
def get_bars(response: Response, sec: Sec, quo: Quo,
             series: Annotated[list[str], Query(min_length=1, max_length=14)],
             interval: Interval, block: str, source: str = ""):
    """Bars for a chart: its series at one interval, one fixed block at a time (app/blocks.py).

    A series is an instrument (`UST-10Y-CMT`: its yield), `spread(LONG,SHORT)` (long minus short) or
    `fly(WING,BODY,WING)` (2 x body minus both wings). Values are decimals ("0.041", "0.0052"), each with a
    `_display` form in the series' `unit` (percent for a yield, basis points for a spread or fly). Blocks are a year of days or a decade of weeks or months, so a
    screenful is a handful of requests and the same block is always the same URL: a finished
    block (`final`) is cached by the browser for a day. `source` (UST-PAR, H15-TCM) narrows yields to one
    publisher. All arithmetic is Decimal; values are strings.
    """
    try:
        b = blocks.block(interval, block)
        specs = [blocks.parse(x) for x in series]
    except blocks.BadRequest as e:
        raise HTTPException(422, str(e)) from None
    today = _today()
    start, end = b.start.isoformat(), min(b.end, today).isoformat()
    insts = [[_resolve(sec, n) for n in spec.names] for spec in specs]
    final = blocks.is_final(b, today)
    response.headers["Cache-Control"] = FINAL_CACHE if final else OPEN_CACHE
    if b.start > today:
        return BarsResponse(interval=interval, block=block, start=start, end=b.end.isoformat(), final=False,
                            series=[_bar_series(spec, ii, []) for spec, ii in zip(specs, insts, strict=True)])

    def unique(ids):
        return list(dict.fromkeys(ids))

    # An instrument's own values (a CMT's yield, a Treasury security's price): quote-svc sums them up per
    # period in its query (days come as they are), one call per field.
    yield_bars: dict[int, list[BarOut]] = {}
    for fld, unit in (("yield", "%"), ("price", "price")):
        ids = unique(ii[0].sec_id for spec, ii in zip(specs, insts, strict=True)
                     if spec.kind == "yield" and field_of(ii[0]) == fld)
        src = source if fld == "yield" else ""
        if ids and interval == "day":
            for s in quo.series(ids, start, end, src, field=fld):
                yield_bars[s.sec_id] = [_bar(p.as_of, p.as_of, p.value, p.value, p.value, p.value, unit, p.source)
                                        for p in s.points]
        elif ids:
            for s in quo.bars(ids, start, end, interval, src, field=fld):
                yield_bars[s.sec_id] = [_bar(x.start, x.last, x.open, x.high, x.low, x.close, unit, x.source)
                                        for x in s.bars]

    # Spreads and flies: every day both (or all three) have, as decimals, then summed up per period.
    # A block bounds it: at most a decade of days per instrument.
    derived_ids = unique(i.sec_id for spec, ii in zip(specs, insts, strict=True) if spec.kind != "yield" for i in ii)
    daily: dict[int, dict[str, str]] = {}
    if derived_ids:
        daily = {s.sec_id: {p.as_of: p.value for p in s.points}
                 for s in quo.series(derived_ids, start, end, source)}

    out = []
    for spec, ii in zip(specs, insts, strict=True):
        if spec.kind == "yield":
            out.append(_bar_series(spec, ii, yield_bars.get(ii[0].sec_id, [])))
            continue
        cols = [daily.get(i.sec_id, {}) for i in ii]
        days = sorted(set.intersection(*(set(c) for c in cols)))
        vals = [[Decimal(c[d]) for c in cols] for d in days]
        if spec.kind == "spread":
            diff = [v[0] - v[1] for v in vals]
        else:
            diff = [2 * v[1] - v[0] - v[2] for v in vals]
        summed = bars([(d, x, None) for d, x in zip(days, diff, strict=True)], interval)
        out.append(_bar_series(spec, ii, [
            _bar(x["date"], x["last"], x["open"], x["high"], x["low"], x["close"], "bp", "",
                 [c[x["last"]] for c in cols])
            for x in summed]))
    return BarsResponse(interval=interval, block=block, start=start, end=b.end.isoformat(), final=final, series=out)


def _bar(start: str, last: str, o, h, lo, c, unit: str, source: str, inputs: list[str] | None = None) -> BarOut:
    """A bar of decimals (strings from quote-svc, or Decimals summed up here) with their display forms."""
    return BarOut(date=start, last_date=last, open=_s(Decimal(o)), high=_s(Decimal(h)), low=_s(Decimal(lo)),
                  close=_s(Decimal(c)), open_display=display(o, unit), high_display=display(h, unit),
                  low_display=display(lo, unit), close_display=display(c, unit), source=source,
                  inputs=[_s(Decimal(x)) for x in inputs or []], inputs_display=[percent(x) for x in inputs or []])


def _bar_series(spec: blocks.SeriesSpec, insts: list[Instrument], got: list[BarOut]) -> BarSeriesOut:
    names = [i.short_name for i in insts]
    unit = "price" if spec.kind == "yield" and field_of(insts[0]) == "price" else spec.unit
    return BarSeriesOut(key=blocks.key(spec.kind, names), label=blocks.label(spec.kind, names), unit=unit,
                        inputs=names if spec.kind != "yield" else [], bars=got)


def upstream_error_handler(_request, exc: UpstreamError):
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": f"a service didn't answer: {exc}"}, status_code=502)
