from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from finance.series import SeriesState

FIXTURES = Path(__file__).parent / "fixtures"


class FakeFetcher:
    """Serves canned bodies by URL prefix; an unknown URL is a 404, like the real thing would be."""

    def __init__(self, pages: dict[str, bytes | str]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url: str) -> httpx.Response:
        self.calls.append(url)
        request = httpx.Request("GET", url)
        body = next((v for k, v in self.pages.items() if url.startswith(k)), None)
        if body is None:
            raise httpx.HTTPStatusError("404", request=request, response=httpx.Response(404, request=request))
        return httpx.Response(200, content=body.encode() if isinstance(body, str) else body, request=request)

    def close(self) -> None:
        pass


def weekdays(end: date, count: int) -> list[str]:
    """`count` business days ending on `end`, oldest first."""
    out, day = [], end
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day -= timedelta(days=1)
    return out[::-1]


def seeded_state(tmp_path: Path, end: date = date(2026, 10, 16), days: int = 70) -> SeriesState:
    """A state with smooth synthetic history for every tracked series."""
    state = SeriesState.load(tmp_path / "state")
    dates = weekdays(end, days)
    shapes = {
        "EURRON": (5.0, 0.0002),
        "USDRON": (4.6, -0.0005),
        "EURUSD": (1.09, 0.0005),
        "SXR8": (700.0, 0.6),
        "SXRV": (1100.0, 1.2),
        "VWCE": (140.0, 0.08),
        "XEON": (148.0, 0.008),
        "ESTR": (1.92, 0.0),
    }
    for series_id, (start, step) in shapes.items():
        state.merge(series_id, {d: start + i * step for i, d in enumerate(dates)}, end)
    state.merge("DFR", {"2025-04-23": 2.25, "2025-06-11": 2.0}, end)
    return state


@pytest.fixture
def fixture_bytes():
    return lambda name: (FIXTURES / name).read_bytes()


@pytest.fixture
def fixture_text():
    return lambda name: (FIXTURES / name).read_text()
