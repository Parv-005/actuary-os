"""Unit tests for analytics/quality.py (§6.4/§22): threshold boundaries,
coverage gate, outlier rule, small-sample, rename, shifts, concentration."""
from app.analytics import quality as q


def test_recon_threshold_boundaries():
    assert q.classify_recon(0.4, 0.5, 2.0) == ("INFO", "PASS")
    assert q.classify_recon(0.5, 0.5, 2.0) == ("INFO", "PASS")  # inclusive
    assert q.classify_recon(0.6, 0.5, 2.0) == ("WARNING", "WARNING")
    assert q.classify_recon(2.0, 0.5, 2.0) == ("WARNING", "WARNING")  # inclusive
    assert q.classify_recon(2.1, 0.5, 2.0) == ("BLOCKER", "BLOCKER")
    # seeded storyline: premium -2.10% blocks, claims -1.17% warns
    assert q.classify_recon(-2.10, 0.5, 2.0) == ("BLOCKER", "BLOCKER")
    assert q.classify_recon(-1.17, 0.5, 2.0) == ("WARNING", "WARNING")
    assert q.recon_diff_pct(117480000, 120000000) == (
        (117480000 - 120000000) / 120000000 * 100)
    assert q.recon_diff_pct(100, 0) is None


def test_coverage_gate_boundary():
    in90 = ["2026-09-01"] * 90 + ["2026-08-01"] * 10
    assert q.coverage_pct(in90, "2026-09")["blocked"] is False  # 90% passes
    just_under = ["2026-09-01"] * 89 + ["2026-08-01"] * 11 + [""]
    cov = q.coverage_pct(just_under, "2026-09")
    assert cov["blocked"] is True and cov["total"] == 100  # blanks excluded
    assert q.coverage_pct([], "2026-09")["blocked"] is True


def test_outlier_flags_extreme_only():
    amounts = [100.0] * 200 + [10000.0]
    ids = [f"C{i}" for i in range(201)]
    out = q.outlier_flags(amounts, ids)
    assert out["flagged_total"] == 1
    assert out["flagged"][0]["claim_id"] == "C200"
    # uniform book: nothing above threshold
    flat = q.outlier_flags([500.0] * 20, ids[:20])
    assert flat["flagged_total"] == 0
    # too few rows: skipped, never blocks
    assert q.outlier_flags([1.0, 2.0], ["a", "b"])["threshold"] is None


def test_small_sample_cells():
    small = q.small_sample_cells(
        {("Commercial", "Marine Cargo"): 4, ("Motor", "Motor"): 64},
        {("Commercial", "Marine Cargo"): 3, ("Motor", "Motor"): 1800})
    assert len(small) == 1 and small[0]["cell"] == ["Commercial", "Marine Cargo"]
    assert q.small_sample_cells({("A", "B"): 20}, {("A", "B"): 10}) == []


def test_rename_candidates():
    ren = q.rename_candidates({"Commercial", "Motor"}, {"Commercial", "Auto"})
    assert ren["vanished"] == ["Motor"] and ren["appeared"] == ["Auto"]
    assert q.rename_candidates({"A"}, {"A"}) == {"vanished": [], "appeared": []}


def test_shifts_and_concentration():
    assert q.lr_shift_pp(0.673, 0.631) == (
        (0.673 - 0.631) * 100)  # +4.2pp storyline
    assert q.volume_change_pct(6000, 5658) == (342 / 5658 * 100)
    assert q.volume_change_pct(6000, 0) is None
    assert q.concentration({"South": 0.375, "North": 0.24})["warn"] is False
    assert q.concentration({"South": 0.55, "North": 0.2}) == {
        "top": "South", "share": 0.55, "warn": True}
