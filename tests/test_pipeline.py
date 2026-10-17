"""The workflow's command sequence end to end, on fixtures: collect -> merge-offers -> render."""

from __future__ import annotations

import json

from conftest import FakeFetcher, seeded_state
from test_offers import deposit, fidelis, raw
from test_sources import pages

from finance import cli
from finance.series import SeriesState

NOW = "2026-10-16T17:00:00+00:00"


def run(*argv):
    assert cli.main(["--now", NOW, *argv]) == 0


def test_daily_run_renders_queues_and_then_has_nothing_new(tmp_path, monkeypatch, fixture_bytes, capsys):
    out = tmp_path / "out"
    seeded_state(out).save()  # a week of history already on the data branch
    monkeypatch.setattr(cli, "Fetcher", lambda delay: FakeFetcher(pages(fixture_bytes)))
    report = tmp_path / "report.json"
    run("collect", "--state-dir", str(out / "state"), "--out", str(report))

    (tmp_path / "discover.txt").write_text(raw(fidelis(), deposit()))
    run(
        "merge-offers",
        "--raw",
        str(tmp_path / "discover.txt"),
        "--report",
        str(report),
        "--state-dir",
        str(out / "state"),
    )
    capsys.readouterr()
    run("should-send", str(report))
    assert capsys.readouterr().out.strip() == "true"

    why = {
        "tldr": ["Leul a fost stabil; S&P 500 în EUR a scăzut odată cu tehnologia americană."],
        "why": {
            "SXR8": {
                "text": "Scădere pe fondul rezultatelor slabe.",
                "sources": [{"title": "CNBC", "url": "https://www.cnbc.com/a"}],
            }
        },
        "news": [],
    }
    (tmp_path / "why.json").write_text(json.dumps(why, ensure_ascii=False))
    run(
        "render",
        "--report",
        str(report),
        "--prose",
        str(tmp_path / "why.json"),
        "--out-dir",
        str(out),
        "--link",
        "https://example.org",
    )

    html = (out / "digests" / "2026-10-16.html").read_text()
    text = (out / "digests" / "2026-10-16.txt").read_text()
    assert "5,3368" in html  # BNR EUR/RON from the fixture, Romanian decimals
    assert "Scădere pe fondul rezultatelor slabe." in html
    assert "Fidelis EUR 2 ani" in text and "5,25% pe an" in text
    assert "nu este consultanță de investiții" in text
    entry = json.loads((out / "outbox" / "2026-10-16.json").read_text())
    assert entry["status"] == "pending"
    assert entry["subject"].startswith("Bani în euro 16.10: EUR/RON 5,3368")

    # same sources again: every row was reported and no offer changed, so nothing to send
    run("collect", "--state-dir", str(out / "state"), "--out", str(report))
    run(
        "merge-offers",
        "--raw",
        str(tmp_path / "discover.txt"),
        "--report",
        str(report),
        "--state-dir",
        str(out / "state"),
    )
    capsys.readouterr()
    run("should-send", str(report))
    assert capsys.readouterr().out.strip() == "false"
    assert SeriesState.load(out / "state").data["reported"]["EURRON"] == "2026-10-16"


def test_weekly_run_lists_active_offers_and_survives_without_claude(tmp_path, monkeypatch, fixture_bytes):
    out = tmp_path / "out"
    seeded_state(out).save()
    monkeypatch.setattr(cli, "Fetcher", lambda delay: FakeFetcher(pages(fixture_bytes)))
    report = tmp_path / "report.json"
    run("collect", "--state-dir", str(out / "state"), "--out", str(report), "--mode", "weekly")
    (tmp_path / "discover.txt").write_text(raw(fidelis()))
    run(
        "merge-offers",
        "--raw",
        str(tmp_path / "discover.txt"),
        "--report",
        str(report),
        "--state-dir",
        str(out / "state"),
    )
    run("render", "--report", str(report), "--prose", str(tmp_path / "missing.json"), "--out-dir", str(out))

    text = (out / "digests" / "2026-10-16-saptamanal.txt").read_text()
    assert "CELE MAI BUNE OFERTE ACTIVE" in text
    assert "90 zile" in text
    assert "Explicațiile automate au lipsit azi" in text
    entry = json.loads((out / "outbox" / "2026-10-16-saptamanal.json").read_text())
    assert entry["subject"].startswith("Bani în euro: săptămâna 12.10–16.10")


def test_failed_discover_is_reported_not_fatal(tmp_path, monkeypatch, fixture_bytes):
    out = tmp_path / "out"
    monkeypatch.setattr(cli, "Fetcher", lambda delay: FakeFetcher(pages(fixture_bytes)))
    report = tmp_path / "report.json"
    run("collect", "--state-dir", str(out / "state"), "--out", str(report))
    run(
        "merge-offers",
        "--raw",
        str(tmp_path / "absent.txt"),
        "--report",
        str(report),
        "--state-dir",
        str(out / "state"),
    )
    assert json.loads(report.read_text())["offers_failed"] == "no JSON object in Claude's output"


def test_ntfy_payload(tmp_path, monkeypatch, capsys):
    digest = tmp_path / "d.json"
    digest.write_text(json.dumps({"subject": "Bani în euro 16.10: EUR/RON 5,0812", "tldr": ["unu", "doi"]}))
    monkeypatch.setenv("NTFY_TOPIC", " topic \n")
    run("ntfy", "--digest", str(digest), "--link", "https://example.org")
    payload = json.loads(capsys.readouterr().out)
    assert payload["topic"] == "topic"
    assert payload["message"] == "- unu\n- doi"
    assert payload["title"].startswith("Bani în euro")
