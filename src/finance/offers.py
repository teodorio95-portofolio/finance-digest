"""Validate and track savings offers that Claude found with WebSearch. Claude only prints JSON; this decides.

Expected shape (anything else is dropped, never trusted):

    {"offers": [{"kind", "issuer", "product", "currency", "rate_pct", "term_months",
                 "valid_until", "min_amount", "conditions", "url", "source_date"}]}

out/state/offers.json keeps every offer by key, with its rate history. An offer
is "nou" the first time its key is seen and "schimbat" when its rate moves.
There is no silent baseline: on the first run the current offers are the news.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from finance.text import clip, fold, slug

SCHEMA_VERSION = 1
KINDS = {"titluri_stat": "titluri de stat", "depozit": "depozit la termen", "economii": "cont de economii"}
CURRENCIES = ("EUR", "RON", "USD")
MAX_OFFERS = 20
MAX_RATE = 25.0  # a "rate" above this is a parsing mistake, not an offer
ACTIVE_FOR = timedelta(days=21)  # not seen again in three weeks: no longer listed as active
HISTORY_LIMIT = 20
PROGRAMS = ("fidelis", "tezaur")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class OffersError(ValueError):
    pass


def extract_json(raw: str) -> dict[str, Any]:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise OffersError("no JSON object in Claude's output")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise OffersError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise OffersError("top level is not an object")
    return data


def _text(entry: dict, key: str, limit: int) -> str | None:
    value = entry.get(key)
    return clip(value, limit) if isinstance(value, str) and value.strip() else None


def _date(entry: dict, key: str) -> str | None:
    value = entry.get(key)
    return value if isinstance(value, str) and DATE.match(value) else None


def key(offer: dict[str, Any]) -> str:
    """Stable across runs even when Claude words the product differently."""
    term = offer["term_months"] if offer["term_months"] is not None else "x"
    if offer["kind"] == "titluri_stat":
        name = fold(offer["product"])
        program = next((p for p in PROGRAMS if p in name), slug(offer["issuer"]))
        # one Fidelis or Tezaur issue per month: the subscription deadline tells issues apart
        issue = (offer["valid_until"] or offer["source_date"] or "")[:7] or "x"
        return f"{program}|{offer['currency']}|{term}|{issue}"
    return f"{offer['kind']}|{slug(offer['issuer'])}|{offer['currency']}|{term}"


def parse(raw: str, today: date) -> list[dict[str, Any]]:
    entries = extract_json(raw).get("offers") or []
    if not isinstance(entries, list):
        raise OffersError("offers is not a list")
    kept: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        url, rate = entry.get("url"), entry.get("rate_pct")
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        if isinstance(rate, bool) or not isinstance(rate, int | float) or not 0 < rate <= MAX_RATE:
            continue
        term = entry.get("term_months")
        if isinstance(term, bool) or not (term is None or (isinstance(term, int) and 0 <= term <= 360)):
            continue
        offer = {
            "kind": entry.get("kind"),
            "issuer": _text(entry, "issuer", 60),
            "product": _text(entry, "product", 100),
            "currency": entry.get("currency"),
            "rate_pct": round(float(rate), 3),
            "term_months": term,
            "valid_until": _date(entry, "valid_until"),
            "min_amount": _text(entry, "min_amount", 60),
            "conditions": _text(entry, "conditions", 220),
            "url": url.strip(),
            "source_date": _date(entry, "source_date"),
        }
        if offer["kind"] not in KINDS or offer["currency"] not in CURRENCIES:
            continue
        if not (offer["issuer"] and offer["product"]):
            continue
        if offer["valid_until"] and offer["valid_until"] < today.isoformat():
            continue  # subscription already closed
        offer["key"] = key(offer)
        kept.setdefault(offer["key"], offer)  # first mention wins; Claude lists the best source first
    return list(kept.values())[:MAX_OFFERS]


class OffersState:
    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self.data = data

    @classmethod
    def load(cls, state_dir: Path) -> OffersState:
        path = Path(state_dir) / "offers.json"
        data = (
            json.loads(path.read_text()) if path.exists() else {"schema_version": SCHEMA_VERSION, "items": {}}
        )
        if data.get("schema_version") != SCHEMA_VERSION:
            raise OffersError(f"{path}: schema_version {data.get('schema_version')} != {SCHEMA_VERSION}")
        return cls(path, data)

    @property
    def items(self) -> dict[str, dict[str, Any]]:
        return self.data["items"]

    def merge(self, offers: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
        """Store offers; return the news: each with `status` "nou" or "schimbat" (+ `old_rate`)."""
        news = []
        for offer in offers:
            known = self.items.get(offer["key"])
            stored = {k: v for k, v in offer.items() if k != "key"}
            if known is None:
                self.items[offer["key"]] = {
                    **stored,
                    "first_seen": today.isoformat(),
                    "last_seen": today.isoformat(),
                }
                news.append({**offer, "status": "nou"})
                continue
            if known["rate_pct"] != offer["rate_pct"]:
                history = known.setdefault("history", [])
                history.append(
                    {"date": today.isoformat(), "old": known["rate_pct"], "new": offer["rate_pct"]}
                )
                del history[:-HISTORY_LIMIT]
                news.append({**offer, "status": "schimbat", "old_rate": known["rate_pct"]})
            known.update(stored, last_seen=today.isoformat())
        return news

    def active(self, today: date) -> list[dict[str, Any]]:
        """Offers seen in the last three weeks whose subscription has not closed, best rate first."""
        cutoff = (today - ACTIVE_FOR).isoformat()
        out = [
            {"key": k, **v}
            for k, v in self.items.items()
            if v["last_seen"] >= cutoff
            and (not v.get("valid_until") or v["valid_until"] >= today.isoformat())
        ]
        return sorted(out, key=lambda o: (o["currency"] != "EUR", -o["rate_pct"]))

    def known_lines(self, today: date, limit: int = 25) -> str:
        """For the discover prompt: what is already known, so Claude reuses names and checks rates."""
        lines = [
            f"- {o['kind']} · {o['issuer']} · {o['product']} · {o['currency']} · {o['rate_pct']}% · "
            f"{o['term_months'] if o['term_months'] is not None else '?'} luni · {o['url']}"
            for o in self.active(today)[:limit]
        ]
        return "\n".join(lines) or "- (none yet)"

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
