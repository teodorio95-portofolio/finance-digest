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
    # raw runner output, 17.10.2026: the https namespace of curs.bnr.ro, one line, 10 banking days
    rates = sources.parse_bnr(fixture_bytes("bnr_10days.xml"), ("EUR", "USD", "HUF"))
    assert len(rates["EUR"]) == 10
    assert rates["EUR"]["2026-10-16"] == 5.3368 and rates["EUR"]["2026-10-15"] == 5.337
    assert rates["USD"]["2026-10-16"] == 4.743
    assert rates["HUF"]["2026-10-16"] < 0.05  # quoted per 100 HUF


def test_bnr_ignores_the_namespace():
    body = b'<DataSet xmlns="urn:something-new"><Body><Cube date="2026-10-16"><Rate currency="EUR">5.1</Rate></Cube></Body></DataSet>'
    assert sources.parse_bnr(body)["EUR"] == {"2026-10-16": 5.1}


def test_bnr_html_page_is_an_error():
    with pytest.raises(sources.SourceError):
        sources.parse_bnr(b"<html><body>Curs XML</body>")


def test_ecb_fx_reads_usd(fixture_bytes):
    rates = sources.parse_ecb_fx(fixture_bytes("ecb_90d.xml"))
    assert len(rates) == 65
    assert rates["2026-10-16"] == 1.1253 and rates["2026-10-15"] == 1.1208


def test_ecb_csv(fixture_bytes):
    # raw rows around the September 2026 hike and the last 20 days (the full answer is 400 rows)
    rates = sources.parse_ecb_csv(fixture_bytes("ecb_dfr.csv"))
    assert rates["2026-09-15"] == 2.25 and rates["2026-09-16"] == 2.5
    assert max(rates) == "2026-10-17"


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


MORNING_AFTER = datetime(2026, 10, 17, 5, 30, tzinfo=UTC)


def test_justetf_skips_weekends_and_waits_for_the_end_of_day(fixture_bytes):
    body = fixture_bytes("justetf_sxr8.json")
    points = sources.parse_justetf(body, MORNING_AFTER)
    assert len(points) == 22
    assert "2026-10-10" not in points and "2026-10-11" not in points  # Saturday, Sunday
    assert points["2026-10-16"] == 740.44
    assert max(sources.parse_justetf(body, AFTER_XETRA)) == "2026-10-15"  # before 21:00 UTC


def test_justetf_drops_a_holiday_carried_forward():
    body = json.dumps(
        {
            "series": [
                {"date": "2026-12-24", "value": {"raw": 10.0}},
                {"date": "2026-12-25", "value": {"raw": 10.0}},
                {"date": "2026-12-28", "value": {"raw": 10.2}},
            ]
        }
    ).encode()
    assert sources.parse_justetf(body, MORNING_AFTER) == {"2026-12-24": 10.0, "2026-12-28": 10.2}


def test_justetf_error_page():
    with pytest.raises(sources.SourceError):
        sources.parse_justetf(b"<!DOCTYPE html><html>Just a moment...</html>", MORNING_AFTER)


def pages(fixture_bytes, **extra):
    base = {
        sources.BNR_10DAYS: fixture_bytes("bnr_10days.xml"),
        "https://curs.bnr.ro/files/xml/years/": fixture_bytes("bnr_10days.xml"),
        sources.ECB_FX: fixture_bytes("ecb_90d.xml"),
        "https://data-api.ecb.europa.eu/service/data/FM/": fixture_bytes("ecb_dfr.csv"),
        "https://data-api.ecb.europa.eu/service/data/EST/": fixture_bytes("ecb_dfr.csv"),
        "https://www.justetf.com/api/": fixture_bytes("justetf_sxr8.json"),
        "https://query1.finance.yahoo.com/": fixture_bytes("yahoo_sxr8.json"),
    }
    return base | extra


def test_collect_fills_every_series(tmp_path, fixture_bytes):
    state = SeriesState.load(tmp_path)
    fetcher = FakeFetcher(pages(fixture_bytes))
    failed = sources.collect(fetcher.get, state, AFTER_XETRA)
    assert failed == []
    assert state.points("EURRON")[-1] == ("2026-10-16", 5.3368)
    assert state.points("EURUSD")[-1] == ("2026-10-16", 1.1253)
    assert state.points("DFR")[-1] == ("2026-10-17", 2.5)
    assert state.points("SXR8")[-1] == ("2026-10-15", 744.98)  # the 16th closes at 21:00 UTC
    assert state.data["sources"]["etf:SXR8"]["via"] == "justetf"
    # sparse history: the yearly BNR file and 400 days of ETF history are fetched
    assert any("/years/nbrfxrates2026.xml" in url for url in fetcher.calls)
    assert any("dateFrom=2025-09-11" in url for url in fetcher.calls)


def test_collect_falls_back_to_yahoo_and_records_it(tmp_path, fixture_bytes):
    state = SeriesState.load(tmp_path)
    blocked = pages(fixture_bytes)
    del blocked["https://www.justetf.com/api/"]
    assert sources.collect(FakeFetcher(blocked).get, state, AFTER_XETRA) == []
    assert state.points("SXR8")[-1] == ("2026-10-16", 740.78)
    assert state.data["sources"]["etf:SXR8"]["via"] == "yahoo"


def test_both_etf_sources_failing_is_one_failure_with_both_errors(tmp_path, fixture_bytes):
    state = SeriesState.load(tmp_path)
    blocked = pages(fixture_bytes)
    del blocked["https://www.justetf.com/api/"], blocked["https://query1.finance.yahoo.com/"]
    failed = sources.collect(FakeFetcher(blocked).get, state, AFTER_XETRA)
    assert {f["source"] for f in failed} == {"etf:SXR8", "etf:SXRV", "etf:VWCE", "etf:XEON"}
    assert "justetf:" in failed[0]["error"] and "yahoo:" in failed[0]["error"]


def test_a_failing_source_keeps_its_points_and_counts(tmp_path, fixture_bytes):
    state = SeriesState.load(tmp_path)
    sources.collect(FakeFetcher(pages(fixture_bytes)).get, state, AFTER_XETRA)
    broken = pages(fixture_bytes)
    del broken[sources.BNR_10DAYS]
    for _ in range(2):
        failed = sources.collect(FakeFetcher(broken).get, state, AFTER_XETRA)
        assert [f["source"] for f in failed] == ["bnr"]
        assert not failed[0]["alert"]
    failed = sources.collect(FakeFetcher(broken).get, state, AFTER_XETRA)
    assert failed[0]["alert"] and failed[0]["streak"] == 3
    assert state.points("EURRON")[-1] == ("2026-10-16", 5.3368)
    # the alert is not repeated the next day
    failed = sources.collect(FakeFetcher(broken).get, state, datetime(2026, 10, 17, 17, tzinfo=UTC))
    assert not failed[0]["alert"]


def test_merge_prunes_old_points_but_keeps_the_last_move(tmp_path):
    state = SeriesState.load(tmp_path)
    old = {"2023-03-22": 3.0, "2025-04-23": 2.25, "2025-06-11": 2.0}
    state.merge("DFR", old | {"2026-10-16": 2.0}, date(2026, 10, 16))
    assert [d for d, _ in state.points("DFR")] == ["2025-04-23", "2025-06-11", "2026-10-16"]
