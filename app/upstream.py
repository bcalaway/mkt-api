"""What mkt-api reads: secmaster-svc's instruments and quote-svc's quotes, over gRPC.

`Securities` and `Quotes` are the interfaces the API uses; the Grpc* classes
talk to the real services (proto/securities.proto and proto/quotes.proto,
copied from those repos). Tests use fakes with the same methods. Answers are
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
class Latest:
    sec_id: int
    as_of: str
    value: str
    source: str


class Securities(Protocol):
    def list_instruments(self) -> list[Instrument]: ...
    def get_instrument(self, name: str) -> Instrument: ...  # NotFound
    def search(self, query: str, limit: int) -> list[Instrument]: ...


class Quotes(Protocol):
    def series(self, sec_ids: list[int], start: str, end: str, source: str = "") -> list[Series]: ...
    def latest(self, sec_ids: list[int]) -> list[Latest]: ...


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


class GrpcQuotes(_Grpc):
    def __init__(self, target: str | None = None):
        self.target = target or settings.quote_grpc

    def _stub(self, channel):
        from app.grpc_gen import quotes_pb2_grpc

        return quotes_pb2_grpc.QuotesStub(channel)

    def series(self, sec_ids: list[int], start: str, end: str, source: str = "") -> list[Series]:
        from app.grpc_gen import quotes_pb2 as pb

        r = self._call(self._stub, "GetSeries", pb.GetSeriesRequest(
            sec_ids=sec_ids, start=start, end=end, field="yield", source=source))
        return [Series(s.sec_id, [Point(p.as_of, p.value, p.source) for p in s.points]) for s in r.series]

    def latest(self, sec_ids: list[int]) -> list[Latest]:
        from app.grpc_gen import quotes_pb2 as pb

        r = self._call(self._stub, "GetLatest", pb.GetLatestRequest(sec_ids=sec_ids, field="yield"))
        return [Latest(x.sec_id, x.as_of, x.value, x.source) for x in r.latest]
