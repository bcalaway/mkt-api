"""The gRPC clients (app/upstream.py) against in-process fakes of secmaster-svc and quote-svc.

Needs compiled grpcio, so CI runs it and scripts/sandbox-test.sh skips it.
"""

from concurrent import futures

import grpc
import pytest

from app.grpc_gen import quotes_pb2, quotes_pb2_grpc, securities_pb2, securities_pb2_grpc
from app.upstream import GrpcQuotes, GrpcSecurities, NotFound, UpstreamError

TEN = securities_pb2.Instrument(
    sec_id=10, short_name="UST-10Y-CMT", tenor="P10Y", description="10-year", status="active", type="cmt_yield",
    curve="UST", calendar="SIFMA-US",
    identifiers=[securities_pb2.InstrumentIdentifier(scheme="UST-PAR", value="BC_10YEAR")],
    notes=[securities_pb2.InstrumentNote(key="k", date="2021-12-06", text="t")],
)


class Securities(securities_pb2_grpc.SecuritiesServicer):
    def ListInstruments(self, request, context):
        assert request.include_inactive
        return securities_pb2.ListInstrumentsResponse(instruments=[TEN])

    def GetInstrument(self, request, context):
        if request.name.upper() != "UST-10Y-CMT":
            context.abort(grpc.StatusCode.NOT_FOUND, f"no instrument {request.name}")
        return TEN

    def Search(self, request, context):
        return securities_pb2.ListInstrumentsResponse(instruments=[TEN][: request.limit])


class Quotes(quotes_pb2_grpc.QuotesServicer):
    def GetSeries(self, request, context):
        assert (request.field, request.source) == ("yield", "H15-TCM")
        return quotes_pb2.GetSeriesResponse(series=[quotes_pb2.Series(
            sec_id=i, short_name="x", points=[quotes_pb2.Point(as_of=request.start, value="0.041", source="H15-TCM")])
            for i in request.sec_ids])

    def GetBars(self, request, context):
        assert (request.interval, request.field) == ("month", "yield")
        return quotes_pb2.GetBarsResponse(series=[quotes_pb2.BarSeries(sec_id=i, short_name="x", bars=[quotes_pb2.Bar(
            start="2026-10-01", last="2026-10-02", open="0.0415", high="0.0415", low="0.041", close="0.041",
            source="UST-PAR")]) for i in request.sec_ids])

    def GetLatest(self, request, context):
        return quotes_pb2.GetLatestResponse(latest=[quotes_pb2.Latest(
            sec_id=10, short_name="UST-10Y-CMT", as_of="2026-10-02", value="0.041", source="UST-PAR")])


@pytest.fixture
def target():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    securities_pb2_grpc.add_SecuritiesServicer_to_server(Securities(), server)
    quotes_pb2_grpc.add_QuotesServicer_to_server(Quotes(), server)
    port = server.add_insecure_port("localhost:0")
    server.start()
    yield f"localhost:{port}"
    server.stop(None)


def test_securities(target):
    sec = GrpcSecurities(target)
    [i] = sec.list_instruments()
    assert (i.sec_id, i.short_name, i.tenor, i.identifiers[0].value, i.notes[0].key) == (
        10, "UST-10Y-CMT", "P10Y", "BC_10YEAR", "k")
    assert sec.get_instrument("ust-10y-cmt").short_name == "UST-10Y-CMT"
    with pytest.raises(NotFound):
        sec.get_instrument("NOPE")
    assert [x.short_name for x in sec.search("10", 5)] == ["UST-10Y-CMT"]


def test_quotes(target):
    quo = GrpcQuotes(target)
    [s] = quo.series([10], "2026-10-01", "2026-10-02", "H15-TCM")
    assert (s.sec_id, s.points[0].as_of, s.points[0].value, s.points[0].source) == (
        10, "2026-10-01", "0.041", "H15-TCM")
    [b] = quo.bars([10], "2026-10-01", "2026-10-31", "month")
    assert (b.sec_id, b.bars[0].start, b.bars[0].open, b.bars[0].close, b.bars[0].last) == (
        10, "2026-10-01", "0.0415", "0.041", "2026-10-02")
    [x] = quo.latest([10])
    assert (x.as_of, x.value) == ("2026-10-02", "0.041")


def test_an_unreachable_service_is_an_upstream_error(monkeypatch):
    from dataclasses import replace

    from app import upstream

    monkeypatch.setattr(upstream, "settings", replace(upstream.settings, grpc_timeout_seconds=1))
    with pytest.raises(UpstreamError, match="UNAVAILABLE|DEADLINE"):
        GrpcSecurities("localhost:1").list_instruments()
