"""finance: deterministic half of the digest. Claude never writes state; this does.

finance collect      --state-dir out/state --out report.json [--mode daily|weekly]
finance prompt       prompt/discover.md --state-dir out/state --report report.json   # filled prompt on stdout
finance merge-offers --raw discover.txt --report report.json --state-dir out/state
finance should-send  report.json [--force]                 # prints true/false
finance render       --report report.json [--prose why.json] --out-dir out [--link URL]
finance send         --root out                            # every pending outbox entry
finance ntfy         --digest out/digests/<stem>.json --link URL   # ntfy JSON on stdout
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from finance import movers, outbox, prose, sources
from finance.email import render
from finance.http import Fetcher
from finance.offers import OffersError, OffersState
from finance.offers import parse as parse_offers
from finance.series import SeriesState

NTFY_MAX = 400


def now_utc(args: argparse.Namespace) -> datetime:
    return (
        datetime.fromisoformat(args.now).astimezone(UTC) if getattr(args, "now", None) else datetime.now(UTC)
    )


def stem(report: dict) -> str:
    return f"{report['date']}-saptamanal" if report["mode"] == "weekly" else report["date"]


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def write_json(path: str | Path, data: dict) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def cmd_collect(args: argparse.Namespace) -> int:
    now = now_utc(args)
    state = SeriesState.load(args.state_dir)
    fetcher = Fetcher(delay=args.delay)
    try:
        failed = sources.collect(fetcher.get, state, now)
    finally:
        fetcher.close()
    state.save()
    report = {
        "date": now.date().isoformat(),
        "mode": args.mode,
        "rows": movers.build(state, args.mode, now.date()),
        "failed": [{k: v for k, v in f.items() if k != "alert"} for f in failed],
        "alerts": [{k: v for k, v in f.items() if k != "alert"} for f in failed if f["alert"]],
        "offers_new": [],
        "offers_active": [],
    }
    write_json(args.out, report)
    fresh = [r["id"] for r in report["rows"] if r["fresh"]]
    print(f"collect: {len(report['rows'])} series, fresh: {', '.join(fresh) or 'none'}; {len(failed)} failed")
    return 0


def prompt_data(report: dict) -> str:
    """The explain prompt's facts: the numbers, rounded, without the bookkeeping."""
    keep = (
        "id",
        "label",
        "kind",
        "currency",
        "date",
        "value",
        "day",
        "changes",
        "split",
        "notable",
        "fresh",
        "note",
    )
    rows = [
        {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items() if k in keep}
        for r in report["rows"]
    ]
    for r in rows:
        r["changes"] = {h: round(v, 3) for h, v in r.get("changes", {}).items()}
        if "split" in r:
            r["split"] = {h: {k: round(v, 3) for k, v in s.items()} for h, s in r["split"].items()}
    offers = [
        {
            k: o.get(k)
            for k in (
                "status",
                "kind",
                "issuer",
                "product",
                "currency",
                "rate_pct",
                "old_rate",
                "term_months",
            )
        }
        for o in report.get("offers_new", [])
    ]
    data = {"date": report["date"], "mode": report["mode"], "rows": rows, "offers_new": offers}
    return json.dumps(data, ensure_ascii=False, indent=1)


def cmd_prompt(args: argparse.Namespace) -> int:
    today = now_utc(args).date()
    text = Path(args.template).read_text().replace("{{TODAY}}", today.isoformat())
    if "{{KNOWN_OFFERS}}" in text:
        text = text.replace("{{KNOWN_OFFERS}}", OffersState.load(args.state_dir).known_lines(today))
    if "{{DATA}}" in text or "{{MODE}}" in text:
        report = read_json(args.report)
        text = text.replace("{{DATA}}", prompt_data(report)).replace("{{MODE}}", report["mode"])
    print(text)
    return 0


def cmd_merge_offers(args: argparse.Namespace) -> int:
    today = now_utc(args).date()
    report = read_json(args.report)
    state = OffersState.load(args.state_dir)
    raw = Path(args.raw).read_text() if Path(args.raw).exists() else ""
    try:
        found = parse_offers(raw, today)
    except OffersError as exc:
        report["offers_failed"] = str(exc)[:200]
        print(f"offers: {exc}", file=sys.stderr)
    else:
        report["offers_new"] = state.merge(found, today)
        state.save()
        print(f"offers: {len(found)} valid, {len(report['offers_new'])} new or changed")
    report["offers_active"] = state.active(today)
    write_json(args.report, report)
    return 0


def has_news(report: dict) -> bool:
    return (
        report["mode"] == "weekly"
        or bool(report.get("offers_new"))
        or any(r["fresh"] for r in report["rows"])
    )


def cmd_should_send(args: argparse.Namespace) -> int:
    print("true" if args.force or has_news(read_json(args.report)) else "false")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    report = read_json(args.report)
    raw = Path(args.prose).read_text() if args.prose and Path(args.prose).exists() else ""
    explained = prose.load(raw, {r["id"] for r in report["rows"]})
    if raw and not explained:
        print("render: Claude's explanations were malformed; sending numbers only", file=sys.stderr)
    digest = render.build(report, explained, args.link)
    html, text = render.html(digest), render.text(digest)
    if args.preview:
        Path(args.preview).write_text(html)
        Path(args.preview).with_suffix(".txt").write_text(text)
        print(f"preview: {args.preview}")
        return 0
    out = Path(args.out_dir)
    folder = out / "digests"
    folder.mkdir(parents=True, exist_ok=True)
    name = stem(report)
    (folder / f"{name}.html").write_text(html)
    (folder / f"{name}.txt").write_text(text)
    write_json(
        folder / f"{name}.json", {"subject": digest["subject"], "tldr": digest["tldr"], "report": report}
    )
    outbox.enqueue(out, date.fromisoformat(report["date"]), digest["subject"], stem=name)
    state = SeriesState.load(out / "state")
    movers.mark_reported(state, report["rows"])
    state.save()
    print(f"render: {name} queued: {digest['subject']}")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    return outbox.send_pending(Path(args.root), args.to)


def cmd_ntfy(args: argparse.Namespace) -> int:
    """TL;DR as the push text, a tap opens the archive; JSON so the title can carry diacritics."""
    digest = read_json(args.digest)
    message = "\n".join(f"- {line}" for line in digest["tldr"])[:NTFY_MAX] or "Digest nou."
    payload = {
        "topic": os.environ["NTFY_TOPIC"].strip(),
        "title": digest["subject"][:120],
        "message": message,
        "tags": ["euro"],
        "markdown": True,
        "click": args.link,
        "actions": [{"action": "view", "label": "Deschide arhiva", "url": args.link}],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finance", description="finance-digest: deterministic half")
    parser.add_argument("--now", help="pretend it is this ISO datetime (tests, replays)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect")
    p.add_argument("--state-dir", type=Path, required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--mode", choices=("daily", "weekly"), default="daily")
    p.add_argument("--delay", type=float, default=1.0)
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("prompt")
    p.add_argument("template")
    p.add_argument("--state-dir", type=Path, default=Path("out/state"))
    p.add_argument("--report")
    p.set_defaults(func=cmd_prompt)

    p = sub.add_parser("merge-offers")
    p.add_argument("--raw", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--state-dir", type=Path, required=True)
    p.set_defaults(func=cmd_merge_offers)

    p = sub.add_parser("should-send")
    p.add_argument("report")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_should_send)

    p = sub.add_parser("render")
    p.add_argument("--report", required=True)
    p.add_argument("--prose")
    p.add_argument("--out-dir", default="out")
    p.add_argument("--link")
    p.add_argument("--preview", help="write HTML (+ .txt) here instead of queueing")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("send")
    p.add_argument("--root", default="out")
    p.add_argument("--to")
    p.set_defaults(func=cmd_send)

    p = sub.add_parser("ntfy")
    p.add_argument("--digest", required=True)
    p.add_argument("--link", required=True)
    p.set_defaults(func=cmd_ntfy)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
