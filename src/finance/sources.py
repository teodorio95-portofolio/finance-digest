"""Fetch and parse every numeric source. Pure parsers + one `collect` that feeds SeriesState.

BNR and ECB are official and keyless. ETF closes come from Yahoo's chart API
(unofficial, no key), with Stooq's CSV as the fallback per ticker. A bar for a
session that has not closed yet is dropped, so a run in the middle of the US
session never reports an intraday price as the day's close.
"""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from finance.series import SERIES, SeriesState

BNR_10DAYS = "https://www.bnr.ro/nbrfxrates10days.xml"
BNR_YEAR = "https://www.bnr.ro/files/xml/years/nbrfxrates{year}.xml"
ECB_FX = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
ECB_API = "https://data-api.ecb.europa.eu/service/data/{flow}/{key}?lastNObservations=400&format=csvdata"
# lastN, not a start date: the policy rate only has observations on the days it changed
ECB_SERIES = {
    "ecb-dfr": ("FM", "D.U2.EUR.4F.KR.DFR.LEV", "DFR"),
    "ecb-estr": ("EST", "B.EU000A2X2A25.WT", "ESTR"),
}
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d"
STOOQ = "https://stooq.com/q/d/l/?s={symbol}&i=d&d1={start}"
# Stooq has no session metadata: a bar dated today counts once this UTC time has passed
# (Xetra closes 17:30 CET/CEST = 15:30 or 16:30 UTC; NYSE 16:00 ET = 20:00 or 21:00 UTC).
CLOSED_AFTER_UTC = {"xetra": (16, 45), "us": (21, 15)}
BACKFILL_BELOW = 60  # fewer points than this: fetch a year, so the 30/90-day columns fill at once


class SourceError(ValueError):
    pass


def _local(tag: str) -> str:
    """'{http://www.bnr.ro/xsd}Cube' -> 'Cube'; namespace-agnostic on purpose (BNR's may change)."""
    return tag.rsplit("}", 1)[-1]


def parse_bnr(body: bytes, currencies: tuple[str, ...] = ("EUR", "USD")) -> dict[str, dict[str, float]]:
    """BNR XML (daily, 10 days or yearly) -> {"EUR": {"2026-10-16": 5.0812}, "USD": {...}}."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SourceError(f"BNR: not XML ({exc})") from exc
    out: dict[str, dict[str, float]] = {c: {} for c in currencies}
    for cube in root.iter():
        if _local(cube.tag) != "Cube" or "date" not in cube.attrib:
            continue
        for rate in cube:
            currency, value = rate.attrib.get("currency"), (rate.text or "").strip()
            if _local(rate.tag) != "Rate" or currency not in out or not value:
                continue
            multiplier = float(rate.attrib.get("multiplier", "1"))
            out[currency][cube.attrib["date"]] = float(value) / multiplier
    if not any(out.values()):
        raise SourceError("BNR: no rates in the document")
    return out


def parse_ecb_fx(body: bytes, currency: str = "USD") -> dict[str, float]:
    """eurofxref XML -> {"2026-10-16": 1.1253}: units of `currency` per 1 EUR."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SourceError(f"ECB fx: not XML ({exc})") from exc
    out: dict[str, float] = {}
    for cube in root.iter():
        if _local(cube.tag) != "Cube" or "time" not in cube.attrib:
            continue
        for child in cube:
            if child.attrib.get("currency") == currency:
                out[cube.attrib["time"]] = float(child.attrib["rate"])
    if not out:
        raise SourceError(f"ECB fx: no {currency} rates")
    return out


def parse_ecb_csv(body: bytes) -> dict[str, float]:
    """ECB Data API csvdata -> {TIME_PERIOD: OBS_VALUE}."""
    rows = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    if not rows.fieldnames or "TIME_PERIOD" not in rows.fieldnames or "OBS_VALUE" not in rows.fieldnames:
        raise SourceError(f"ECB API: unexpected header {rows.fieldnames}")
    out = {r["TIME_PERIOD"]: float(r["OBS_VALUE"]) for r in rows if (r.get("OBS_VALUE") or "").strip()}
    if not out:
        raise SourceError("ECB API: no observations")
    return out


def parse_yahoo(body: bytes, now: datetime) -> dict[str, float]:
    """Yahoo v8 chart JSON -> {exchange-local date: close}, closed sessions only."""
    try:
        chart = json.loads(body)["chart"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SourceError(f"Yahoo: unexpected body ({exc})") from exc
    if chart.get("error") or not chart.get("result"):
        raise SourceError(f"Yahoo: {chart.get('error') or 'empty result'}")
    result = chart["result"][0]
    meta = result.get("meta", {})
    offset = int(meta.get("gmtoffset", 0))
    session = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    stamps = result.get("timestamp") or []
    closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    epoch = int(now.timestamp())
    out: dict[str, float] = {}
    for i, (stamp, close) in enumerate(zip(stamps, closes, strict=False)):
        open_session = session and stamp >= session.get("start", 0) and epoch < session.get("end", 0)
        if open_session:
            continue  # today's session is still trading
        if close is None:
            # the last bar of a session that just closed often has no close yet; the meta price is it
            if i != len(stamps) - 1 or meta.get("regularMarketPrice") is None:
                continue
            close = meta["regularMarketPrice"]
        day = datetime.fromtimestamp(stamp + offset, UTC).date().isoformat()
        out[day] = float(close)
    if not out:
        raise SourceError("Yahoo: no closed sessions")
    return out


def parse_stooq(body: bytes, now: datetime, exchange: str) -> dict[str, float]:
    """Stooq daily CSV (Date,Open,High,Low,Close,Volume) -> {date: close}, closed sessions only."""
    text = body.decode("utf-8-sig", errors="replace").strip()
    rows = csv.DictReader(io.StringIO(text))
    if not rows.fieldnames or "Close" not in rows.fieldnames:
        raise SourceError(f"Stooq: unexpected body {text[:80]!r}")
    hour, minute = CLOSED_AFTER_UTC[exchange]
    today = now.date().isoformat()
    closed_today = (now.hour, now.minute) >= (hour, minute)
    out = {}
    for row in rows:
        day, close = row.get("Date") or "", (row.get("Close") or "").strip()
        if not close or (day == today and not closed_today):
            continue
        out[day] = float(close)
    if not out:
        raise SourceError("Stooq: no rows")
    return out


Fetch = Callable[[str], Any]  # Fetcher.get: returns a response with .content


def _bnr(get: Fetch, state: SeriesState, today: date) -> dict[str, dict[str, float]]:
    rates = parse_bnr(get(BNR_10DAYS).content)
    if len(state.points("EURRON")) < BACKFILL_BELOW:
        years = [today.year] + ([today.year - 1] if today.month <= 3 else [])
        for year in years:
            for currency, points in parse_bnr(get(BNR_YEAR.format(year=year)).content).items():
                rates[currency] = points | rates[currency]
    return {"EURRON": rates["EUR"], "USDRON": rates["USD"]}


def _etf(get: Fetch, series, state: SeriesState, now: datetime) -> tuple[dict[str, float], str]:
    sparse = len(state.points(series.id)) < BACKFILL_BELOW
    errors = []
    try:
        url = YAHOO.format(symbol=series.yahoo, range="1y" if sparse else "3mo")
        return parse_yahoo(get(url).content, now), "yahoo"
    except Exception as exc:  # any failure falls back to Stooq
        errors.append(f"yahoo: {type(exc).__name__}: {exc}")
    start = (now.date() - timedelta(days=400 if sparse else 120)).strftime("%Y%m%d")
    try:
        return parse_stooq(
            get(STOOQ.format(symbol=series.stooq, start=start)).content, now, series.exchange
        ), "stooq"
    except Exception as exc:
        errors.append(f"stooq: {type(exc).__name__}: {exc}")
    raise SourceError("; ".join(errors))


def collect(get: Fetch, state: SeriesState, now: datetime) -> list[dict[str, Any]]:
    """Fetch every source into `state`; returns the failures (each with `alert`)."""
    today = now.date()
    failed: list[dict[str, Any]] = []

    def run(source: str, fetch: Callable[[], tuple[dict[str, dict[str, float]], str | None]]) -> None:
        try:
            by_series, via = fetch()
        except Exception as exc:
            failure = state.record_failure(source, f"{type(exc).__name__}: {exc}", today)
            failed.append(failure)
            print(f"{source}: FAILED {failure['error']}")
            return
        for series_id, points in by_series.items():
            fresh = state.merge(series_id, points, today)
            print(f"{source}: {series_id} {len(points)} points, {fresh} new{f' via {via}' if via else ''}")
        state.record_success(source, today, via)

    run("bnr", lambda: (_bnr(get, state, today), None))
    run("ecb-fx", lambda: ({"EURUSD": parse_ecb_fx(get(ECB_FX).content)}, None))
    for source, (flow, key, series_id) in ECB_SERIES.items():
        url = ECB_API.format(flow=flow, key=key)
        run(source, lambda url=url, sid=series_id: ({sid: parse_ecb_csv(get(url).content)}, None))
    for series in SERIES:
        if series.kind == "etf":

            def fetch(series=series):
                points, via = _etf(get, series, state, now)
                return {series.id: points}, via

            run(series.source, fetch)
    return failed
