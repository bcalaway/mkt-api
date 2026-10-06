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
    monkeypatch.setattr(api, "_today", lambda: date(2026, 10, 3))  # a Saturday
    yield sec, quo
    app.dependency_overrides.clear()


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_percent_and_basis_points():
    assert api.percent("0.041") == "4.10"
    assert api.percent("0.04125") == "4.125"
    assert api.percent("0") == "0.00"
    assert api.basis_points("0.0412", "0.036") == "52"
    assert api.basis_points("0.0358", "0.041") == "-52"
    assert api.basis_points("0.04125", "0.041") == "2.5"


def test_offset_dates():
    assert api.offset_date(date(2026, 10, 3), "1W") == date(2026, 9, 26)
    assert api.offset_date(date(2026, 3, 31), "1M") == date(2026, 2, 28)
    assert api.offset_date(date(2026, 1, 15), "3M") == date(2025, 10, 15)
    assert api.offset_date(date(2024, 2, 29), "1Y") == date(2023, 2, 28)


def test_instruments_by_short_name_without_sec_ids():
    out = client.get("/api/instruments").json()
    assert [i["name"] for i in out] == ["UST-1.5M-CMT", "UST-2Y-CMT", "UST-10Y-CMT"]
    assert "sec_id" not in str(out)


def test_an_instrument_by_alias_with_identifiers_notes_and_latest():
    six = client.get("/api/instruments/ust-6w-cmt").json()
    assert six["name"] == "UST-1.5M-CMT" and six["latest"] == {
        "date": "2026-10-02", "value": "0.04", "percent": "4.00", "source": "UST-PAR"}
    ten = client.get("/api/instruments/UST-10Y-CMT").json()
    assert [i["scheme"] for i in ten["identifiers"]] == ["UST-PAR", "H15-TCM"]
    assert ten["identifiers"][0]["valid_from"] is None and ten["notes"][0]["key"] == "par-curve-method-2021"
    assert ten["latest"]["source"] == "H15-TCM"
    assert client.get("/api/instruments/NOPE").status_code == 404


def test_search():
    assert [i["name"] for i in client.get("/api/search", params={"q": "10y"}).json()] == ["UST-10Y-CMT"]
    assert client.get("/api/search", params={"q": ""}).status_code == 422


def test_series_with_percent_and_source(fakes):
    out = client.get("/api/series", params={"name": ["UST-10Y-CMT", "UST-2Y-CMT"],
                                            "start": "2026-10-01", "end": "2026-10-02"}).json()
    ten, two = out["series"]
    assert ten["name"] == "UST-10Y-CMT" and ten["points"][1] == {
        "date": "2026-10-02", "last_date": "2026-10-02", "value": "0.041", "percent": "4.10",
        "open_percent": "4.10", "high_percent": "4.10", "low_percent": "4.10", "source": "H15-TCM"}
    assert out["interval"] == "day"
    assert len(two["points"]) == 2
    _, quo = fakes
    assert quo.calls[-1] == ("series", (10, 2), "2026-10-01", "2026-10-02", "")
    # Default range: a year to today.
    assert client.get("/api/series", params={"name": "UST-2Y-CMT"}).json()["start"] == "2025-10-03"
    assert client.get("/api/series", params={"name": "UST-2Y-CMT", "start": "2026-10-02",
                                             "end": "2026-10-01"}).status_code == 422
    assert client.get("/api/series").status_code == 422


def test_curve_uses_the_last_business_day_on_or_before_each_date():
    out = client.get("/api/curve", params={"compare": ["1D", "2d"]}).json()["curves"]
    latest, one, two = out
    # Saturday the 3rd: Friday the 2nd's curve; the 1.5-month has a value only then.
    assert (latest["label"], latest["requested"], latest["date"]) == ("latest", "2026-10-03", "2026-10-02")
    assert [p["name"] for p in latest["points"]] == ["UST-1.5M-CMT", "UST-2Y-CMT", "UST-10Y-CMT"]
    assert latest["points"][2]["percent"] == "4.10" and latest["missing"] == []
    assert (one["label"], one["date"]) == ("1D", "2026-10-02")
    assert (two["date"], two["missing"]) == ("2026-10-01", ["UST-1.5M-CMT"])
    assert client.get("/api/curve", params={"compare": "1Q"}).status_code == 422
    nothing = client.get("/api/curve", params={"date": "2020-01-01"}).json()["curves"][0]
    assert nothing["date"] is None and nothing["points"] == [] and len(nothing["missing"]) == 3


def test_spread_in_basis_points_on_common_dates():
    out = client.get("/api/spread", params={"long": "UST-10Y-CMT", "short": "UST-2Y-CMT",
                                            "start": "2026-09-30", "end": "2026-10-02"}).json()
    assert out["name"] == "UST-10Y-CMT - UST-2Y-CMT"
    assert [(p["date"], p["bp"]) for p in out["points"]] == [
        ("2026-09-30", "54"), ("2026-10-01", "52"), ("2026-10-02", "52")]
    assert out["points"][0]["long"] == "4.15" and out["points"][0]["short"] == "3.61"


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


def test_series_in_bars():
    out = client.get("/api/series", params={"name": "UST-10Y-CMT", "start": "2026-09-01", "end": "2026-10-31",
                                            "interval": "month"}).json()
    sep, oct_ = out["series"][0]["points"]
    assert out["interval"] == "month"
    assert sep == {"date": "2026-09-01", "last_date": "2026-09-30", "value": "0.0415", "percent": "4.15",
                   "open_percent": "4.15", "high_percent": "4.15", "low_percent": "4.15", "source": "UST-PAR"}
    # October: 4.12 then 4.10; the close's source is the close's.
    assert (oct_["date"], oct_["last_date"], oct_["open_percent"], oct_["high_percent"], oct_["low_percent"],
            oct_["percent"], oct_["source"]) == ("2026-10-01", "2026-10-02", "4.12", "4.12", "4.10", "4.10", "H15-TCM")
    week = client.get("/api/series", params={"name": "UST-10Y-CMT", "start": "2026-09-01", "end": "2026-10-31",
                                             "interval": "week"}).json()["series"][0]["points"]
    assert [(p["date"], p["high_percent"], p["low_percent"]) for p in week] == [("2026-09-28", "4.15", "4.10")]
    assert client.get("/api/series", params={"name": "UST-10Y-CMT", "interval": "hour"}).status_code == 422


def test_spread_in_bars():
    out = client.get("/api/spread", params={"long": "UST-10Y-CMT", "short": "UST-2Y-CMT", "start": "2026-09-01",
                                            "end": "2026-10-31", "interval": "week"}).json()
    [w] = out["points"]
    assert (w["date"], w["last_date"], w["open_bp"], w["high_bp"], w["low_bp"], w["bp"]) == (
        "2026-09-28", "2026-10-02", "54", "54", "52", "52")
    assert (w["long"], w["short"]) == ("4.10", "3.58")


def test_daily_series_in_columns():
    out = client.get("/api/series/daily", params={"name": "UST-10Y-CMT"}).json()
    assert out["start"] == "1962-01-01" and out["end"] == "2026-10-03"
    [ten] = out["series"]
    assert ten["dates"] == ["2026-09-30", "2026-10-01", "2026-10-02"]
    assert ten["percents"] == ["4.15", "4.12", "4.10"]
    assert ten["sources"] == [{"start": 0, "source": "UST-PAR"}, {"start": 2, "source": "H15-TCM"}]


def test_server_timing_header():
    r = client.get("/api/instruments")
    assert r.headers["server-timing"].startswith("api;dur=") and "upstream;dur=" in r.headers["server-timing"]
