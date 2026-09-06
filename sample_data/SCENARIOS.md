# Sample-data scenarios (verified outputs of `generate_sample_data.py`, seed 42)

Fictional insurer **Meridian General Insurance**, currency **₹ (INR)**.
Regenerate: `cd backend && .venv/bin/python scripts/generate_sample_data.py`
(all numbers below are asserted by the script itself — `STORYLINE OK`).

Portfolio: **6,000 policies / 280 claims (292 rows incl. dupes)** in Sep 2026.

| # | Scenario | Location in data | Verified behavior |
|---|---|---|---|
| 1 | Normal baselines | Motor, Home | metrics compute normally |
| 1b | Stable book | Health: LR **58.00% → 58.00%** (+0.00pp) | green "no material deviation" finding |
| 2 | Loss-ratio increase | Commercial/Construction/**South**: LR **62.9% → 78.1% (+15.2pp)** | portfolio LR **63.1% → 67.3% (+4.2pp, AvE +4.5pp vs expected 62.8%)**; high-severity finding, **~61% contribution** (region-level OAT share 60.8%) |
| 3 | Severity-driven deterioration | Construction South severity **+13.1%** (258,519 → 292,387), frequency **+2.4%** | Insight: "driven more by severity than frequency" |
| 4 | Concentrated segment | deterioration concentrated in South region | regional-concentration finding (medium) |
| 5 | Duplicate records | **12 exact duplicate** claim rows in `claims_2026_09_v2.csv` | auto-removed per validated dedup rule — logged, audit row, INFO validation notice, UI toast |
| 6 | Missing value | **15 claims** with blank `region` (policy link intact, never the outlier, never C-South) | green auto-fix via policy join (logged) |
| 7 | Reconciliation mismatch | earned premium sums **₹117,480,000** vs reference **₹120,000,000 (−2.10%)** | red CP-2 blocker (threshold >2.0%) |
| 8 | Outlier claim | **₹30,00,000** Construction South claim (~10× cell-average severity) | yellow "unusual but not proven invalid". **[ID] plan says ₹1.5cr, but a 15M single claim cannot coexist with the 78.1% cell-LR / 61%-contribution math (cell incurred ≈ ₹14.0M); calibrated to book scale** — same precedent as the plan's own ₹50cr→₹1.5cr calibration. §25 step 9 demo script should read "₹30L" until the book is rescaled. |
| 9 | Small sample | Marine Cargo: **3 policies, 4 claims**, LR 77.5% | small-sample caveat on its metrics + report note (not a finding) |
| 10 | Prior-period monitoring item | `history/` Aug-2026 files (seed-only) + seeded Aug workflow (Phase 5 `seed.py`) | Knowledge Agent links "repeat monitoring item" |

Assumption baseline (`reference_values`, migration `002`): expected severity trend
Construction **+5.0%** vs observed **+13.1%** → variance **+8.1pp ≥ 5pp gate** → CP-4.
Expected portfolio LR **62.8%**; Apr–Aug history series in `reference_values`.

Extra column: `segment` is present on all three CSVs (plan §15 lists claims
columns without it, but segment attribution needs a source; data-prep alias table
maps it, validation covers it).
`claims_2026_09.csv` (v1) = stale Aug data relabeled → CP-1 duplicate/period profiles.
