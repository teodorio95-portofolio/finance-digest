from __future__ import annotations

from datetime import date

import pytest
from conftest import seeded_state

from finance import movers
from finance.series import BY_ID


def rows_by_id(state, mode="daily"):
    return {r["id"]: r for r in movers.build(state, mode)}


def test_daily_row_has_day_7_and_30_day_changes(tmp_path):
    rows = rows_by_id(seeded_state(tmp_path))
    sxr8 = rows["SXR8"]
    assert sxr8["date"] == "2026-10-16"
    assert set(sxr8["changes"]) == {"7", "30"}
    assert sxr8["day"] == pytest.approx((sxr8["value"] / (sxr8["value"] - 0.6) - 1) * 100)
    assert sxr8["fresh"]


def test_weekly_adds_90_days(tmp_path):
    rows = rows_by_id(seeded_state(tmp_path), "weekly")
    assert set(rows["EURRON"]["changes"]) == {"7", "30", "90"}


def test_split_shows_the_euro_effect_with_the_right_sign(tmp_path):
    # EUR/USD rises in the seeded history: a stronger euro costs a US index in euro
    split = rows_by_id(seeded_state(tmp_path))["SXR8"]["split"]["30"]
    assert split["usd"] > 0
    assert split["fx"] < 0


def test_a_fresh_rate_cut_is_in_points_and_notable(tmp_path):
    state = seeded_state(tmp_path)
    state.merge("DFR", {"2026-10-15": 1.75}, date(2026, 10, 16))
    dfr = {r["id"]: r for r in movers.build(state, "daily", date(2026, 10, 16))}["DFR"]
    assert dfr["day"] == pytest.approx(-0.25)
    assert dfr["notable"]
    assert dfr["last_change"] == {"date": "2026-10-15", "old": 2.0, "new": 1.75}


def test_rate_remembers_the_last_move(tmp_path):
    dfr = rows_by_id(seeded_state(tmp_path))["DFR"]
    assert dfr["last_change"] == {"date": "2025-06-11", "old": 2.25, "new": 2.0}


def test_an_old_rate_change_is_not_todays_move(tmp_path):
    state = seeded_state(tmp_path)  # never reported: the first run must not flag the 2025 cut
    dfr = {r["id"]: r for r in movers.build(state, "daily", date(2026, 10, 16))}["DFR"]
    assert dfr["day"] is None and not dfr["notable"]
    assert dfr["changes"] == {"7": 0.0, "30": 0.0}
    assert dfr["last_change"]["date"] == "2025-06-11"


def test_estr_has_no_last_change_line(tmp_path):
    assert "last_change" not in rows_by_id(seeded_state(tmp_path))["ESTR"]


def test_xeon_yield_is_annualised(tmp_path):
    xeon = rows_by_id(seeded_state(tmp_path))["XEON"]
    assert 0 < xeon["yield_30d"] < 10


def test_notable_needs_a_fresh_move_over_the_threshold(tmp_path):
    state = seeded_state(tmp_path)
    state.merge("EURRON", {"2026-10-19": 5.0 + 69 * 0.0002 + 0.03}, date(2026, 10, 19))  # ~+0.6%
    rows = rows_by_id(state)
    assert rows["EURRON"]["notable"]
    assert not rows["SXR8"]["notable"]  # +0.08% is under 1%
    movers.mark_reported(state, list(rows.values()))
    again = rows_by_id(state)
    assert not again["EURRON"]["fresh"] and not again["EURRON"]["notable"]


def test_value_at_picks_the_point_on_or_before():
    points = [("2026-10-14", 1.0), ("2026-10-16", 2.0)]
    assert movers.value_at(points, date(2026, 10, 15)) == ("2026-10-14", 1.0)
    assert movers.value_at(points, date(2026, 10, 13)) is None


def test_every_series_is_configured_with_a_source():
    assert all(s.source for s in BY_ID.values())
    assert all(s.isin and s.yahoo for s in BY_ID.values() if s.kind == "etf")
