from __future__ import annotations

import json

from finance import prose

IDS = {"EURRON", "SXR8"}


def test_a_cause_without_a_source_is_dropped():
    raw = json.dumps(
        {
            "tldr": ["Leul a rămas stabil 📈"],
            "why": {
                "EURRON": {"text": "BNR a intervenit.", "sources": []},
                "SXR8": {
                    "text": "Rezultatele Nvidia au tras indicele în jos.",
                    "sources": [{"title": "Reuters", "url": "https://www.reuters.com/x"}],
                },
                "MADEUP": {"text": "x", "sources": [{"url": "https://a.b"}]},
            },
            "news": [
                {
                    "title": "BCE menține dobânzile",
                    "url": "https://www.ecb.europa.eu/press",
                    "date": "2026-10-16",
                },
                {"title": "no link"},
            ],
        }
    )
    out = prose.load(raw, IDS)
    assert out["tldr"] == ["Leul a rămas stabil"]
    assert list(out["why"]) == ["SXR8"]
    assert [n["title"] for n in out["news"]] == ["BCE menține dobânzile"]


def test_garbage_is_no_prose():
    assert prose.load("I could not finish.", IDS) == {}
    assert prose.load("{not json}", IDS) == {}
