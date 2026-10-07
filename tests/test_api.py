"""The JSON API (app/api.py), against fake upstreams."""

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api
from app.main import app
from app.openapi import schema_text
from app.upstream import UpstreamError
from tests.fakes import FakeQuotes, FakeSecurities

ROOT = Path(__file__).resolve().parent.parent
client = TestClient(app)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    sec, quo = FakeSecurities(), FakeQuotes()
    app.dependency_overrides[api.securities] = lambda: sec
    app.dependency_overrides[api.quotes] = lambda: quo
    api._instruments_cache.clear()
    api._detail_cache.clear()
    monkeypatch.setattr(api, "_today", lambda: date(2026, 10, 3))  # a Saturday
    yield sec, quo
    app.dependency_overrides.clear()


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_percent():
    assert api.percent("0.041") == "4.10"
    assert api.percent("0.04125") == "4.125"
    assert api.percent("0") == "0.00"
    assert api.bp("0.0052") == "52" and api.bp("-0.00025") == "-2.5" and api.bp("0") == "0"


def test_offset_dates():
    assert api.offset_date(date(2026, 10, 3), "1W") == date(2026, 9, 26)
    assert api.offset_date(date(2026, 3, 31), "1M") == date(2026, 2, 28)
    assert api.offset_date(date(2026, 1, 15), "3M") == date(2025, 10, 15)
    assert api.offset_date(date(2024, 2, 29), "1Y") == date(2023, 2, 28)


def test_instruments_by_short_name_without_sec_ids():
    out = client.get("/api/instruments").json()
    assert [i["name"] for i in out] == ["UST-1.5M-CMT", "UST-2Y-CMT", "UST-10Y-CMT", "UST-4.25-2035-08-15"]
    assert "sec_id" not in str(out) and out[3]["type"] == "ust_note"
    tenors = client.get("/api/instruments", params={"type": "cmt_yield"}).json()
    assert [i["name"] for i in tenors] == ["UST-1.5M-CMT", "UST-2Y-CMT", "UST-10Y-CMT"]


def test_an_instrument_by_alias_with_identifiers_notes_and_latest():
    six = client.get("/api/instruments/ust-6w-cmt").json()
    assert six["name"] == "UST-1.5M-CMT" and six["latest"] == {
        "date": "2026-10-02", "value": "0.04", "display": "4.00", "source": "UST-PAR"}
    ten = client.get("/api/instruments/UST-10Y-CMT").json()
    assert [i["scheme"] for i in ten["identifiers"]] == ["UST-PAR", "H15-TCM"]
    assert ten["identifiers"][0]["valid_from"] is None and ten["notes"][0]["key"] == "par-curve-method-2021"
    assert ten["latest"]["source"] == "H15-TCM"
    assert client.get("/api/instruments/NOPE").status_code == 404


def test_search():
    assert [i["name"] for i in client.get("/api/search", params={"q": "10y"}).json()] == ["UST-10Y-CMT"]
    assert client.get("/api/search", params={"q": ""}).status_code == 422


def test_curve_uses_the_last_business_day_on_or_before_each_date():
    out = client.get("/api/curve", params={"compare": ["1D", "2d"]}).json()["curves"]
    latest, one, two = out
    # Saturday the 3rd: Friday the 2nd's curve; the 1.5-month has a value only then.
    assert (latest["label"], latest["requested"], latest["date"]) == ("latest", "2026-10-03", "2026-10-02")
    assert [p["name"] for p in latest["points"]] == ["UST-1.5M-CMT", "UST-2Y-CMT", "UST-10Y-CMT"]
    assert (latest["points"][2]["value"], latest["points"][2]["display"]) == ("0.041", "4.10")
    assert latest["missing"] == []
    assert (one["label"], one["date"]) == ("1D", "2026-10-02")
    assert (two["date"], two["missing"]) == ("2026-10-01", ["UST-1.5M-CMT"])
    assert client.get("/api/curve", params={"compare": "1Q"}).status_code == 422
    nothing = client.get("/api/curve", params={"date": "2020-01-01"}).json()["curves"][0]
    assert nothing["date"] is None and nothing["points"] == [] and len(nothing["missing"]) == 3


def test_the_curve_asks_only_for_the_tenors(fakes):
    # Thousands of active Treasury securities live beside the CMTs; asking quote-svc for all of them timed out.
    _, quo = fakes
    client.get("/api/curve")
    asked = {i for call in quo.calls if call[0] == "series" for i in call[1]}
    assert asked == {1, 2, 10}


def test_an_upstream_failure_is_a_502(fakes):
    sec, _ = fakes

    def down():
        raise UpstreamError("secmaster-svc:9090 ListInstruments: UNAVAILABLE")

    sec.list_instruments = down
    r = client.get("/api/instruments")
    assert r.status_code == 502 and "UNAVAILABLE" in r.json()["detail"]


def test_the_committed_schema_is_current():
    # mkt-ui's typed client is generated from openapi.json: regenerate it with
    # `python -m app.openapi > openapi.json` after changing the API.
    assert (ROOT / "openapi.json").read_text() == schema_text()


def test_period_starts():
    d = date(2026, 10, 2)  # a Friday
    assert api.period_start(d, "day") == d
    assert api.period_start(d, "week") == date(2026, 9, 28)
    assert api.period_start(d, "month") == date(2026, 10, 1)
    assert api.period_start(d, "quarter") == date(2026, 10, 1)
    assert api.period_start(date(2026, 8, 31), "quarter") == date(2026, 7, 1)
    assert api.period_start(d, "year") == date(2026, 1, 1)


def test_server_timing_header():
    r = client.get("/api/instruments")
    assert r.headers["server-timing"].startswith("api;dur=") and "upstream;dur=" in r.headers["server-timing"]


def test_blocks():
    from app.blocks import BadRequest, block, block_of, is_final

    d = block("day", "2026")
    assert (d.start, d.end) == (date(2026, 1, 1), date(2026, 12, 31))
    # A week belongs to the decade its Monday is in: the 2020s' weeks run from Monday 6 January 2020
    # to Sunday 6 January 2030 (Monday 31 December 2029's week).
    w = block("week", "2020")
    assert (w.start, w.end) == (date(2020, 1, 6), date(2030, 1, 6))
    assert block_of("week", date(2020, 1, 3)) == "2010" and block_of("week", date(2030, 1, 6)) == "2020"
    m = block("month", "2020")
    assert (m.start, m.end) == (date(2020, 1, 1), date(2029, 12, 31))
    assert block_of("month", date(2026, 10, 2)) == "2020" and block_of("day", date(2026, 10, 2)) == "2026"
    for interval, bad in [("day", "2026-10"), ("week", "2026"), ("month", "2026"), ("year", "20")]:
        with pytest.raises(BadRequest):
            block(interval, bad)
    assert not is_final(block("day", "2025"), date(2026, 1, 3))
    assert is_final(block("day", "2025"), date(2026, 1, 8))


def test_series_expressions():
    from app.blocks import BadRequest, parse

    assert parse("UST-10Y-CMT").kind == "yield"
    assert parse(" spread( UST-10Y-CMT , ust-2y-cmt ) ").names == ("UST-10Y-CMT", "ust-2y-cmt")
    assert parse("FLY(UST-2Y-CMT,UST-5Y-CMT,UST-10Y-CMT)").kind == "fly"
    for bad in ["spread(UST-10Y-CMT)", "fly(A,B)", "sum(A,B)", "A B", ""]:
        with pytest.raises(BadRequest):
            parse(bad)


def test_bars_by_block(fakes):
    r = client.get("/api/bars", params={"series": ["UST-10Y-CMT", "spread(UST-10Y-CMT,ust-2y-cmt)",
                                                   "fly(UST-6W-CMT,UST-2Y-CMT,UST-10Y-CMT)"],
                                        "interval": "day", "block": "2026"})
    out = r.json()
    assert (out["start"], out["end"], out["final"]) == ("2026-01-01", "2026-12-31", False)
    assert r.headers["cache-control"] == "private, max-age=300"
    ten, spread, fly = out["series"]
    assert (ten["key"], ten["unit"], ten["inputs"]) == ("UST-10Y-CMT", "%", [])
    # Values are decimals; the display forms are in the series' unit.
    assert ten["bars"][-1] == {"date": "2026-10-02", "last_date": "2026-10-02", "open": "0.041", "high": "0.041",
                              "low": "0.041", "close": "0.041", "open_display": "4.10", "high_display": "4.10",
                              "low_display": "4.10", "close_display": "4.10", "source": "H15-TCM", "inputs": [],
                              "inputs_display": []}
    assert (spread["key"], spread["label"], spread["unit"]) == (
        "spread(UST-10Y-CMT,UST-2Y-CMT)", "UST-10Y-CMT - UST-2Y-CMT", "bp")
    assert [(x["date"], x["close"], x["close_display"], x["inputs_display"]) for x in spread["bars"]] == [
        ("2026-09-30", "0.0054", "54", ["4.15", "3.61"]), ("2026-10-01", "0.0052", "52", ["4.12", "3.60"]),
        ("2026-10-02", "0.0052", "52", ["4.10", "3.58"])]
    assert spread["bars"][-1]["inputs"] == ["0.041", "0.0358"]
    # 2 x 3.58 - 4.00 - 4.10 = -94 bp, on the one day all three have.
    assert fly["key"] == "fly(UST-1.5M-CMT,UST-2Y-CMT,UST-10Y-CMT)"
    assert [(x["date"], x["close"], x["close_display"]) for x in fly["bars"]] == [("2026-10-02", "-0.0094", "-94")]


def test_bars_by_week_and_decade(fakes):
    _, quo = fakes
    week = client.get("/api/bars", params={"series": ["UST-10Y-CMT", "spread(UST-10Y-CMT,UST-2Y-CMT)"],
                                           "interval": "week", "block": "2020"}).json()
    ten, spread = week["series"]
    assert [(x["date"], x["open"], x["high"], x["low"], x["close"], x["last_date"]) for x in ten["bars"]] == [
        ("2026-09-28", "0.0415", "0.0415", "0.041", "0.041", "2026-10-02")]
    assert [(x["open_display"], x["high_display"], x["low_display"], x["close_display"]) for x in ten["bars"]] == [
        ("4.15", "4.15", "4.10", "4.10")]
    assert [(x["date"], x["open_display"], x["low_display"], x["close_display"]) for x in spread["bars"]] == [
        ("2026-09-28", "54", "52", "52")]
    # The yield's bars come from quote-svc's GetBars, over the block's whole weeks (up to today).
    assert ("bars", (10,), "2020-01-06", "2026-10-03", "week", "") in quo.calls
    old = client.get("/api/bars", params={"series": "UST-10Y-CMT", "interval": "month", "block": "1990"})
    assert old.json()["final"] is True and old.headers["cache-control"] == "private, max-age=86400"
    later = client.get("/api/bars", params={"series": "UST-10Y-CMT", "interval": "day", "block": "2027"}).json()
    assert later["series"][0]["bars"] == [] and later["final"] is False


def test_bars_rejects_bad_requests():
    assert client.get("/api/bars", params={"series": "UST-10Y-CMT", "interval": "week", "block": "2026"}).status_code == 422
    assert client.get("/api/bars", params={"series": "spread(UST-10Y-CMT)", "interval": "day",
                                           "block": "2026"}).status_code == 422
    assert client.get("/api/bars", params={"series": "NOPE", "interval": "day", "block": "2026"}).status_code == 404


def test_events_from_notes():
    out = client.get("/api/events", params={"series": ["UST-2Y-CMT", "spread(UST-10Y-CMT,UST-2Y-CMT)"]}).json()
    # The method change is on both: it comes once, naming both.
    assert out["events"] == [
        {"date": "1976-06-01", "key": "h15-first", "title": "H.15 starts", "text": "H.15 starts.",
         "series": ["UST-2Y-CMT"]},
        {"date": "2021-12-06", "key": "par-curve-method-2021", "title": "Method change", "text": "Method changed.",
         "series": ["UST-2Y-CMT", "UST-10Y-CMT"]},
    ]
    assert client.get("/api/events", params={"series": "NOPE"}).status_code == 404
    assert api.event_title("gap-2002-2006") == "Gap" and api.event_title("odd-one") == "Odd one"


def test_swagger_ui_and_schema_under_api():
    docs = client.get("/api/docs")
    assert docs.status_code == 200 and "text/html" in docs.headers["content-type"] and "/api/openapi.json" in docs.text
    assert client.get("/api/openapi.json").json()["paths"].keys() == schema_text_paths()
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


def schema_text_paths():
    import json

    return json.loads(schema_text())["paths"].keys()


# --- Treasury securities (phase 3, step 9) ---

def test_securities_list_outstanding_with_latest_price(fakes):
    _ = fakes
    out = client.get("/api/securities").json()
    assert out["total"] == 1 and [s["name"] for s in out["securities"]] == ["UST-4.25-2035-08-15"]
    note = out["securities"][0]
    assert (note["coupon"], note["coupon_display"], note["on_the_run"]) == ("0.0425", "4.25", ["UST-10Y-OTR"])
    assert note["price"] == {"date": "2026-10-01", "value": "99.828125", "display": "99.828125", "source": "TD-PRICES"}
    assert "sec_id" not in str(out)
    every = client.get("/api/securities", params={"include_inactive": True, "type": "bill"}).json()
    assert [s["name"] for s in every["securities"]] == ["UST-B-2026-01-02"] and every["securities"][0]["price"] is None
    assert client.get("/api/securities", params={"type": "swap"}).status_code == 422


def test_a_security_in_full(fakes):
    d = client.get("/api/securities/ust-10y-otr").json()
    assert d["name"] == "UST-4.25-2035-08-15" and d["terms"]["cusip"] == "91282CNC1"
    assert d["provenance"]["coupon_rate"].startswith("published") and d["auctions"][0]["auction_date"] == "2025-08-06"
    assert d["on_the_run"] == [{"alias": "UST-10Y-OTR", "since": "2025-08-06", "until": None}]
    assert d["index_ratio"] is None and d["price"]["display"] == "99.828125"
    assert client.get("/api/securities/UST-10Y-CMT").status_code == 404


def test_a_security_charts_its_price(fakes):
    _, quo = fakes
    out = client.get("/api/bars", params={"series": "UST-4.25-2035-08-15", "interval": "day", "block": "2026"}).json()
    [s] = out["series"]
    assert s["unit"] == "price" and [b["close_display"] for b in s["bars"]] == ["99.500", "99.828125"]
    assert ("series", (500,), "2026-01-01", "2026-10-03", "", "price") in quo.calls
    inst = client.get("/api/instruments/UST-4.25-2035-08-15").json()
    assert inst["latest"]["display"] == "99.828125"
