"""Phase 0 probe: fetch every source from a GitHub runner, parse it, and report.

Writes raw bodies to probe-out/ (uploaded as a short-lived artifact, the source of
test fixtures) and a Markdown table to $GITHUB_STEP_SUMMARY: HTTP status, size,
time, and how many points the real parser got, so a 200 block page is not
mistaken for success.

    uv run python scripts/probe.py
"""

from __future__ import annotations

import os
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from finance import sources
from finance.http import UA
from finance.series import SERIES

OUT = Path("probe-out")


def targets(now: datetime) -> list[tuple[str, str, object]]:
    out: list[tuple[str, str, object]] = [
        ("bnr-10days", sources.BNR_10DAYS, lambda b: sources.parse_bnr(b)["EUR"]),
        ("bnr-year", sources.BNR_YEAR.format(year=now.year), lambda b: sources.parse_bnr(b)["EUR"]),
        ("ecb-fx", sources.ECB_FX, sources.parse_ecb_fx),
    ]
    for name, (flow, key, _) in sources.ECB_SERIES.items():
        out.append((name, sources.ECB_API.format(flow=flow, key=key), sources.parse_ecb_csv))
    stooq_start = (now.date() - timedelta(days=30)).strftime("%Y%m%d")
    for s in SERIES:
        if s.kind != "etf":
            continue
        out.append(
            (
                f"yahoo-{s.id}",
                sources.YAHOO.format(symbol=s.yahoo, range="1mo"),
                lambda b: sources.parse_yahoo(b, now),
            )
        )
        out.append(
            (
                f"stooq-{s.id}",
                sources.STOOQ.format(symbol=s.stooq, start=stooq_start),
                lambda b, ex=s.exchange: sources.parse_stooq(b, now, ex),
            )
        )
    return out


def main() -> None:
    OUT.mkdir(exist_ok=True)
    now = datetime.now(UTC)
    rows = ["| source | HTTP | size | time | parsed |", "|---|---|---|---|---|"]
    client = httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
    for name, url, parse in targets(now):
        started = time.monotonic()
        try:
            resp = client.get(url)
        except httpx.HTTPError as exc:
            rows.append(f"| {name} | ERR | - | - | {type(exc).__name__}: {exc} |")
            continue
        took = time.monotonic() - started
        ext = {"xml": "xml", "json": "json", "csv": "csv"}.get(
            next((k for k in ("xml", "json", "csv") if k in resp.headers.get("content-type", "")), ""), "txt"
        )
        (OUT / f"{name}.{ext}").write_bytes(resp.content)
        try:
            points = parse(resp.content)  # type: ignore[operator]
            last = max(points)
            parsed = f"{len(points)} points, last {last} = {points[last]}"
        except Exception as exc:
            parsed = f"FAIL {type(exc).__name__}: {str(exc)[:120]}"
        size = len(resp.content) / 1024
        rows.append(f"| {name} | {resp.status_code} | {size:.0f} KiB | {took:.1f}s | {parsed} |")
        time.sleep(1)  # ~1 req/s, be polite
    client.close()
    text = f"## Probe {date.today().isoformat()} ({now:%H:%M} UTC)\n\n" + "\n".join(rows) + "\n"
    print(text)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as fh:
            fh.write(text)


if __name__ == "__main__":
    main()
