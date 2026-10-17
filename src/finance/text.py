"""Text helpers shared by the parsers and the renderer: folding and Romanian formatting."""

from __future__ import annotations

import re
import unicodedata
from datetime import date

WEEKDAYS = ["Luni", "Marți", "Miercuri", "Joi", "Vineri", "Sâmbătă", "Duminică"]
MONTHS = [
    "ianuarie", "februarie", "martie", "aprilie", "mai", "iunie",
    "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie",
]  # fmt: skip
MINUS = "−"


def fold(text: str) -> str:
    """Lowercase and strip diacritics (both ș and ş), so keys match either spelling."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", fold(text))


def ro_date(day: date) -> str:
    """date(2026, 10, 16) -> 'Vineri, 16 octombrie 2026'."""
    return f"{WEEKDAYS[day.weekday()]}, {day.day} {MONTHS[day.month - 1]} {day.year}"


def short_date(iso: str | None) -> str | None:
    if not iso:
        return None
    day = date.fromisoformat(iso)
    return f"{day.day:02d}.{day.month:02d}.{day.year}"


def ro_number(value: float, decimals: int = 2) -> str:
    """5.08123 -> '5,0812' (4 decimals); 1234.5 -> '1.234,50'. Romanian separators."""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", " ").replace(".", ",").replace(" ", ".")


def ro_signed(value: float | None, decimals: int = 2, suffix: str = "%") -> str:
    """0.123 -> '+0,12%', -1.5 -> '−1,50%' (a real minus sign); None -> '–'."""
    if value is None:
        return "–"
    rounded = round(value, decimals)
    if rounded == 0:
        return f"0,{'0' * decimals}{suffix}" if decimals else f"0{suffix}"
    sign = "+" if rounded > 0 else MINUS
    return f"{sign}{ro_number(abs(rounded), decimals)}{suffix}"


def ro_count(n: int, singular: str, plural: str) -> str:
    """Romanian needs 'de' from 20 on: '1 ofertă', '3 oferte', '21 de oferte'."""
    if n == 1:
        return f"1 {singular}"
    if n % 100 >= 20 or (n >= 100 and n % 100 == 0):
        return f"{n} de {plural}"
    return f"{n} {plural}"


def clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
