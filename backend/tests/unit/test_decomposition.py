"""Unit tests for analytics/decomposition.py (§6.5/§22): dominant,
multi-driver, and no-movement cases; shares sum to 100% ± 0.01."""
import pytest

from app.analytics.decomposition import oat_contributions


def test_single_dominant_driver():
    cur = {("A",): 0.80, ("B",): 0.60}
    prev = {("A",): 0.60, ("B",): 0.60}
    w = {("A",): 0.5, ("B",): 0.5}
    total = 0.70 - 0.60  # portfolio movement +0.10
    out = oat_contributions(cur, prev, w, total)
    assert out[("A",)] == pytest.approx(0.20 * 0.5 / 0.10 * 100)
    assert out[("B",)] == pytest.approx(0.0)
    assert abs(sum(out.values()) - 100.0) <= 0.01


def test_multi_driver_not_forced_single_cause():
    cur = {("A",): 0.70, ("B",): 0.65, ("C",): 0.60}
    prev = {("A",): 0.60, ("B",): 0.60, ("C",): 0.60}
    w = {("A",): 0.5, ("B",): 0.3, ("C",): 0.2}
    total = (0.70 * 0.5 + 0.65 * 0.3 + 0.60 * 0.2) - 0.60
    out = oat_contributions(cur, prev, w, total)
    assert len(out) == 3
    assert out[("A",)] > out[("B",)] > out[("C",)] == 0.0
    assert abs(sum(out.values()) - 100.0) <= 0.01


def test_no_movement_returns_empty():
    cur = {("A",): 0.60}
    prev = {("A",): 0.60}
    assert oat_contributions(cur, prev, {("A",): 1.0}, 0.0) == {}
    assert oat_contributions(cur, prev, {("A",): 1.0}, None) == {}
    assert oat_contributions(cur, prev, None, 0.10) == {}


def test_cells_missing_prior_are_skipped_not_fabricated():
    cur = {("A",): 0.80, ("NEW",): 0.90}
    prev = {("A",): 0.60}
    out = oat_contributions(cur, prev, {("A",): 0.6, ("NEW",): 0.4}, 0.10)
    assert set(out) == {("A",)}
