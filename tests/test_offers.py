from __future__ import annotations

import json
from datetime import date

import pytest

from finance import offers

TODAY = date(2026, 10, 16)


def fidelis(**over):
    base = {
        "kind": "titluri_stat",
        "issuer": "Ministerul Finanțelor",
        "product": "Fidelis EUR 2 ani (emisiunea din octombrie 2026)",
        "currency": "EUR",
        "rate_pct": 5.25,
        "term_months": 24,
        "valid_until": "2026-10-23",
        "min_amount": "100 EUR",
        "conditions": "Dobânda este neimpozabilă.",
        "url": "https://mfinante.gov.ro/fidelis",
        "source_date": "2026-10-10",
    }
    return base | over


def deposit(**over):
    base = {
        "kind": "depozit",
        "issuer": "Libra Internet Bank",
        "product": "Depozit EUR 12 luni",
        "currency": "EUR",
        "rate_pct": 2.6,
        "term_months": 12,
        "valid_until": None,
        "url": "https://www.librabank.ro/depozite",
    }
    return base | over


def raw(*items, chatter="Here is the JSON:\n"):
    return chatter + json.dumps({"offers": list(items)}, ensure_ascii=False)


def test_parse_keeps_valid_offers_and_drops_the_rest():
    found = offers.parse(
        raw(
            fidelis(),
            deposit(),
            deposit(url="http://insecure.example/x"),
            deposit(rate_pct=60),
            deposit(rate_pct="2.5"),
            deposit(kind="actiuni"),
            deposit(currency="GBP"),
            fidelis(valid_until="2026-10-01"),  # subscription closed
            deposit(term_months=True),
            "not an object",
        ),
        TODAY,
    )
    assert [o["kind"] for o in found] == ["titluri_stat", "depozit"]
    assert found[0]["key"] == "fidelis|EUR|24|2026-10"


def test_parse_without_json_raises():
    with pytest.raises(offers.OffersError):
        offers.parse("Sorry, I could not search today.", TODAY)


def test_key_ignores_wording_and_issuer_spelling():
    a = offers.parse(raw(deposit(product="Depozit la termen 12 luni EUR")), TODAY)[0]
    b = offers.parse(raw(deposit(issuer="LIBRA internet bank")), TODAY)[0]
    assert a["key"] == b["key"]


def test_merge_reports_new_then_changed_then_nothing(tmp_path):
    state = offers.OffersState.load(tmp_path)
    first = state.merge(offers.parse(raw(fidelis(), deposit()), TODAY), TODAY)
    assert [o["status"] for o in first] == ["nou", "nou"]
    second = state.merge(offers.parse(raw(fidelis(), deposit(rate_pct=2.8)), TODAY), TODAY)
    assert [(o["status"], o.get("old_rate")) for o in second] == [("schimbat", 2.6)]
    assert state.merge(offers.parse(raw(deposit(rate_pct=2.8)), TODAY), TODAY) == []
    state.save()
    reloaded = offers.OffersState.load(tmp_path)
    assert reloaded.items["depozit|librainternetbank|EUR|12"]["history"][0] == {
        "date": "2026-10-16",
        "old": 2.6,
        "new": 2.8,
    }


def test_active_drops_closed_and_stale_offers_and_puts_eur_first(tmp_path):
    state = offers.OffersState.load(tmp_path)
    ron = deposit(issuer="CEC Bank", currency="RON", rate_pct=6.0)
    state.merge(offers.parse(raw(fidelis(), deposit(), ron), TODAY), TODAY)
    assert [o["currency"] for o in state.active(TODAY)] == ["EUR", "EUR", "RON"]
    assert state.active(TODAY)[0]["rate_pct"] == 5.25
    later = date(2026, 10, 24)  # Fidelis subscription closed on the 23rd
    assert all(o["kind"] != "titluri_stat" for o in state.active(later))
    assert state.active(date(2026, 11, 30)) == []  # nothing re-seen for three weeks
