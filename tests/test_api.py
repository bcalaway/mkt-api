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
        "date": "2026-10-02", "value": "0.041", "percent": "4.10", "source": "H15-TCM"}
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
