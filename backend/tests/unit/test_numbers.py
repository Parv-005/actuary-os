from app.utils.numbers import format_inr, format_pct, format_pp
from app.utils.time import iso, utcnow


def test_inr_grouping():
    assert format_inr(0) == "₹0"
    assert format_inr(1200000) == "₹12,00,000"
    assert format_inr(117480000) == "₹11,74,80,000"
    assert format_inr(3000000) == "₹30,00,000"
    assert format_inr(-4500) == "₹-4,500"
    assert format_inr(None) == "—"


def test_pct_pp():
    assert format_pct(0.673) == "67.3%"
    assert format_pct(None) == "—"
    assert format_pp(4.2) == "+4.2pp"
    assert format_pp(-2.1) == "-2.1pp"


def test_utcnow_aware():
    now = utcnow()
    assert now.tzinfo is not None
    assert "T" in iso(now)
