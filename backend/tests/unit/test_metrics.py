"""Unit tests for analytics/metrics.py (§6.5/§22): undefined handling,
exposure-based frequency, AvE sign conventions, flags, prev/delta wiring."""
import pandas as pd

from app.analytics import metrics as m


def _frames():
    claims = pd.DataFrame({
        "product": ["Commercial", "Commercial", "Empty"],
        "segment": ["Construction", "Construction", "Ghost"],
        "region": ["South", "South", "North"],
        "incurred_amount": [800, 200, 50],
        "claim_id": ["C1", "C2", "C3"],
    })
    premium = pd.DataFrame({
        "product": ["Commercial", "Empty", "ZeroPrem", "VoidOnly"],
        "segment": ["Construction", "Ghost", "Void", "None"],
        "region": ["South", "North", "West", "East"],
        "earned_premium": [1000, 500, 0, 400],
    })
    exposure = pd.DataFrame({
        "product": ["Commercial", "Empty", "ZeroPrem", "VoidOnly"],
        "segment": ["Construction", "Ghost", "Void", "None"],
        "region": ["South", "North", "West", "East"],
        "earned_exposure_units": [2.0, 1.0, 4.0, 2.0],
        "active_policies": [5, 30, 30, 30],
    })
    return claims, premium, exposure


def _by_key(rows, key, dims):
    return next(r for r in rows
                if r["metric_key"] == key and r["dimensions"] == dims)


def test_zero_premium_null_with_reason():
    rows = m.build_rows(m.aggregate_frames(*_frames()), period="2026-09",
                        dv_ids=[])
    zp = _by_key(rows, "loss_ratio",
                 {"product": "ZeroPrem", "segment": "Void", "region": "West"})
    assert zp["value"] is None
    assert zp["undefined_reason"] == "premium base is zero"
    # frequency still defined (exposure base is non-zero)
    zf = _by_key(rows, "claim_frequency",
                 {"product": "ZeroPrem", "segment": "Void", "region": "West"})
    assert zf["value"] == 0 / 4.0
    # premium-only cell: no claims -> severity undefined, LR is 0.0 (valid)
    void_lr = _by_key(rows, "loss_ratio",
                      {"product": "VoidOnly", "segment": "None",
                       "region": "East"})
    assert void_lr["value"] == 0.0
    void_sev = _by_key(rows, "claim_severity",
                       {"product": "VoidOnly", "segment": "None",
                        "region": "East"})
    assert void_sev["value"] is None
    assert void_sev["undefined_reason"] == "no claims in cell"
    # thin cell (1 claim C3, earned 500): LR 0.1, severity 50 — both defined
    ghost_lr = _by_key(rows, "loss_ratio",
                       {"product": "Empty", "segment": "Ghost",
                        "region": "North"})
    assert ghost_lr["value"] == 50 / 500


def test_frequency_uses_exposure_not_premium():
    rows = m.build_rows(m.aggregate_frames(*_frames()), period="2026-09",
                        dv_ids=[])
    port = _by_key(rows, "claim_frequency", {})
    # 3 claims / 9.0 exposure units — NOT / earned premium (1900)
    assert port["value"] == 3 / 9.0
    assert port["formula"] == "claim_frequency = claim_count/earned_exposure_units"


def test_ave_sign_conventions_and_missing_baseline():
    rows = m.build_rows(m.aggregate_frames(*_frames()), period="2026-09",
                        dv_ids=[], expected_lr=0.5)
    ave = _by_key(rows, "ave_variance", {})
    actual = 1050 / 1900  # portfolio incurred / earned
    assert ave["value"] == (actual - 0.5) * 100
    assert ave["expected_value"] == 0.5 and ave["unit"] == "pp"

    rows_none = m.build_rows(m.aggregate_frames(*_frames()),
                             period="2026-09", dv_ids=[])
    ave_none = _by_key(rows_none, "ave_variance", {})
    assert ave_none["value"] is None
    assert ave_none["undefined_reason"] == (
        "no expected baseline for loss_ratio (2026-09)")


def test_prev_delta_and_small_sample_flags():
    cur = m.aggregate_frames(*_frames())
    prior = {"loss_ratio": {}}  # placeholder replaced below
    _ = prior
    # synthetic prior: same cells, LR 0.6 at portfolio via metrics fallback
    rows = m.build_rows(cur, period="2026-09", dv_ids=[],
                        prior_metrics={("loss_ratio", "{}"): 0.6})
    port = _by_key(rows, "loss_ratio", {})
    assert port["prev_value"] == 0.6
    assert port["delta_pp"] == (1050 / 1900 - 0.6) * 100
    # small-sample: 2 claims / 5 policies cell flagged; 30-policy cell not
    cs = _by_key(rows, "loss_ratio",
                 {"product": "Commercial", "segment": "Construction",
                  "region": "South"})
    assert cs["flags"]["small_sample"] is True
    ghost = _by_key(rows, "loss_ratio",
                    {"product": "Empty", "segment": "Ghost", "region": "North"})
    assert ghost["flags"]["small_sample"] is True  # 1 claim < 20
    # the whole toy book is small-sample too — flags are mechanical per cell
    assert port["flags"]["small_sample"] is True


def test_severity_delta_is_relative_pct():
    cur = m.aggregate_frames(*_frames())
    rows = m.build_rows(cur, period="2026-09", dv_ids=[],
                        prior_metrics={("claim_severity", "{}"): 250.0})
    port = _by_key(rows, "claim_severity", {})
    assert port["value"] == 1050 / 3
    assert port["delta_pp"] == (1050 / 3 - 250.0) / 250.0 * 100
