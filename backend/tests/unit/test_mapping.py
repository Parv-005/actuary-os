"""Mapping + parsing unit tests (§6.3/§22): aliases, fuzzy, rupee, dates."""
import pandas as pd

from app.mappings import resolve_columns
from app.services.datasets import (
    enrich_claims_region,
    find_key_collisions,
    parse_date,
    parse_money,
    parse_number,
    parse_period,
)


def test_exact_alias_hits():
    m = resolve_columns(["claim_id", "policy_id", "claim_amt"], "claims")
    assert m.mapped["claim_id"] == "claim_id"
    assert m.mapped["claim_amt"] == "incurred_amount"
    assert not m.fuzzy


def test_fuzzy_threshold():
    # 0.90 similarity -> auto-mapped
    m = resolve_columns(["incurred_amt"], "claims")
    assert m.fuzzy.get("incurred_amt") == "incurred_amount"
    # garbage -> unmapped with suggestions
    m2 = resolve_columns(["zzzqqqxxx"], "claims")
    assert "zzzqqqxxx" in m2.unmapped


def test_override_and_ignored():
    m = resolve_columns(["BizClass", "junk_col"], "claims",
                        overrides={"BizClass": "product"}, ignored=["junk_col"])
    assert m.mapped["BizClass"] == "product"
    assert m.ignored == ["junk_col"]


def test_rupee_parsing():
    assert parse_money("₹1,20,000") == (120000.0, "ok")
    assert parse_money("Rs. 500.50") == (500.5, "ok")
    assert parse_money("-1,234") == (-1234.0, "ok")
    assert parse_money("") == (None, "missing")
    assert parse_money("N/A") == (None, "rejected")
    assert parse_money("1.2.3") == (None, "rejected")
    assert parse_number("42", as_int=True) == (42.0, "ok")


def test_date_and_period_parsing():
    assert parse_date("2026-09-05") == ("2026-09-05", "ok")
    assert parse_date("05-09-2026") == ("2026-09-05", "ok")
    assert parse_date("not-a-date") == (None, "rejected")
    assert parse_date("") == (None, "missing")
    assert parse_period("2026-09") == ("2026-09", "ok")
    assert parse_period("2026/09/01") == ("2026-09", "ok")
    assert parse_period("Sep-26") == (None, "rejected")


def test_collisions_detected_not_merged():
    df = pd.DataFrame([
        {"claim_id": "C1", "incurred_amount": 100.0},
        {"claim_id": "C1", "incurred_amount": 999.0},
        {"claim_id": "C2", "incurred_amount": 50.0},
    ])
    res = find_key_collisions(df, ["claim_id"], ["incurred_amount"])
    assert res["count"] == 2  # both C1 rows flagged
    assert res["samples"][0]["key"] == ["C1"]


def test_region_fix_fills_missing():
    claims = pd.DataFrame([
        {"claim_id": "C1", "policy_id": "P1", "region": ""},
        {"claim_id": "C2", "policy_id": "P9", "region": "North"},
    ])
    exposure = pd.DataFrame([
        {"policy_id": "P1", "region": "South"},
    ])
    out, fix = enrich_claims_region(claims, exposure)
    assert fix["filled"] == 1
    assert out.iloc[0]["region"] == "South"
    assert out.iloc[1]["region"] == "North"
