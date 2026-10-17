"""What the digest tracks, and the one state file that keeps every price and rate.

out/state/series.json on the `data` branch:

    {"schema_version": 1,
     "points":   {"EURRON": {"2026-10-16": 5.0812, ...}, ...},   # date -> close or rate
     "sources":  {"bnr": {"failures", "last_error", "last_success", "last_alert", "via"}, ...},
     "reported": {"EURRON": "2026-10-16", ...}}                   # newest date already emailed

A failed fetch never touches `points`, so the next good run continues from the
last good values. Claude never writes this file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
KEEP_DAYS = 400  # enough for the 90-day column of the weekly digest, with room to spare
FAILURES_BEFORE_ALERT = 3
ALERT_EVERY = timedelta(days=7)


@dataclass(frozen=True)
class Series:
    id: str
    label: str
    kind: str  # fx | etf | rate
    source: str  # state key of the source that feeds it
    currency: str | None = None  # quote currency of a price
    decimals: int = 2
    # a daily move at or above this (in %, or in percentage points for a rate) asks Claude why
    threshold: float = 1.0
    yahoo: str | None = None
    stooq: str | None = None
    exchange: str | None = None  # xetra | us: when a bar dated today is complete
    tracks: str | None = None  # the USD-listed reference this EUR-listed ETF replicates
    note: str | None = None


SERIES: tuple[Series, ...] = (
    Series("EURRON", "EUR/RON", "fx", "bnr", "RON", decimals=4, threshold=0.3, note="curs BNR"),
    Series("USDRON", "USD/RON", "fx", "bnr", "RON", decimals=4, threshold=0.5, note="curs BNR"),
    Series(
        "EURUSD", "EUR/USD", "fx", "ecb-fx", "USD", decimals=4, threshold=0.5, note="curs de referință BCE"
    ),
    Series(
        "SXR8",
        "iShares Core S&P 500 (SXR8)",
        "etf",
        "etf:SXR8",
        "EUR",
        yahoo="SXR8.DE",
        stooq="sxr8.de",
        exchange="xetra",
        tracks="SPY",
        note="S&P 500, UCITS, în EUR pe Xetra",
    ),
    Series(
        "SXRV",
        "iShares Nasdaq 100 (SXRV)",
        "etf",
        "etf:SXRV",
        "EUR",
        yahoo="SXRV.DE",
        stooq="sxrv.de",
        exchange="xetra",
        tracks="QQQ",
        note="Nasdaq-100, UCITS, în EUR pe Xetra",
    ),
    Series(
        "VWCE",
        "Vanguard FTSE All-World (VWCE)",
        "etf",
        "etf:VWCE",
        "EUR",
        yahoo="VWCE.DE",
        stooq="vwce.de",
        exchange="xetra",
        note="acțiuni globale, UCITS, în EUR pe Xetra",
    ),
    Series(
        "XEON",
        "Xtrackers EUR Overnight (XEON)",
        "etf",
        "etf:XEON",
        "EUR",
        decimals=4,
        threshold=0.2,
        yahoo="XEON.DE",
        stooq="xeon.de",
        exchange="xetra",
        note="cash în EUR, randament aproape de €STR",
    ),
    Series(
        "SPY",
        "SPDR S&P 500 (SPY)",
        "etf",
        "etf:SPY",
        "USD",
        yahoo="SPY",
        stooq="spy.us",
        exchange="us",
        note="referință SUA, nu se poate cumpăra din UE",
    ),
    Series(
        "QQQ",
        "Invesco QQQ (Nasdaq-100)",
        "etf",
        "etf:QQQ",
        "USD",
        yahoo="QQQ",
        stooq="qqq.us",
        exchange="us",
        note="referință SUA, nu se poate cumpăra din UE",
    ),
    Series("DFR", "Rata facilității de depozit BCE", "rate", "ecb-dfr", threshold=0.01, decimals=2),
    Series("ESTR", "€STR (dobânda overnight în EUR)", "rate", "ecb-estr", threshold=0.05, decimals=3),
)
BY_ID = {s.id: s for s in SERIES}


class StateError(RuntimeError):
    pass


class SeriesState:
    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self.data = data

    @classmethod
    def load(cls, state_dir: Path) -> SeriesState:
        path = Path(state_dir) / "series.json"
        if path.exists():
            data = json.loads(path.read_text())
            if data.get("schema_version") != SCHEMA_VERSION:
                raise StateError(f"{path}: schema_version {data.get('schema_version')} != {SCHEMA_VERSION}")
        else:
            data = {"schema_version": SCHEMA_VERSION, "points": {}, "sources": {}, "reported": {}}
        return cls(path, data)

    def points(self, series_id: str) -> list[tuple[str, float]]:
        """Oldest first."""
        return sorted(self.data["points"].get(series_id, {}).items())

    def merge(self, series_id: str, points: dict[str, float], today: date) -> int:
        """Store points (a newer fetch overrides a date); returns how many dates are new."""
        known = self.data["points"].setdefault(series_id, {})
        fresh = sum(1 for day in points if day not in known)
        known.update({day: round(value, 6) for day, value in points.items()})
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        # the two newest old points stay: a policy rate only has points on the days it changed,
        # and "last change: from 2.25% to 2.00%" needs both sides of the last move
        for day in sorted(d for d in known if d < cutoff)[:-2]:
            del known[day]
        return fresh

    def _source(self, source: str) -> dict[str, Any]:
        return self.data["sources"].setdefault(
            source, {"failures": 0, "last_error": None, "last_success": None, "last_alert": None}
        )

    def record_success(self, source: str, today: date, via: str | None = None) -> None:
        entry = self._source(source)
        entry.update(failures=0, last_error=None, last_success=today.isoformat())
        if via:
            entry["via"] = via

    def record_failure(self, source: str, error: str, today: date) -> dict[str, Any]:
        """Count a failed fetch; the returned failure has `alert` set when this run should raise it."""
        entry = self._source(source)
        entry["failures"] += 1
        entry["last_error"] = error[:500]
        alert = False
        if entry["failures"] >= FAILURES_BEFORE_ALERT:
            last = entry.get("last_alert")
            if not last or today - date.fromisoformat(last) >= ALERT_EVERY:
                entry["last_alert"] = today.isoformat()
                alert = True
        return {"source": source, "error": error[:300], "streak": entry["failures"], "alert": alert}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
