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

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from app import blocks
from app.upstream import GrpcQuotes, GrpcSecurities, Instrument, NotFound, Quotes, Securities, UpstreamError

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
    """A decimal in its unit's display form: percent ("4.10") or basis points ("52")."""
    return percent(str(value)) if unit == "%" else bp(value)


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
    value: str  # the rate as a decimal: "0.041"
    display: str  # in percent: "4.10"
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
    open: str  # decimals: a yield's rate ("0.041"), a spread's or fly's difference of rates ("0.0052")
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
    unit: Literal["%", "bp"]  # what the *_display fields are in
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
    got = [x for x in quo.latest([i.sec_id]) if x.value]
    latest = LatestOut(date=got[0].as_of, value=got[0].value, display=percent(got[0].value),
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

    # Yields: quote-svc sums them up per period in its query (days come as they are).
    yield_ids = unique(ii[0].sec_id for spec, ii in zip(specs, insts, strict=True) if spec.kind == "yield")
    yield_bars: dict[int, list[BarOut]] = {}
    if yield_ids and interval == "day":
        for s in quo.series(yield_ids, start, end, source):
            yield_bars[s.sec_id] = [_bar(p.as_of, p.as_of, p.value, p.value, p.value, p.value, "%", p.source)
                                    for p in s.points]
    elif yield_ids:
        for s in quo.bars(yield_ids, start, end, interval, source):
            yield_bars[s.sec_id] = [_bar(x.start, x.last, x.open, x.high, x.low, x.close, "%", x.source)
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
    return BarSeriesOut(key=blocks.key(spec.kind, names), label=blocks.label(spec.kind, names), unit=spec.unit,
                        inputs=names if spec.kind != "yield" else [], bars=got)


def upstream_error_handler(_request, exc: UpstreamError):
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": f"a service didn't answer: {exc}"}, status_code=502)
