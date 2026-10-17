"""Turn report.json (+ Claude's explanations) into the digest: data dict, HTML and plain text.

Every number comes from the report (BNR, ECB, Yahoo/Stooq); Claude only supplies
the TL;DR, the "why" next to a row and the news list, each with its links. CSS
lives in digest.css and is inlined with css-inline (Gmail drops most of <style>).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import css_inline
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from finance.offers import KINDS
from finance.prose import no_emoji
from finance.text import ro_count, ro_date, ro_number, ro_signed, short_date

HERE = Path(__file__).parent
HORIZON_LABELS = {"7": "7 zile", "30": "30 zile", "90": "90 zile"}
SUBJECT_ETFS = (("SXR8", "S&P 500"), ("SXRV", "Nasdaq"))
MAX_ACTIVE = 10


def tone(value: float | None) -> str:
    if value is None or round(value, 2) == 0:
        return ""
    return "pos" if value > 0 else "neg"


def ddmm(iso: str, year_of: str | None = None) -> str:
    """'16.10'; with the year when it is not the report's year ('11.06.2025')."""
    day = date.fromisoformat(iso)
    text = f"{day.day:02d}.{day.month:02d}"
    return f"{text}.{day.year}" if year_of and year_of[:4] != iso[:4] else text


def value_text(r: dict[str, Any]) -> str:
    if r["kind"] == "rate":
        return f"{ro_number(r['value'], r['decimals'])}%"
    unit = {"EUR": " €", "USD": " $", "RON": " lei"}.get(r["currency"] or "", "")
    return f"{ro_number(r['value'], r['decimals'])}{unit if r['kind'] == 'etf' else ''}"


def change_text(r: dict[str, Any], value: float | None) -> str:
    if r["kind"] == "rate":
        return ro_signed(value, 2, " pp")
    return ro_signed(value, 2)


def row(r: dict[str, Any], why: dict[str, Any], mode: str, today: str) -> dict[str, Any]:
    horizons = ("7", "30", "90") if mode == "weekly" else ("7", "30")
    out = {
        "id": r["id"],
        "label": r["label"],
        "note": r.get("note"),
        "value": value_text(r),
        "date": ddmm(r["date"], today),
        "stale": not r["fresh"] and mode == "daily" and r["kind"] != "rate",
        "day": change_text(r, r.get("day")),
        "day_tone": tone(r.get("day")) if r["kind"] == "etf" else "",
        "changes": [
            {
                "label": HORIZON_LABELS[h],
                "text": change_text(r, r["changes"].get(h)),
                "tone": tone(r["changes"].get(h)) if r["kind"] == "etf" else "",
            }
            for h in horizons
        ],
        "extra": [],
        "why": why.get(r["id"]),
        "unexplained": r.get("notable") and r["id"] not in why,
    }
    for h, split in sorted((r.get("split") or {}).items(), key=lambda kv: int(kv[0])):
        out["extra"].append(
            f"{HORIZON_LABELS[h]}: indicele în USD {ro_signed(split['usd'])}, efectul cursului EUR/USD "
            f"{ro_signed(split['fx'])} (aproximativ)"
        )
    if r.get("yield_30d") is not None:
        out["extra"].append(f"Randament anualizat pe ultimele 30 de zile: ~{ro_number(r['yield_30d'], 2)}%")
    if r.get("last_change"):
        lc = r["last_change"]
        out["extra"].append(
            f"Ultima schimbare: {short_date(lc['date'])}, de la {ro_number(lc['old'], r['decimals'])}% "
            f"la {ro_number(lc['new'], r['decimals'])}%"
        )
    return out


def term_text(months: int | None) -> str:
    if months is None:
        return "termen nespecificat"
    if months == 0:
        return "la vedere"
    if months % 12 == 0:
        return ro_count(months // 12, "an", "ani")
    return ro_count(months, "lună", "luni")


def offer(o: dict[str, Any]) -> dict[str, Any]:
    facts = [KINDS[o["kind"]], term_text(o.get("term_months")), o["currency"]]
    if o.get("min_amount"):
        facts.append(f"minim {o['min_amount']}")
    if o.get("valid_until"):
        facts.append(f"subscriere până la {short_date(o['valid_until'])}")
    status = None
    if o.get("status") == "nou":
        status = "nou"
    elif o.get("status") == "schimbat":
        status = f"dobândă schimbată: {ro_number(o['old_rate'], 2)}% → {ro_number(o['rate_pct'], 2)}%"
    return {
        "title": no_emoji(f"{o['issuer']} · {o['product']}"),
        "rate": f"{ro_number(o['rate_pct'], 2)}% pe an",
        "facts": " · ".join(facts),
        "conditions": o.get("conditions"),
        "url": o["url"],
        "status": status,
        "seen": short_date(o.get("source_date")),
    }


def counts(report: dict[str, Any]) -> list[str]:
    news = report.get("offers_new", [])
    new = sum(1 for o in news if o["status"] == "nou")
    changed = len(news) - new
    notable = sum(1 for r in report["rows"] if r.get("notable"))
    parts = [
        (new, "ofertă nouă", "oferte noi"),
        (changed, "dobândă schimbată", "dobânzi schimbate"),
        (notable, "mișcare notabilă", "mișcări notabile"),
    ]
    return [ro_count(n, singular, plural) for n, singular, plural in parts if n]


def subject(report: dict[str, Any], chips: list[str]) -> str:
    rows = {r["id"]: r for r in report["rows"]}
    weekly = report["mode"] == "weekly"
    parts = []
    if eur := rows.get("EURRON"):
        moved = eur["changes"].get("7") if weekly else eur.get("day")
        parts.append(f"EUR/RON {ro_number(eur['value'], 4)} ({ro_signed(moved)})")
    for series_id, name in SUBJECT_ETFS:
        if r := rows.get(series_id):
            moved = r["changes"].get("7") if weekly else r.get("day")
            parts.append(f"{name} {ro_signed(moved)}")
    parts += [c for c in chips if "ofert" in c or "dobân" in c]
    if report.get("alerts"):
        parts.append("surse degradate")
    day = date.fromisoformat(report["date"])
    if weekly:
        start = day - timedelta(days=day.weekday())  # the week's Monday
        head = f"Bani în euro: săptămâna {ddmm(start.isoformat())}–{ddmm(report['date'])}"
    else:
        head = f"Bani în euro {ddmm(report['date'])}"
    return f"{head}: {' · '.join(parts)}" if parts else head


def fallback_tldr(report: dict[str, Any], chips: list[str]) -> list[str]:
    rows = {r["id"]: r for r in report["rows"]}
    weekly = report["mode"] == "weekly"
    span = "în ultimele 7 zile" if weekly else "față de cotația precedentă"
    bullets = []
    if eur := rows.get("EURRON"):
        moved = eur["changes"].get("7") if weekly else eur.get("day")
        bullets.append(f"EUR/RON {ro_number(eur['value'], 4)} lei ({ro_signed(moved)} {span}).")
    etfs = [
        f"{name} {ro_signed(r['changes'].get('7') if weekly else r.get('day'))}"
        for series_id, name in SUBJECT_ETFS
        if (r := rows.get(series_id))
    ]
    if etfs:
        bullets.append(f"ETF-uri în EUR: {', '.join(etfs)} {span}.")
    if chips:
        bullets.append(f"Azi: {', '.join(chips)}.")
    return bullets or ["Nicio cotație nouă în sursele urmărite."]


def build(report: dict[str, Any], prose: dict[str, Any], link: str | None = None) -> dict[str, Any]:
    why = prose.get("why", {})
    mode = report["mode"]
    chips = counts(report)
    day = date.fromisoformat(report["date"])
    rows = [row(r, why, mode, report["date"]) for r in report["rows"]]
    by_kind = {
        kind: [r for r, raw in zip(rows, report["rows"], strict=True) if raw["kind"] == kind]
        for kind in ("fx", "etf", "rate")
    }
    return {
        "date": report["date"],
        "date_human": ro_date(day),
        "mode": mode,
        "subject": subject(report, chips),
        "chips": chips,
        "tldr": prose.get("tldr") or fallback_tldr(report, chips),
        "fx": by_kind["fx"],
        "etf": by_kind["etf"],
        "rates": by_kind["rate"],
        "horizons": [HORIZON_LABELS[h] for h in (("7", "30", "90") if mode == "weekly" else ("7", "30"))],
        "offers_new": [offer(o) for o in report.get("offers_new", [])],
        "offers_active": [offer(o) for o in report.get("offers_active", [])[:MAX_ACTIVE]]
        if mode == "weekly"
        else [],
        "news": [{**n, "date": short_date(n.get("date"))} for n in prose.get("news", [])],
        "explained": bool(prose.get("why") or prose.get("tldr")),
        "alerts": report.get("alerts", []),
        "failed": report.get("failed", []),
        "offers_failed": report.get("offers_failed"),
        "link": link,
    }


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(HERE / "templates"),
        autoescape=lambda name: bool(name and name.endswith(".html.j2")),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def html(digest: dict[str, Any]) -> str:
    css = (HERE / "digest.css").read_text()
    page = _env().get_template("digest.html.j2").render(d=digest, css=css)
    return css_inline.CSSInliner(keep_style_tags=True).inline(page)


def text(digest: dict[str, Any]) -> str:
    return _env().get_template("digest.txt.j2").render(d=digest).strip() + "\n"
