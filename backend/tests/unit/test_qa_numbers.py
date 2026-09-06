"""Unit tests for services/qa_verify.py (§6.9/§22): injected mismatch
caught, rounding-tolerance pass, hallucinated numeral caught, money match,
years/versions/CP-refs skipped."""
from app.services.qa_verify import extract_numerals, verify_prose


def test_injected_mismatch_caught():
    # plan §16 example: report "11.1%" vs metric 11.7
    mismatches = verify_prose(["Loss ratio 11.1% for the quarter."], [11.7])
    assert len(mismatches) == 1
    assert mismatches[0]["prose"] == "11.1%"
    assert mismatches[0]["nearest"] == 11.7
    assert 'report "11.1%"' in mismatches[0]["message"]


def test_rounding_tolerance_pass():
    assert verify_prose(["Loss ratio 67.3% (up 4.2pp)."], [67.29, 4.199]) == []
    # ratio displayed as % matches the raw ratio value x100
    assert verify_prose(["Loss ratio 67.3%."], [0.673]) == []
    # 1-decimal display rounding of 60.84
    assert verify_prose(["Share 60.8%."], [60.84]) == []


def test_hallucinated_number_caught():
    mismatches = verify_prose(
        ["Loss ratio 67.3% but severity exploded 99.9%."],
        [0.673, 0.05, 292387.0])
    assert len(mismatches) == 1
    assert mismatches[0]["prose"] == "99.9%"


def test_money_match_and_mismatch():
    assert verify_prose(["Premium ₹1,20,000."], [120000.0]) == []
    assert verify_prose(["Premium ₹117.5M."], [117480000.0]) == []
    bad = verify_prose(["Premium ₹120.0M."], [117480000.0])
    assert len(bad) == 1 and bad[0]["prose"] == "₹120.0M"


def test_labels_skipped():
    # years, versions, checkpoint refs and periods are labels, not values
    assert extract_numerals("September 2026 report, v3.1, CP-4, period "
                            "2026-09.") == []
    assert verify_prose(["Review of 2026-09 under v3.1 (CP-4)."],
                        [0.5]) == []


def test_counts_match_exactly():
    assert verify_prose(["280 claims across 6000 policies."],
                        [280.0, 6000.0]) == []
    assert len(verify_prose(["281 claims."], [280.0])) == 1
