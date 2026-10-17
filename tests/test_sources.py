from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from conftest import FakeFetcher

from finance import sources
from finance.series import SeriesState

AFTER_XETRA = datetime(2026, 10, 16, 17, 0, tzinfo=UTC)
DURING_XETRA = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def test_bnr_reads_eur_and_usd_and_applies_multiplier(fixture_bytes):
    rates = sources.parse_bnr(fixture_bytes("bnr_10days_sample.xml"), ("EUR", "USD", "HUF"))
    assert rates["EUR"] == {"2026-10-16": 5.0812, "2026-10-15": 5.0790}
    assert rates["USD"]["2026-10-16"] == 4.5154
    assert rates["HUF"]["2026-10-16"] == pytest.approx(0.01295)


def test_bnr_ignores_the_namespace():
    body = b'<DataSet xmlns="urn:something-new"><Body><Cube date="2026-10-16"><Rate currency="EUR">5.1</Rate></Cube></Body></DataSet>'
    assert sources.parse_bnr(body)["EUR"] == {"2026-10-16": 5.1}


def test_bnr_html_page_is_an_error():
    with pytest.raises(sources.SourceError):
        sources.parse_bnr(b"<html><body>Curs XML</body>")


def test_ecb_fx_reads_usd(fixture_bytes):
    assert sources.parse_ecb_fx(fixture_bytes("ecb_90d_sample.xml")) == {
        "2026-10-16": 1.1253,
        "2026-10-15": 1.1212,
    }


def test_ecb_csv(fixture_bytes):
    assert sources.parse_ecb_csv(fixture_bytes("ecb_dfr_sample.csv")) == {
        "2025-04-23": 2.25,
        "2025-06-11": 2.0,
    }


def test_ecb_csv_rejects_an_error_page():
    with pytest.raises(sources.SourceError):
        sources.parse_ecb_csv(b"<html>503 Service Unavailable</html>")


def test_yahoo_after_the_close_uses_the_meta_price_for_the_null_bar(fixture_bytes):
    points = sources.parse_yahoo(fixture_bytes("yahoo_sxr8.json"), AFTER_XETRA)
    assert list(points) == ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
    assert points["2026-10-16"] == 740.78
    assert points["2026-10-15"] == pytest.approx(744.48)


def test_yahoo_during_the_session_drops_today(fixture_bytes):
    points = sources.parse_yahoo(fixture_bytes("yahoo_sxr8.json"), DURING_XETRA)
    assert max(points) == "2026-10-15"


def test_yahoo_error_body():
    body = json.dumps({"chart": {"result": None, "error": {"code": "Not Found"}}}).encode()
    with pytest.raises(sources.SourceError):
        sources.parse_yahoo(body, AFTER_XETRA)


def test_stooq_drops_a_us_bar_before_the_us_close(fixture_bytes):
    body = fixture_bytes("stooq_spy_sample.csv")
    assert max(sources.parse_stooq(body, AFTER_XETRA, "us")) == "2026-10-15"
    late = datetime(2026, 10, 16, 21, 30, tzinfo=UTC)
    assert sources.parse_stooq(body, late, "us")["2026-10-16"] == 658.9


def test_stooq_apikey_message_is_an_error():
    with pytest.raises(sources.SourceError):
        sources.parse_stooq(b"Get your apikey", AFTER_XETRA, "us")


def pages(fixture_bytes, **extra):
    base = {
        "https://www.bnr.ro/nbrfxrates10days.xml": fixture_bytes("bnr_10days_sample.xml"),
        "https://www.bnr.ro/files/xml/years/": fixture_bytes("bnr_10days_sample.xml"),
        sources.ECB_FX: fixture_bytes("ecb_90d_sample.xml"),
        "https://data-api.ecb.europa.eu/service/data/FM/": fixture_bytes("ecb_dfr_sample.csv"),
        "https://data-api.ecb.europa.eu/service/data/EST/": fixture_bytes("ecb_dfr_sample.csv"),
        "https://query1.finance.yahoo.com/": fixture_bytes("yahoo_sxr8.json"),
    }
    return base | extra


def test_collect_fills_every_series(tmp_path, fixture_bytes):
    state = SeriesState.load(tmp_path)
    fetcher = FakeFetcher(pages(fixture_bytes))
    failed = sources.collect(fetcher.get, state, AFTER_XETRA)
    assert failed == []
    assert state.points("EURRON")[-1] == ("2026-10-16", 5.0812)
    assert state.points("EURUSD")[-1] == ("2026-10-16", 1.1253)
    assert state.points("DFR")[-1] == ("2025-06-11", 2.0)
    assert state.points("SXR8")[-1] == ("2026-10-16", 740.78)
    # sparse history: the yearly BNR file and a one-year Yahoo range are fetched
    assert any("/years/nbrfxrates2026.xml" in url for url in fetcher.calls)
    assert any("range=1y" in url for url in fetcher.calls)


def test_collect_falls_back_to_stooq_and_records_it(tmp_path, fixture_bytes):
    state = SeriesState.load(tmp_path)
    blocked = pages(fixture_bytes)
    del blocked["https://query1.finance.yahoo.com/"]
    blocked["https://stooq.com/"] = fixture_bytes("stooq_spy_sample.csv")
    assert sources.collect(FakeFetcher(blocked).get, state, AFTER_XETRA) == []
    assert state.points("SPY")[-1] == ("2026-10-15", 661.05)
    assert state.data["sources"]["etf:SPY"]["via"] == "stooq"


def test_a_failing_source_keeps_its_points_and_counts(tmp_path, fixture_bytes):
    state = SeriesState.load(tmp_path)
    sources.collect(FakeFetcher(pages(fixture_bytes)).get, state, AFTER_XETRA)
    broken = pages(fixture_bytes)
    del broken["https://www.bnr.ro/nbrfxrates10days.xml"]
    for _ in range(2):
        failed = sources.collect(FakeFetcher(broken).get, state, AFTER_XETRA)
        assert [f["source"] for f in failed] == ["bnr"]
        assert not failed[0]["alert"]
    failed = sources.collect(FakeFetcher(broken).get, state, AFTER_XETRA)
    assert failed[0]["alert"] and failed[0]["streak"] == 3
    assert state.points("EURRON")[-1] == ("2026-10-16", 5.0812)
    # the alert is not repeated the next day
    failed = sources.collect(FakeFetcher(broken).get, state, datetime(2026, 10, 17, 17, tzinfo=UTC))
    assert not failed[0]["alert"]


def test_merge_prunes_old_points_but_keeps_the_last_move(tmp_path):
    state = SeriesState.load(tmp_path)
    old = {"2023-03-22": 3.0, "2025-04-23": 2.25, "2025-06-11": 2.0}
    state.merge("DFR", old | {"2026-10-16": 2.0}, date(2026, 10, 16))
    assert [d for d, _ in state.points("DFR")] == ["2025-04-23", "2025-06-11", "2026-10-16"]
