"""Turn the stored points into the digest's numbers: changes per horizon, and what moved enough to explain.

Changes are percent for prices and FX, percentage points for rates. Each row
reports its own latest date: BNR, Xetra and NYSE close at different times and on
different holidays, so one "today" for everything would be wrong.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from finance.series import SERIES, Series, SeriesState

HORIZONS = {"daily": (7, 30), "weekly": (7, 30, 90)}
WEEKLY_FACTOR = 2.5  # a week's move must be this many daily thresholds to count as notable
YIELD_WINDOW = 30
RATE_NEWS_DAYS = 7  # an ECB move older than this is history, not today's news


def value_at(points: list[tuple[str, float]], day: date) -> tuple[str, float] | None:
    """The newest point on or before `day` (points are oldest first)."""
    target = day.isoformat()
    best = None
    for point in points:
        if point[0] > target:
            break
        best = point
    return best


def change(series: Series, old: float, new: float) -> float:
    return new - old if series.kind == "rate" else (new / old - 1) * 100


def horizon_change(
    series: Series, points: list[tuple[str, float]], days: int, anchor: date | None = None
) -> tuple[float, str] | None:
    """Change from the point `days` before `anchor` (default: the latest point); None when history is short.

    A rate is a step function anchored on today: the ECB rate has points only on the days it changed.
    """
    latest_day, latest = points[-1]
    start = value_at(points, (anchor or date.fromisoformat(latest_day)) - timedelta(days=days))
    if start is None or (start[0] == latest_day and anchor is None):
        return None
    return change(series, start[1], latest), start[0]


def last_change(points: list[tuple[str, float]]) -> dict[str, Any] | None:
    """When a rate last moved: {"date", "old", "new"}."""
    for (_, before), (day, after) in zip(reversed(points[:-1]), reversed(points[1:]), strict=True):
        if round(before, 6) != round(after, 6):
            return {"date": day, "old": before, "new": after}
    return None


def decomposition(
    state: SeriesState, series: Series, start_day: str, latest_day: str
) -> dict[str, float] | None:
    """Split a EUR-quoted US-index ETF's move into the index in USD and the EUR/USD effect.

    The ETF's value in USD is its EUR price times EUR/USD (ECB fixing), so the split needs no
    US-listed quote. Approximate: Xetra closes at 17:30 CET, the ECB fixing is at 14:15.
    """
    if not series.us_index:
        return None
    own, fx = state.points(series.id), state.points("EURUSD")
    picks = [value_at(p, date.fromisoformat(d)) for p in (own, fx) for d in (start_day, latest_day)]
    if any(p is None for p in picks) or not fx:
        return None
    eur_start, eur_end, fx_start, fx_end = (p[1] for p in picks)  # type: ignore[index]
    # a stronger euro (EUR/USD up) lowers what a US index is worth in euro
    return {
        "usd": (eur_end * fx_end / (eur_start * fx_start) - 1) * 100,
        "fx": (fx_start / fx_end - 1) * 100,
    }


def annual_yield(points: list[tuple[str, float]], days: int = YIELD_WINDOW) -> float | None:
    """Annualised growth over ~`days` (for a money-market ETF this is its interest rate)."""
    latest_day, latest = points[-1]
    start = value_at(points, date.fromisoformat(latest_day) - timedelta(days=days))
    if start is None:
        return None
    span = (date.fromisoformat(latest_day) - date.fromisoformat(start[0])).days
    if span < days // 2:
        return None
    return ((latest / start[1]) ** (365 / span) - 1) * 100


def row(state: SeriesState, series: Series, mode: str, today: date | None = None) -> dict[str, Any] | None:
    points = state.points(series.id)
    if not points:
        return None
    latest_day, latest = points[-1]
    fresh = latest_day > state.data["reported"].get(series.id, "")
    is_rate = series.kind == "rate"
    day = change(series, points[-2][1], latest) if len(points) > 1 else None
    if is_rate:
        # a rate's "day" is its last move, shown only while that move is recent news
        anchor = today or date.fromisoformat(latest_day)
        recent = date.fromisoformat(latest_day) >= anchor - timedelta(days=RATE_NEWS_DAYS)
        day = day if fresh and recent else None
    out: dict[str, Any] = {
        "id": series.id,
        "label": series.label,
        "kind": series.kind,
        "currency": series.currency,
        "decimals": series.decimals,
        "note": series.note,
        "us_index": series.us_index,
        "date": latest_day,
        "value": latest,
        "fresh": fresh,
        "day": day,
        "changes": {},
    }
    for days in HORIZONS[mode]:
        if (moved := horizon_change(series, points, days, today if is_rate else None)) is not None:
            out["changes"][str(days)] = moved[0]
            if days in (7, 30) and (split := decomposition(state, series, moved[1], latest_day)):
                out.setdefault("split", {})[str(days)] = split
    if series.policy:
        out["last_change"] = last_change(points)
    if series.id == "XEON":
        out["yield_30d"] = annual_yield(points)
    if mode == "weekly":
        week = out["changes"].get("7")
        out["notable"] = week is not None and abs(week) >= series.threshold * WEEKLY_FACTOR
    else:
        out["notable"] = bool(out["fresh"] and out["day"] is not None and abs(out["day"]) >= series.threshold)
    return out


def build(state: SeriesState, mode: str = "daily", today: date | None = None) -> list[dict[str, Any]]:
    return [r for s in SERIES if (r := row(state, s, mode, today))]


def mark_reported(state: SeriesState, rows: list[dict[str, Any]]) -> None:
    for r in rows:
        state.data["reported"][r["id"]] = r["date"]
