"""Validate Claude's explanations of the day's moves. Every claim needs a link, or it is dropped.

Expected shape:

    {"tldr": ["..."],
     "why":  {"<series id>": {"text": "...", "sources": [{"title": "...", "url": "https://..."}]}},
     "news": [{"title", "url", "source", "date", "note"}]}

Numbers in the email always come from the report; Claude's text sits next to them.
"""

from __future__ import annotations

import json
import re
from typing import Any

from finance.text import clip

# Pictographs and their joiners; the digest has no emoji (same rule as the other digests).
EMOJI = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U00002b00-\U00002bff\U0000fe0f\U0000200d\U000020e3]"
)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_TLDR, MAX_NEWS, MAX_SOURCES = 3, 6, 3


def no_emoji(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", EMOJI.sub("", text)).strip()


def _link(entry: Any) -> dict[str, str] | None:
    if not isinstance(entry, dict):
        return None
    url, title = entry.get("url"), entry.get("title")
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    return {
        "url": url.strip(),
        "title": clip(no_emoji(title), 140) if isinstance(title, str) and title.strip() else url,
    }


def load(raw: str, series_ids: set[str]) -> dict[str, Any]:
    """Only well-formed parts survive; a malformed answer is the same as no answer."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    tldr = [clip(no_emoji(t), 220) for t in data.get("tldr") or [] if isinstance(t, str) and t.strip()]
    why: dict[str, dict[str, Any]] = {}
    for series_id, entry in (data.get("why") or {}).items() if isinstance(data.get("why"), dict) else ():
        if series_id not in series_ids or not isinstance(entry, dict):
            continue
        text = entry.get("text")
        sources = [s for s in map(_link, entry.get("sources") or []) if s][:MAX_SOURCES]
        if isinstance(text, str) and text.strip() and sources:  # no source, no cause
            why[series_id] = {"text": clip(no_emoji(text), 400), "sources": sources}
    news = []
    for entry in data.get("news") or [] if isinstance(data.get("news"), list) else ():
        if not (link := _link(entry)):
            continue
        day = entry.get("date")
        note = entry.get("note")
        source = entry.get("source")
        news.append(
            link
            | {
                "source": clip(source, 40) if isinstance(source, str) and source.strip() else None,
                "date": day if isinstance(day, str) and DATE.match(day) else None,
                "note": clip(no_emoji(note), 240) if isinstance(note, str) and note.strip() else None,
            }
        )
    return {"tldr": tldr[:MAX_TLDR], "why": why, "news": news[:MAX_NEWS]}
