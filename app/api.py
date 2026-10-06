"""The JSON API mkt-ui's server calls (mkt-data's docs/phase-2.md, Part B step 9).

Every answer names instruments by short name (`UST-10Y-CMT`), never by
secmaster-svc's internal sec_id. Values stay decimal strings end to end:
`value` is the rate as a decimal ("0.041"), `percent` the same in percent
("4.10"), spreads in basis points ("52"), all computed with Decimal here so
the UI never does arithmetic on floats it shows back.

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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.upstream import GrpcQuotes, GrpcSecurities, Instrument, NotFound, Quotes, Securities, UpstreamError

router = APIRouter(prefix="/api")
NEW_YORK = ZoneInfo("America/New_York")
# How far back a curve date looks for the last business day on or before it.
CURVE_LOOKBACK_DAYS = 10
INSTRUMENTS_CACHE_SECONDS = 300
OFFSET = re.compile(r"^(\d{1,2})([DWMY])$")


# --- Dependencies (tests override these) ---


def securities() -> Securities:
    return GrpcSecurities()


def quotes() -> Quotes:
    return GrpcQuotes()


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


def percent(value: str) -> str:
    """A decimal rate as percent, exactly, with at least two places: "0.041" -> "4.10"."""
    p = (Decimal(value) * 100).normalize()
    if p.as_tuple().exponent > -2:
        p = p.quantize(Decimal("0.01"))
    return format(p, "f")


def basis_points(long: str, short: str) -> str:
    """long - short in basis points, exactly: "0.0412", "0.036" -> "52"."""
    bp = ((Decimal(long) - Decimal(short)) * 10000).normalize()
    return format(bp, "f")


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

Interval = Literal["day", "week", "month", "quarter", "year"]


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
    value: str
    percent: str
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


class PointOut(BaseModel):
    date: str  # the day; for a longer interval, the period's first calendar day
    last_date: str  # the day the close is from (the same as date for daily points)
    value: str  # the close, as a decimal rate
    percent: str  # the close in percent
    open_percent: str  # the period's first, highest and lowest values, in percent (all the close for a day)
    high_percent: str
    low_percent: str
    source: str  # UST-PAR or H15-TCM: which publisher golden took the close from


class SeriesOut(BaseModel):
    name: str
    tenor: str
    points: list[PointOut]


class SourceRun(BaseModel):
    start: int  # the index in dates where this source starts
    source: str


class CompactSeriesOut(BaseModel):
    name: str
    tenor: str
    dates: list[str]
    percents: list[str]  # close in percent, one per date
    sources: list[SourceRun]  # runs: each source applies from its index to the next run's


class CompactSeriesResponse(BaseModel):
    start: str
    end: str
    series: list[CompactSeriesOut]


class SeriesResponse(BaseModel):
    start: str
    end: str
    interval: Interval
    series: list[SeriesOut]


class CurvePointOut(BaseModel):
    name: str
    tenor: str
    value: str
    percent: str
    source: str


class CurveOut(BaseModel):
    label: str  # "latest", "1W", "1M", ...
    requested: str  # the date asked for
    date: str | None  # the business day on or before it that has values; None if none within 10 days
    points: list[CurvePointOut]
    missing: list[str]  # instruments with no value that day (not yet published then, or a gap)


class CurveResponse(BaseModel):
    curves: list[CurveOut]


class SpreadPointOut(BaseModel):
    date: str  # the day, or the period's first calendar day
    last_date: str
    bp: str  # the close
    open_bp: str
    high_bp: str
    low_bp: str
    long: str  # percent, on last_date
    short: str


class SpreadResponse(BaseModel):
    name: str  # "UST-10Y-CMT - UST-2Y-CMT"
    long: str
    short: str
    start: str
    end: str
    interval: Interval
    points: list[SpreadPointOut]


def _summary(i: Instrument) -> InstrumentSummary:
    return InstrumentSummary(name=i.short_name, aliases=list(i.aliases), tenor=i.tenor, description=i.description,
                             status=i.status)


Sec = Annotated[Securities, Depends(securities)]
Quo = Annotated[Quotes, Depends(quotes)]


# --- Routes ---


@router.get("/instruments", operation_id="listInstruments", response_model=list[InstrumentSummary])
def list_instruments(sec: Sec):
    """Every instrument, shortest tenor first."""
    return [_summary(i) for i in _instruments(sec)]


@router.get("/instruments/{name}", operation_id="getInstrument", response_model=InstrumentDetail)
def get_instrument(name: str, sec: Sec, quo: Quo):
    """One instrument by short name or alias, with its identifiers, notes and latest golden value."""
    i = _resolve(sec, name)
    if not i.identifiers and not i.notes:
        i = sec.get_instrument(i.short_name)  # the list leaves identifiers and notes out
    got = [x for x in quo.latest([i.sec_id]) if x.value]
    latest = LatestOut(date=got[0].as_of, value=got[0].value, percent=percent(got[0].value),
                       source=got[0].source) if got else None
    return InstrumentDetail(
        **_summary(i).model_dump(), type=i.type, currency=i.currency, country=i.country, curve=i.curve,
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


@router.get("/series", operation_id="getSeries", response_model=SeriesResponse)
def series(sec: Sec, quo: Quo, name: Annotated[list[str], Query(min_length=1, max_length=14)],
           start: date | None = None, end: date | None = None, source: str = "", interval: Interval = "day"):
    """Golden yields (or one source's: UST-PAR, H15-TCM) for instruments over a date range (default: a year).

    `interval` sums each series up per week, month, quarter or year: open, high, low and close, so all of
    history fits a chart (about 780 monthly bars since 1962, against 16,000 days).
    """
    end = end or _today()
    start = start or offset_date(end, "1Y")
    if start > end:
        raise HTTPException(422, f"start {start} is after end {end}")
    found = [_resolve(sec, n) for n in name]
    got = {s.sec_id: s for s in quo.series([i.sec_id for i in found], start.isoformat(), end.isoformat(), source)}
    def points(sec_id: int) -> list[PointOut]:
        daily = [(p.as_of, Decimal(p.value), p.source) for p in (got[sec_id].points if sec_id in got else [])]
        return [PointOut(date=b["date"], last_date=b["last"], value=_s(b["close"]), percent=percent(_s(b["close"])),
                         open_percent=percent(_s(b["open"])), high_percent=percent(_s(b["high"])),
                         low_percent=percent(_s(b["low"])), source=b["extra"])
                for b in bars(daily, interval)]

    return SeriesResponse(start=start.isoformat(), end=end.isoformat(), interval=interval, series=[
        SeriesOut(name=i.short_name, tenor=i.tenor, points=points(i.sec_id)) for i in found
    ])


@router.get("/series/daily", operation_id="getDailySeries", response_model=CompactSeriesResponse)
def daily_series(sec: Sec, quo: Quo, name: Annotated[list[str], Query(min_length=1, max_length=14)],
                 start: date | None = None, end: date | None = None, source: str = ""):
    """Every day's yield in a compact form (columns, not objects), default all of history: for a chart that
    loads once and zooms without asking again (about 16,000 days per instrument since 1962).
    """
    end = end or _today()
    start = start or date(1962, 1, 1)
    if start > end:
        raise HTTPException(422, f"start {start} is after end {end}")
    found = [_resolve(sec, n) for n in name]
    got = {s.sec_id: s for s in quo.series([i.sec_id for i in found], start.isoformat(), end.isoformat(), source)}
    out = []
    for i in found:
        pts = got[i.sec_id].points if i.sec_id in got else []
        runs: list[SourceRun] = []
        for n, p in enumerate(pts):
            if not runs or runs[-1].source != p.source:
                runs.append(SourceRun(start=n, source=p.source))
        out.append(CompactSeriesOut(name=i.short_name, tenor=i.tenor, dates=[p.as_of for p in pts],
                                    percents=[percent(p.value) for p in pts], sources=runs))
    return CompactSeriesResponse(start=start.isoformat(), end=end.isoformat(), series=out)


@router.get("/curve", operation_id="getCurves", response_model=CurveResponse)
def curve(sec: Sec, quo: Quo, date_: Annotated[date | None, Query(alias="date")] = None,
          compare: Annotated[list[str] | None, Query(max_length=6)] = None):
    """The curve on a date (default: the latest), plus the same curve `compare` before it (1W, 1M, 1Y).

    Each uses the last business day on or before its date that has values.
    """
    base = date_ or _today()
    wanted = [("latest" if date_ is None else "date", base)] + [(c.upper(), offset_date(base, c)) for c in compare or []]
    insts = [i for i in _instruments(sec) if i.status == "active"]
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
                                            percent=percent(p.value), source=p.source))
        curves.append(CurveOut(label=label, requested=day.isoformat(), date=found, points=points, missing=missing))
    return CurveResponse(curves=curves)


@router.get("/spread", operation_id="getSpread", response_model=SpreadResponse)
def spread(sec: Sec, quo: Quo, long: str, short: str, start: date | None = None, end: date | None = None,
           interval: Interval = "day"):
    """long minus short in basis points on every date both have (2s10s: long=UST-10Y-CMT, short=UST-2Y-CMT).

    `interval` sums the daily spread up per week, month, quarter or year, as for /api/series.
    """
    end = end or _today()
    start = start or offset_date(end, "1Y")
    if start > end:
        raise HTTPException(422, f"start {start} is after end {end}")
    lo, sh = _resolve(sec, long), _resolve(sec, short)
    got = {s.sec_id: {p.as_of: p.value for p in s.points}
           for s in quo.series([lo.sec_id, sh.sec_id], start.isoformat(), end.isoformat())}
    a, b = got.get(lo.sec_id, {}), got.get(sh.sec_id, {})
    return SpreadResponse(
        name=f"{lo.short_name} - {sh.short_name}", long=lo.short_name, short=sh.short_name,
        start=start.isoformat(), end=end.isoformat(), interval=interval,
        points=[SpreadPointOut(date=x["date"], last_date=x["last"], bp=_s(x["close"]), open_bp=_s(x["open"]),
                               high_bp=_s(x["high"]), low_bp=_s(x["low"]), long=percent(a[x["last"]]),
                               short=percent(b[x["last"]]))
                for x in bars([(d, Decimal(basis_points(a[d], b[d])), None) for d in sorted(a.keys() & b.keys())],
                              interval)],
    )


def upstream_error_handler(_request, exc: UpstreamError):
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": f"a service didn't answer: {exc}"}, status_code=502)
