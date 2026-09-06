"""Sample data generator — Meridian General Insurance, INR.

Deterministic (NumPy RNG seed 42). Writes Sep-2026 demo CSVs + Aug-2026 history CSVs.
Cell-level earned/incurred targets are set explicitly so portfolio aggregates hit the
§15 storyline exactly; RNG only distributes within cells (ids, dates, splits).

Targets (Sep 2026):
  earned  = 117,480,000  (vs reference 120,000,000 -> -2.10% recon mismatch, CP-2)
  incurred=  79,064,040  (portfolio LR 67.3%; Aug 63.1%; AvE +4.5pp vs exp 62.8%)
  Commercial/Construction/South LR 62.9% -> 78.1% (+15.2pp, ~61% contribution)
  Construction severity +13.1% vs +5.0% assumption (CP-4); frequency +2.4%
  12 exact duplicate claim rows; ~15 blank-region claims (policy-join fix)
  Marine Cargo: 3 policies, 4 claims (small sample)
  Outlier: Rs.30,00,000 Construction/South claim [ID: plan says Rs.1.5cr, but a
    15M single claim cannot coexist with the 78.1% cell-LR / 61% contribution math
    (cell incurred is ~14M). Calibrated to book scale per the plan's own precedent.]

Usage: python scripts/generate_sample_data.py [--out DIR]
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

SEED = 42
PERIOD = "2026-09"
HIST_PERIOD = "2026-08"

# (product, segment, region, policies_sep, earned_sep, incurred_sep, claims_sep)
SEP_CELLS: list[tuple] = [
    ("Commercial", "Construction", "South", 470, 17_970_000, 14_034_570, 48),
    ("Commercial", "Construction", "North", 260, 5_200_000, 3_432_000, 15),
    ("Commercial", "Construction", "East", 220, 4_600_000, 3_036_000, 13),
    ("Commercial", "Construction", "West", 220, 4_200_000, 2_772_000, 12),
    ("Commercial", "SME", "North", 240, 4_300_000, 2_752_000, 12),
    ("Commercial", "SME", "South", 230, 4_200_000, 2_688_000, 12),
    ("Commercial", "SME", "East", 220, 4_000_000, 2_560_000, 11),
    ("Commercial", "SME", "West", 210, 3_500_000, 2_240_000, 9),
    ("Commercial", "Property", "North", 180, 3_600_000, 2_268_000, 9),
    ("Commercial", "Property", "South", 170, 3_400_000, 2_142_000, 9),
    ("Commercial", "Property", "East", 150, 2_800_000, 1_764_000, 7),
    ("Commercial", "Property", "West", 150, 2_200_000, 1_386_000, 7),
    ("Commercial", "Marine Cargo", "West", 3, 400_000, 310_000, 4),
    ("Motor", "Motor", "North", 480, 8_505_000, 5_829_500, 17),
    ("Motor", "Motor", "South", 470, 8_305_000, 5_691_500, 16),
    ("Motor", "Motor", "East", 440, 6_400_000, 4_416_000, 15),
    ("Motor", "Motor", "West", 410, 5_800_000, 4_002_000, 14),
    ("Health", "Health", "North", 240, 4_800_000, 2_784_000, 8),
    ("Health", "Health", "South", 230, 4_600_000, 2_668_000, 8),
    ("Health", "Health", "East", 220, 4_400_000, 2_552_000, 7),
    ("Health", "Health", "West", 210, 4_200_000, 2_436_000, 7),
    ("Home", "Home", "North", 160, 2_900_000, 2_131_000, 6),
    ("Home", "Home", "South", 150, 2_700_000, 1_984_000, 5),
    ("Home", "Home", "East", 140, 2_500_000, 1_837_000, 5),
    ("Home", "Home", "West", 127, 2_000_000, 1_348_470, 4),
]

# C-South Aug baseline: LR 62.9%, 46 claims, exposure 1000 units.
CS_AUG = {"policies": 460, "earned": 18_906_000, "incurred": 11_891_874, "claims": 46}
# C-South Sep exposure units -> frequency +2.4%: (48/E1)/(46/1000) = 1.024
CS_SEP_EXPOSURE = 1019.0
CS_AUG_EXPOSURE = 1000.0

OUTLIER_AMOUNT = 3_000_000
N_DUPES = 12
N_BLANK_REGION = 15

CLAIM_TYPES = {
    "Commercial": ["Property Damage", "Liability", "Fire", "Marine Transit"],
    "Motor": ["Own Damage", "Third Party"],
    "Health": ["Hospitalization", "Day Care"],
    "Home": ["Property Damage", "Burglary"],
}


def split_total(total: int, weights: np.ndarray) -> list[int]:
    weights = np.asarray(weights, dtype=float)
    weights = weights / weights.sum()
    parts = np.floor(weights * total).astype(int)
    remainder = total - int(parts.sum())
    frac = weights * total - parts
    for i in np.argsort(-frac)[:remainder]:
        parts[i] += 1
    return [int(p) for p in parts]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent.parent.parent / "sample_data"),
    )
    args = ap.parse_args()
    out = Path(args.out)
    hist = out / "history"
    out.mkdir(parents=True, exist_ok=True)
    hist.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(SEED)

    claims, premium, exposure = [], [], []
    policyno, claimno = 0, 0

    def new_policies(product, segment, region, n, earned_total, period, expo_per_policy):
        nonlocal policyno
        nonlocal claims, premium, exposure
        ids = []
        if n == 0:
            return ids
        earned = split_total(earned_total, rng.random(n) + 0.2)
        for i in range(n):
            policyno += 1
            pid = f"P{policyno:05d}"
            ids.append(pid)
            written = int(round(earned[i] * 1.03))
            premium.append([pid, product, segment, region, period, written, earned[i]])
            exposure.append([pid, product, segment, region, period, 1, expo_per_policy])
        return ids

    def new_claims(pids, product, segment, region, n, incurred_total, period, rng,
                   outlier=False, allow_blank=False):
        nonlocal claimno
        nonlocal claims
        if n == 0:
            return
        assert len(pids) > 0
        amounts = split_total(incurred_total - (OUTLIER_AMOUNT if outlier else 0),
                              rng.random(n - (1 if outlier else 0)) + 0.3)
        if outlier:
            amounts = amounts + [OUTLIER_AMOUNT]
            rng.shuffle(amounts)
        types = CLAIM_TYPES[product]
        for i in range(n):
            claimno += 1
            cid = f"C{claimno:05d}"
            pid = pids[rng.integers(len(pids))]
            eday = int(rng.integers(1, 29))
            rday = min(eday + int(rng.integers(0, 10)), 28)
            mm = period[5:7]
            claims.append([cid, pid, product, segment, region,
                           types[int(rng.integers(len(types)))],
                           f"2026-{mm}-{eday:02d}", f"2026-{mm}-{rday:02d}",
                           "Closed" if rng.random() < 0.7 else "Open", amounts[i]])

    # ---- September book ----
    for prod, seg, reg, npol, earned, incurred, ncl in SEP_CELLS:
        expo = 1.0
        if (seg, reg) == ("Construction", "South"):
            expo = round(CS_SEP_EXPOSURE / npol, 4)
        pids = new_policies(prod, seg, reg, npol, earned, PERIOD, expo)
        new_claims(pids, prod, seg, reg, ncl, incurred, PERIOD, rng,
                   outlier=(seg == "Construction" and reg == "South"))

    # 15 blank-region claims (policy link intact -> join fix), never on the outlier row
    # Row layout: 0 cid, 1 pid, 2 product, 3 segment, 4 region, ..., 8 status, 9 incurred
    idxs = [i for i, r in enumerate(claims)
            if r[9] != OUTLIER_AMOUNT and not (r[3] == "Construction" and r[4] == "South")]
    for i in rng.choice(idxs, N_BLANK_REGION, replace=False):
        claims[i][4] = ""

    # 12 exact duplicate rows appended
    dup_idx = rng.choice(len(claims), N_DUPES, replace=False)
    claims.extend([list(claims[i]) for i in dup_idx])

    # ---- August history (seed only) ----
    hclaims, hpremium, hexposure = [], [], []
    claims_save, premium_save, exposure_save = claims, premium, exposure
    claims, premium, exposure = hclaims, hpremium, hexposure
    aug_factor = 0.94
    # Pass 1: policies (fixes Aug earned total). Pass 2: claims, with a plug on
    # Home/West incurred so Aug portfolio LR == 63.1% exactly.
    aug_cells: list[tuple] = []  # (prod, seg, reg, pids, ainc, ancl)
    for prod, seg, reg, npol, earned, incurred, ncl in SEP_CELLS:
        if seg == "Construction" and reg == "South":
            pids = new_policies(prod, seg, reg, CS_AUG["policies"], CS_AUG["earned"],
                                HIST_PERIOD, round(CS_AUG_EXPOSURE / CS_AUG["policies"], 4))
            aug_cells.append((prod, seg, reg, pids, CS_AUG["incurred"], CS_AUG["claims"], True))
            continue
        anpol = max(2 if seg == "Marine Cargo" else 50, int(round(npol * aug_factor)))
        aearned = int(round(earned * aug_factor))
        ainc = int(round(incurred * (0.94 if prod == "Health" else 0.90)))
        ancl = max(1, int(round(ncl * aug_factor)))
        pids = new_policies(prod, seg, reg, anpol, aearned, HIST_PERIOD, 1.0)
        aug_cells.append((prod, seg, reg, pids, ainc, ancl, False))
    aug_earned = sum(r[6] for r in premium)
    aug_target_inc = int(round(aug_earned * 0.631))
    aug_fixed = sum(c[4] for c in aug_cells)
    plug_idx = next(i for i, c in enumerate(aug_cells)
                    if c[1] == "Home" and c[2] == "West")
    plug = aug_target_inc - aug_fixed
    assert plug > 0, (aug_target_inc, aug_fixed)
    aug_cells[plug_idx] = (*aug_cells[plug_idx][:4], aug_cells[plug_idx][4] + plug,
                           *aug_cells[plug_idx][5:])
    for prod, seg, reg, pids, ainc, ancl, _fixed in aug_cells:
        new_claims(pids, prod, seg, reg, ancl, ainc, HIST_PERIOD, rng)
    claims, premium, exposure = claims_save, premium_save, exposure_save

    def dump(path, header, rows):
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    ch = ["claim_id", "policy_id", "product", "segment", "region", "claim_type",
          "event_date", "report_date", "status", "incurred_amount"]
    ph = ["policy_id", "product", "segment", "region", "period",
          "written_premium", "earned_premium"]
    eh = ["policy_id", "product", "segment", "region", "period",
          "active_policies", "earned_exposure_units"]
    dump(out / f"claims_{PERIOD.replace('-', '_')}_v2.csv", ch, claims)
    dump(out / f"premium_{PERIOD.replace('-', '_')}.csv", ph, premium)
    dump(out / f"exposure_{PERIOD.replace('-', '_')}.csv", eh, exposure)
    dump(hist / f"claims_{HIST_PERIOD.replace('-', '_')}.csv", ch, hclaims)
    dump(hist / f"premium_{HIST_PERIOD.replace('-', '_')}.csv", ph, hpremium)
    dump(hist / f"exposure_{HIST_PERIOD.replace('-', '_')}.csv", eh, hexposure)
    # v1 = stale Aug data relabeled as Sep (triggers CP-1 duplicate/period check)
    dump(out / f"claims_{PERIOD.replace('-', '_')}.csv", ch, hclaims)

    # ---- verify storyline numbers ----
    import pandas as pd

    cdf = pd.DataFrame(claims, columns=ch)
    cuniq = cdf.drop_duplicates()
    pdf = pd.DataFrame(premium, columns=ph)
    earned = int(pdf["earned_premium"].sum())
    incurred = int(cuniq["incurred_amount"].sum())
    lr = incurred / earned
    recon = (earned - 120_000_000) / 120_000_000 * 100
    cs = cuniq[(cuniq["segment"] == "Construction") & (cuniq["region"] == "South")]
    cs_earned = 17_970_000
    cs_lr = int(cs["incurred_amount"].sum()) / cs_earned * 100
    have = pd.DataFrame(hclaims, columns=ch)
    haug = pd.DataFrame(hpremium, columns=ph)
    hlr = int(have["incurred_amount"].sum()) / int(haug["earned_premium"].sum()) * 100
    hcs = have[(have["segment"] == "Construction") & (have["region"] == "South")]
    hcs_lr = int(hcs["incurred_amount"].sum()) / CS_AUG["earned"] * 100
    print(f"policies={len(pdf)} claims(rows)={len(cdf)} unique={len(cuniq)} "
          f"dupes={len(cdf) - len(cuniq)} blank_region={(cdf['region'] == '').sum()}")
    print(f"earned={earned:,} incurred={incurred:,} LR={lr * 100:.3f}% "
          f"(Aug {hlr:.3f}%) recon={recon:.2f}%")
    print(f"C-South LR Sep={cs_lr:.3f}% (n={len(cs)}) Aug={hcs_lr:.3f}% "
          f"(n={len(hcs)}) delta={cs_lr - hcs_lr:+.2f}pp")
    s0 = int(hcs["incurred_amount"].sum()) / len(hcs)
    s1 = int(cs["incurred_amount"].sum()) / len(cs)
    print(f"C-South severity Aug={s0:,.0f} Sep={s1:,.0f} change={s1 / s0 - 1:+.2%} "
          f"(assumption +5.0%)")
    f0, f1 = len(hcs) / CS_AUG_EXPOSURE, len(cs) / CS_SEP_EXPOSURE
    print(f"C-South frequency change={f1 / f0 - 1:+.2%}")
    print(f"outlier present: {(cuniq['incurred_amount'] == OUTLIER_AMOUNT).sum()}")
    assert earned == 117_480_000, earned
    assert incurred == 79_064_040, incurred
    assert abs(lr * 100 - 67.3) < 1e-9
    assert abs(recon - (-2.1)) < 1e-9
    assert abs(hlr - 63.1) < 0.05, hlr
    assert abs(hcs_lr - 62.9) < 0.05, hcs_lr
    assert abs(cs_lr - 78.1) < 0.05, cs_lr
    assert len(cdf) - len(cuniq) == N_DUPES
    print("STORYLINE OK")


if __name__ == "__main__":
    main()
