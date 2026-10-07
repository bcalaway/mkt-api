"""What mkt-api reads: secmaster-svc's instruments, quote-svc's quotes, mkt-data's sources and calendar-svc's calendars.

`Securities`, `Quotes`, `Sources` and `Calendars` are the interfaces the API
uses; the Grpc* classes talk to the real services over gRPC
(proto/securities.proto, proto/quotes.proto, proto/source_status.proto and
proto/calendars.proto, copied from those repos). Tests use fakes with the same methods. Answers are
plain dataclasses, values as the decimal strings quote-svc sends.

Each call opens a channel and closes it: a handful of calls per page, on the
same host, so there's nothing worth pooling yet.
"""

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Protocol

from app.config import settings

# Milliseconds spent waiting on secmaster-svc and quote-svc in this request,
# for the Server-Timing header (app/main.py). A list so the endpoint's
# thread (which gets a copy of the context) adds to the request's own total.
_upstream_ms: ContextVar[list[float] | None] = ContextVar("upstream_ms", default=None)


def start_timing() -> list[float]:
    total = [0.0]
    _upstream_ms.set(total)
    return total


class UpstreamError(RuntimeError):
    """A service didn't answer (down, or the call failed)."""


class NotFound(LookupError):
    pass


class OutOfRange(LookupError):
    """A date in a year no source covers (calendar-svc's OUT_OF_RANGE)."""


@dataclass(frozen=True)
class Identifier:
    scheme: str
    value: str
    valid_from: str = ""
    valid_to: str = ""


@dataclass(frozen=True)
class Note:
    key: str
    date: str
    text: str


@dataclass(frozen=True)
class Instrument:
    sec_id: int
    short_name: str
    tenor: str
    description: str
    status: str = "active"
    aliases: tuple[str, ...] = ()
    type: str = ""
    currency: str = ""
    country: str = ""
    curve: str = ""
    calendar: str = ""
    identifiers: tuple[Identifier, ...] = ()
    notes: tuple[Note, ...] = ()


@dataclass(frozen=True)
class Point:
    as_of: str
    value: str  # a decimal string, rates as decimals ("0.041")
    source: str


@dataclass(frozen=True)
class Series:
    sec_id: int
    points: list[Point] = field(default_factory=list)


@dataclass(frozen=True)
class Bar:
    start: str  # the period's first calendar day
    last: str  # the close's date
    open: str  # decimal strings, rates as decimals
    high: str
    low: str
    close: str
    source: str  # the close's


@dataclass(frozen=True)
class Bars:
    sec_id: int
    bars: list[Bar] = field(default_factory=list)


@dataclass(frozen=True)
class Latest:
    sec_id: int
    as_of: str
    value: str
    source: str


@dataclass(frozen=True)
class SecuritySummary:
    """A Treasury security as a list shows it (secmaster-svc's ListSecurities)."""

    sec_id: int
    short_name: str
    cusip: str
    security_type: str  # bill, note, bond, tips, frn
    cmb: bool = False
    term: str = ""
    original_term: str = ""
    coupon_rate: str = ""  # decimal, "0.0425"; "" for bills and FRNs
    frn_spread: str = ""
    issue_date: str = ""
    dated_date: str = ""
    maturity_date: str = ""
    status: str = ""
    description: str = ""
    on_the_run: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecurityList:
    as_of: str
    total: int
    securities: list[SecuritySummary]


@dataclass(frozen=True)
class OnTheRun:
    alias: str
    since: str = ""
    until: str = ""


@dataclass(frozen=True)
class Security:
    """One Treasury security in full (secmaster-svc's GetSecurity): terms and auctions as text maps."""

    instrument: Instrument
    terms: dict[str, str]
    provenance: dict[str, str]
    checks: list[str]
    auctions: list[dict[str, str]]
    on_the_run: list[OnTheRun]
    index_ratio: dict[str, str]
    strip: dict[str, str]


@dataclass(frozen=True)
class AuctionRow:
    sec_id: int
    short_name: str
    fields: dict[str, str]  # cusip, security_type, term, reopening, auction_date, offering_amount, high_yield, ...


@dataclass(frozen=True)
class SourceState:
    """One source mkt-data captures, with how its captures are going (mkt-data's SourceStatus)."""

    name: str
    group: str = ""  # calendars | rates | securities
    calendar: str = ""
    kind: str = ""  # published | rules | projected
    period_kind: str = ""  # day | month | year; "" for a one-page source
    url: str = ""
    description: str = ""
    parsed: bool = False
    captures: int = 0
    capture_bytes: int = 0
    latest_capture_id: int = 0
    latest_capture_at: str = ""
    last_success_at: str = ""
    last_check_at: str = ""
    last_outcome: str = ""  # new | unchanged | error | reparse
    last_parse_outcome: str = ""  # ok | error | ""
    last_error: str = ""
    checks_7d: int = 0
    errors_7d: int = 0
    periods: int = 0
    first_period: str = ""
    last_period: str = ""


@dataclass(frozen=True)
class SourceCheck:
    id: int
    checked_at: str
    outcome: str
    capture_id: int = 0
    period: str = ""
    detail: str = ""
    parse_outcome: str = ""
    parse_detail: str = ""


@dataclass(frozen=True)
class PeriodYear:
    year: str
    periods: int
    captures: int
    capture_bytes: int


@dataclass(frozen=True)
class SourceDetail:
    source: SourceState
    checks: list[SourceCheck]
    years: list[PeriodYear]


class Securities(Protocol):
    def list_instruments(self) -> list[Instrument]: ...
    def get_instrument(self, name: str) -> Instrument: ...  # NotFound
    def search(self, query: str, limit: int) -> list[Instrument]: ...
    def list_securities(self, security_type: str = "", include_inactive: bool = False, maturing_from: str = "",
                        maturing_to: str = "", as_of: str = "", limit: int = 0) -> SecurityList: ...
    def get_security(self, name: str, as_of: str = "") -> Security: ...  # NotFound
    def list_auctions(self, start: str, end: str, limit: int = 0) -> list[AuctionRow]: ...


class Quotes(Protocol):
    def series(self, sec_ids: list[int], start: str, end: str, source: str = "", field: str = "yield") -> list[Series]: ...
    def bars(self, sec_ids: list[int], start: str, end: str, interval: str, source: str = "",
             field: str = "yield") -> list[Bars]: ...
    def latest(self, sec_ids: list[int], field: str = "yield") -> list[Latest]: ...


@dataclass(frozen=True)
class CalendarInfo:
    name: str
    description: str = ""
    timezone: str = ""
    first_year: int = 0
    last_year: int = 0


@dataclass(frozen=True)
class Close:
    date: str
    status: str  # closed | early_close
    holiday: str = ""
    close_time: str = ""  # HH:MM local, early closes only
    projected: bool = False


@dataclass(frozen=True)
class YearCoverage:
    year: int
    source: str  # the mkt-data source credited, e.g. SIFMA-US-ARCHIVE
    kind: str  # published | rules | projected


@dataclass(frozen=True)
class DayAnswer:
    calendar: str
    date: str
    business_day: bool
    status: str  # open | closed | early_close | weekend
    holiday: str = ""
    close_time: str = ""
    projected: bool = False


class Calendars(Protocol):
    def list_calendars(self) -> list[CalendarInfo]: ...
    def closes(self, calendar: str, start: str, end: str) -> list[Close]: ...  # NotFound
    def coverage(self, calendar: str) -> list[YearCoverage]: ...  # NotFound
    def business_day(self, calendar: str, on: str) -> DayAnswer: ...  # NotFound, OutOfRange


class Sources(Protocol):
    def list_sources(self) -> list[SourceState]: ...
    def get_source(self, name: str, checks: int = 0) -> SourceDetail: ...  # NotFound


def _instrument(m) -> Instrument:
    return Instrument(
        sec_id=m.sec_id, short_name=m.short_name, tenor=m.tenor, description=m.description, status=m.status,
        aliases=tuple(m.aliases), type=m.type, currency=m.currency, country=m.country, curve=m.curve,
        calendar=m.calendar,
        identifiers=tuple(Identifier(i.scheme, i.value, i.valid_from, i.valid_to) for i in m.identifiers),
        notes=tuple(Note(n.key, n.date, n.text) for n in m.notes),
    )


class _Grpc:
    target = ""

    def _call(self, make_stub, method: str, request):
        import grpc  # here, so the rest of the app (and its tests) runs without compiled grpcio

        t0 = time.perf_counter()
        try:
            with grpc.insecure_channel(self.target) as channel:
                return getattr(make_stub(channel), method)(request, timeout=settings.grpc_timeout_seconds)
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                raise NotFound(e.details()) from None
            if e.code() == grpc.StatusCode.OUT_OF_RANGE:
                raise OutOfRange(e.details()) from None
            raise UpstreamError(f"{self.target} {method}: {e.code().name} {e.details() or ''}".strip()) from None
        finally:
            total = _upstream_ms.get()
            if total is not None:
                total[0] += (time.perf_counter() - t0) * 1000


class GrpcSecurities(_Grpc):
    def __init__(self, target: str | None = None):
        self.target = target or settings.secmaster_grpc

    def _stub(self, channel):
        from app.grpc_gen import securities_pb2_grpc

        return securities_pb2_grpc.SecuritiesStub(channel)

    def list_instruments(self) -> list[Instrument]:
        from app.grpc_gen import securities_pb2 as pb

        r = self._call(self._stub, "ListInstruments", pb.ListInstrumentsRequest(include_inactive=True))
        return [_instrument(i) for i in r.instruments]

    def get_instrument(self, name: str) -> Instrument:
        from app.grpc_gen import securities_pb2 as pb

        return _instrument(self._call(self._stub, "GetInstrument", pb.GetInstrumentRequest(name=name)))

    def search(self, query: str, limit: int) -> list[Instrument]:
        from app.grpc_gen import securities_pb2 as pb

        r = self._call(self._stub, "Search", pb.SearchRequest(query=query, limit=limit))
        return [_instrument(i) for i in r.instruments]

    def list_securities(self, security_type: str = "", include_inactive: bool = False, maturing_from: str = "",
                        maturing_to: str = "", as_of: str = "", limit: int = 0) -> SecurityList:
        from app.grpc_gen import securities_pb2 as pb

        r = self._call(self._stub, "ListSecurities", pb.ListSecuritiesRequest(
            security_type=security_type, include_inactive=include_inactive, maturing_from=maturing_from,
            maturing_to=maturing_to, as_of=as_of, limit=limit))
        return SecurityList(r.as_of, r.total, [SecuritySummary(
            sec_id=x.sec_id, short_name=x.short_name, cusip=x.cusip, security_type=x.security_type, cmb=x.cmb,
            term=x.term, original_term=x.original_term, coupon_rate=x.coupon_rate, frn_spread=x.frn_spread,
            issue_date=x.issue_date, dated_date=x.dated_date, maturity_date=x.maturity_date, status=x.status,
            description=x.description, on_the_run=tuple(x.on_the_run)) for x in r.securities])

    def get_security(self, name: str, as_of: str = "") -> Security:
        from app.grpc_gen import securities_pb2 as pb

        r = self._call(self._stub, "GetSecurity", pb.GetSecurityRequest(name=name, as_of=as_of))
        return Security(
            instrument=_instrument(r.instrument), terms=dict(r.terms), provenance=dict(r.provenance),
            checks=list(r.checks), auctions=[dict(a.fields) for a in r.auctions],
            on_the_run=[OnTheRun(o.alias, o.since, o.until) for o in r.on_the_run],
            index_ratio=dict(r.index_ratio), strip=dict(r.strip))

    def list_auctions(self, start: str, end: str, limit: int = 0) -> list[AuctionRow]:
        from app.grpc_gen import securities_pb2 as pb

        r = self._call(self._stub, "ListAuctions", pb.ListAuctionsRequest(start=start, end=end, limit=limit))
        return [AuctionRow(a.sec_id, a.short_name, dict(a.fields)) for a in r.auctions]


class GrpcQuotes(_Grpc):
    def __init__(self, target: str | None = None):
        self.target = target or settings.quote_grpc

    def _stub(self, channel):
        from app.grpc_gen import quotes_pb2_grpc

        return quotes_pb2_grpc.QuotesStub(channel)

    def series(self, sec_ids: list[int], start: str, end: str, source: str = "", field: str = "yield") -> list[Series]:
        from app.grpc_gen import quotes_pb2 as pb

        r = self._call(self._stub, "GetSeries", pb.GetSeriesRequest(
            sec_ids=sec_ids, start=start, end=end, field=field, source=source))
        return [Series(s.sec_id, [Point(p.as_of, p.value, p.source) for p in s.points]) for s in r.series]

    def bars(self, sec_ids: list[int], start: str, end: str, interval: str, source: str = "",
             field: str = "yield") -> list[Bars]:
        from app.grpc_gen import quotes_pb2 as pb

        r = self._call(self._stub, "GetBars", pb.GetBarsRequest(
            sec_ids=sec_ids, start=start, end=end, interval=interval, field=field, source=source))
        return [Bars(s.sec_id, [Bar(b.start, b.last, b.open, b.high, b.low, b.close, b.source) for b in s.bars])
                for s in r.series]

    def latest(self, sec_ids: list[int], field: str = "yield") -> list[Latest]:
        from app.grpc_gen import quotes_pb2 as pb

        r = self._call(self._stub, "GetLatest", pb.GetLatestRequest(sec_ids=sec_ids, field=field))
        return [Latest(x.sec_id, x.as_of, x.value, x.source) for x in r.latest]


def _state(m) -> SourceState:
    return SourceState(**{f: getattr(m, f) for f in SourceState.__dataclass_fields__})


class GrpcSources(_Grpc):
    def __init__(self, target: str | None = None):
        self.target = target or settings.mkt_data_grpc

    def _stub(self, channel):
        from app.grpc_gen import source_status_pb2_grpc

        return source_status_pb2_grpc.SourceStatusStub(channel)

    def list_sources(self) -> list[SourceState]:
        from app.grpc_gen import source_status_pb2 as pb

        r = self._call(self._stub, "ListSourceStatus", pb.ListSourceStatusRequest())
        return [_state(x) for x in r.sources]

    def get_source(self, name: str, checks: int = 0) -> SourceDetail:
        from app.grpc_gen import source_status_pb2 as pb

        r = self._call(self._stub, "GetSourceStatus", pb.GetSourceStatusRequest(source=name, checks=checks))
        return SourceDetail(
            source=_state(r.source),
            checks=[SourceCheck(**{f: getattr(c, f) for f in SourceCheck.__dataclass_fields__}) for c in r.checks],
            years=[PeriodYear(y.year, y.periods, y.captures, y.capture_bytes) for y in r.years])


class GrpcCalendars(_Grpc):
    def __init__(self, target: str | None = None):
        self.target = target or settings.calendar_grpc

    def _stub(self, channel):
        from app.grpc_gen import calendars_pb2_grpc

        return calendars_pb2_grpc.CalendarsStub(channel)

    def list_calendars(self) -> list[CalendarInfo]:
        from app.grpc_gen import calendars_pb2 as pb

        r = self._call(self._stub, "ListCalendars", pb.ListCalendarsRequest())
        return [CalendarInfo(c.name, c.description, c.timezone, c.first_year, c.last_year) for c in r.calendars]

    def closes(self, calendar: str, start: str, end: str) -> list[Close]:
        from app.grpc_gen import calendars_pb2 as pb

        r = self._call(self._stub, "Closes", pb.ClosesRequest(calendar=calendar, start=start, end=end))
        return [Close(c.date, c.status, c.holiday, c.close_time, c.projected) for c in r.closes]

    def coverage(self, calendar: str) -> list[YearCoverage]:
        from app.grpc_gen import calendars_pb2 as pb

        r = self._call(self._stub, "Coverage", pb.CoverageRequest(calendar=calendar))
        return [YearCoverage(y.year, y.source, y.kind) for y in r.years]

    def business_day(self, calendar: str, on: str) -> DayAnswer:
        from app.grpc_gen import calendars_pb2 as pb

        r = self._call(self._stub, "BusinessDay", pb.BusinessDayRequest(calendar=calendar, date=on))
        return DayAnswer(r.calendar, r.date, r.business_day, r.status, r.holiday, r.close_time, r.projected)
