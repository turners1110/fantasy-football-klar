# Phase 3B Final Report

**Status: PHASE 3B FAILED** (10 of 16 required conditions cleanly met -- see section 16. The metric-integrity work you asked to be done FIRST, before any tuning, is complete and verified; the harder remediation work -- willingness-model overhaul, counterfactual-accuracy engineering, and full calibration -- was not reached this pass.)

## 1. Files changed / added

New scripts:
- `scripts/build_phase3b_concentration_audit.py` -- fixes the top-12/24 pooling bug
- `scripts/build_phase3b_budget_gap_audit.py` -- gross vs. signed budget-gap terminology
- `scripts/build_phase3b_historical_concentration.py` -- 6-version historical concentration recompute
- `scripts/build_phase3b_projection_audit.py` -- per-position projection/VBD audit
- `scripts/build_phase3b_public_benchmarks.py` -- public rank/tier + existing-neutral benchmark curves
- `scripts/build_phase3b_keeper_adjusted_benchmark.py` -- keeper-adjusted position demand benchmark
- `scripts/build_phase3b_position_decomposition.py` -- controlled RB-overweight root-cause experiments
- `scripts/build_phase3b_sam_label_audit.py` -- Sam-scenario label/price audit against public rank + simulated market price

New tests:
- `tests/test_phase3b_concentration.py` (required tests 1-4, plus a gross/signed sanity check)
- `tests/test_phase3b_market_metrics.py` (required tests 5-20)

No phase 1/2/2B/3A files were modified or rewritten. All prior commits and reports are preserved as-is.

## 2. Concentration-metric root cause

`scripts/build_market_clearing_diagnostics.py` (phase 3A) pooled every sale price from all 40 simulated auctions into one array, sorted it, and divided the 12 single highest prices in that pooled array by the sum of ALL sales across every auction. The numerator (12 sales) does not scale with the number of auctions pooled; the denominator does -- so pooling more seeds mechanically drove the reported share toward zero. Confirmed exactly: manually reconciling one seed (108 real sale prices, fully visible in `concentration_manual_reconciliation.csv`) gives a top-12 share of **61.9%**, not anywhere near 2.95%.

## 3. Corrected simulated concentration

Computed correctly (per-auction, then averaged across 200 seeds), in `concentration_by_simulation.csv`:
- **Mean top-12 share: 65.28%** (median 65.30%)
- **Mean top-24 share: 83.15%**
- Old buggy pooled figure: 0.60% (200 seeds) / 2.95% (the original 40-seed report) -- corrected figure is **109x higher**.

## 4. Historical concentration ranges

Recomputed from source (`historical_concentration_benchmarks.csv`), not copied from memory:

| Version | n | Top-12 share |
|---|---|---|
| RAW_OBSERVED | 182 | 23.77% |
| NON_DOLLAR_ONE | 123 | 24.13% |
| RELIABILITY_WEIGHTED | 123 | 24.13% |
| UNKNOWN_NON_DOLLAR_ONE_SUBSET | 113 | **25.48%** (matches the "~25.4%" figure referenced in the brief, now traced to a specific, reproducible source) |
| CONFIRMED_COMPETITIVE_AUCTION_SUBSET | 0 | N/A -- no row in this repo carries a confirmed auction-origin label |
| FULL_ROSTER_ALL_PLAYERS | 192 | 23.67% |

All six versions cluster tightly at 23.7%-25.5% -- a stable, well-behaved historical estimate regardless of filtering assumption.

## 5. Public benchmark concentration

No genuine external public dollar-value auction list exists anywhere in this repo (searched `inputs/`, `data/`, `output*/`) -- reported `NOT_AVAILABLE` rather than fabricated. The two buildable curves (`public_market_benchmarks.csv`):
- **PUBLIC_RANK_TIER** (FantasyPros rank, 310/320 players matched): top-12 = 31.59%, top-24 = 46.85%
- **EXISTING_PROJECTION_NEUTRAL** (this repo's own `suggested_auction_price`): top-12 = 30.65%, top-24 = 51.78%

Both land well below the simulator's 65.28% and somewhat above the 23.7-25.5% historical range -- the simulator is far more top-heavy than either benchmark.

## 6. Gross and signed budget gaps

`budget_gap_definition_audit.csv`. **Correction to the phase 3A report**: it described "$43" as "the sum of absolute differences," which was wrong. Recomputed directly:
- **gross_absolute_team_difference (all 12 teams): $67.00**
- **signed_net_league_difference (all 12 teams): -$43.00**
- gross_absolute_team_difference (6 UNRESOLVED_GAP teams only): $65.00
- signed_net_league_difference (6 UNRESOLVED_GAP teams only): -$45.00

"$43" was always the signed net total, never a sum of absolute values. Both are now reported side by side, always, and a test (`test_05_gross_vs_signed_budget_difference_differ`) asserts they can never collapse to the same number by construction.

## 7. Primary budget decision

Confirmed (not newly built -- this was already correct architecture): `auction_model/confirmed_keeper_pipeline.py`'s `compute_team_states` sets `primary_auction_budget = sheet_reported` directly for every non-Sam team (cash_adjustments feeds only a conflict-logging comparison figure, never the primary budget itself), and Sam's row uses the $223/$221 user-confirmed override. **REPORTED_BUDGETS_WITH_SAM_OVERRIDE was already the production primary scenario**, with no double-counting of the Brandon/Sam trade adjustment. `outputs/auction_rebuild/data/team_starting_states.csv`'s `primary_auction_budget` IS this scenario; `outputs/auction_rebuild/phase3a/team_starting_states_formula_reconciled.csv` remains the sensitivity scenario. Tests 5-7 verify this directly against the real data files.

## 8. Projection audit

`projection_position_audit.csv` / `top_projection_audit.csv`. Coverage is uneven: QB 54.6%, RB 78.4%, WR 75.8%, TE 56.9% of position rows in `data/projections_2026.csv` actually carry a `projected_points` value -- 158 of 508 rows (31%) have none at all, relying on `mock_draft/points.py`'s fallback-ratio imputation (fixed in phase 3A) to become usable $1 targets rather than true zeros. No duplicate players, no position-label errors, QB scoring correctly uses 4-point passing TDs, half-PPR (0.5/reception) confirmed correct. TE reception coverage looks thin (9 of 116 TE rows show zero recorded receptions) -- flagged for review, not confirmed broken.

## 9. RB-overweight root cause

**Definitively isolated via controlled experiment** (`position_spend_decomposition.csv`), not just theorized:
- Static evidence first: `config.replacement_rank(RB) == config.replacement_rank(WR) == 55` (identical formula inputs), no archetype sets `position_weight` (defaults to 1.0 everywhere), and the "positional extremist" archetypes' RB/WR targets mirror each other exactly -- these structurally rule out causes #2, #3, and #5 as formula bugs.
- The real asymmetry: RB's mean VBD (50.69) is 2.3x WR's (22.46) despite the identical replacement rank -- the raw point projections in `data/projections_2026.csv` are simply far more top-heavy for RB than WR.
- **Controlled experiment confirms this is causal**: rescaling RB's `base_value`/`projected_points` to match WR's VBD shape drops RB spend share from **65.8% to 34.8%** (a 31-point swing) -- by far the largest effect of any experiment run.
- Excluding RB from FLEX scoring: **zero effect** on spend share (65.8% unchanged) -- rules out cause #3 empirically, not just structurally.
- Disabling the incremental-utility gate entirely: **zero effect** (65.8% unchanged) -- rules out cause #11.
- Positive control (artificially injecting an RB position-weight penalty): RB share fell to 50.8%, confirming the experiment harness correctly detects a real, deliberately-injected effect (validating that the null results above are real absences, not a blind spot).

**Conclusion**: the dominant driver is the raw RB/WR point-projection asymmetry feeding `base_value`, amplified further by live competitive bidding (51% at the static-valuation level -> 66% once bidding dynamics apply). This was NOT remediated this phase -- per the explicit instruction not to tune a position multiplier before finding the cause, and given remediation (deciding whether to re-derive projections, adjust replacement-level treatment, or apply a documented correction) is real, separate work.

## 10. Corrected position spending

`keeper_adjusted_position_benchmark.csv` blends public/projection/historical value shares, weighted by this league's real keeper composition and roster demand:

| Position | Simulated (phase 3A) | Keeper-adjusted blended target | Gap |
|---|---|---|---|
| QB | 3.2% | 13.1% | -9.9pp |
| RB | 65.8% | 40.8% | **+25.0pp** |
| WR | 28.9% | 38.3% | -9.4pp |
| TE | 2.2% | 7.8% | -5.6pp |

RB is the dominant, clearly-quantified gap. Not remediated this phase.

## 11. Total spending and unused cash

Unchanged from phase 3A's fix (not revisited this phase): ~96.5% of league cash spent, ~$8.5-8.9/team unused across all configurations tested. This remains healthy; the shape (not the amount) of spending is the open problem.

## 12. Conditional price-distribution design

Implemented for a watchlist of 8 players used in the phase 3A Sam sanity tests (`sam_label_audit.csv`), NOT the full ~320-player pool (scoping decision, disclosed): for each player, `draft_probability` uses ALL simulated outcomes (sold and unsold), while `market_price_p50`/`p75` are computed ONLY over simulations where the player actually sold, with a minimum-observation threshold (5) before reporting a percentile (Geno Smith, sold in only 28% of 60 auctions, still cleared the threshold). Tests 8, 9, and 20 verify this design directly.

**Notable finding**: "Premium RB: Josh Jacobs" (phase 3A's label) has a real simulated **median market price of $201.50** -- far above his $126 `base_value` used in that scenario's `assumed_purchase_price`. "Premium TE: TJ Hockenson" clears at a median of just $19.89, and public rank 175/tier 10 is a weak basis for calling him "premium" at all -- a real labeling issue the audit catches. This means phase 3A's RB scenario actually UNDERSTATED the true cost of that pickup; using the real market price would have made the "worst per-dollar return" conclusion for RB even more pronounced, reinforcing (not undermining) that report's directional finding.

## 13. Counterfactual accuracy

**Not attempted this phase.** The existing measurement stands unchanged: mean absolute error 63.59 points, max 263.14 points (`outputs/auction_rebuild/phase3a/counterfactual_approximation_error.csv`), still far outside the required $3 median / $8 p90 hard-ceiling-error and 5/15-point utility-regret targets. No exact-solve fallback for Sam's shortlist was implemented either. This is real, separate algorithmic work (better completion ordering, position-aware replacement, budget-aware tiering, or caching an exact solve for small remaining pools) that was not reached given the scope of the metric-integrity work prioritized first, per your explicit instruction.

## 14. Calibration method

**Not attempted this phase.** No grid/Bayesian search was run against training/validation/held-out seed groups. `test_19_held_out_seeds_do_not_overlap_training_or_validation` verifies only the STRUCTURAL property that disjoint seed ranges don't overlap by construction -- it does not exercise an actual calibration harness, which does not yet exist.

## 15. Held-out results

**Not applicable** -- no calibration was run, so there is nothing to hold out results for. `held_out_validation.json` was not produced.

## 16. Tests

**159 of 159 tests pass** (138 from phases 1-3A + 5 in `test_phase3b_concentration.py` + 16 in `test_phase3b_market_metrics.py`, one of which skips gracefully when a phase-3A output file it checks isn't present). All 20 of item 17's required tests are covered.

## 17. PHASE 3B PASSED/FAILED determination

**PHASE 3B FAILED** -- 10 of 16 required conditions cleanly met:

| # | Condition | Status |
|---|---|---|
| 1 | Concentration metrics pass manual reconciliation | MET |
| 2 | Top-12 metric no longer contains cross-simulation aggregation errors | MET |
| 3 | Budget gross and signed gaps are distinct | MET |
| 4 | Primary budget scenario is explicit | MET |
| 5 | Projection coverage is audited | MET |
| 6 | RB-overweight root cause is identified | MET |
| 7 | Position spending falls near a keeper-adjusted benchmark | **NOT MET** -- 25pp RB gap, not remediated |
| 8 | Top-player concentration falls near a blended benchmark | **NOT MET** -- 65.3% simulated vs. 25-31% benchmark range |
| 9 | Overall spending remains plausible | PARTIAL -- amount is plausible (96.5% spent), shape is not (see 7/8) |
| 10 | Roster and lineup legality remain 100% | MET |
| 11 | Counterfactual accuracy meets stated thresholds or exact solves used for shortlist | **NOT MET** -- no improvement attempted |
| 12 | Price distributions separate draft probability and conditional price | PARTIAL -- built for an 8-player watchlist, not the full pool |
| 13 | Calibration and held-out seeds remain separate | **NOT MET** -- no calibration harness was built to test this on |
| 14 | All tests pass | MET (159/159) |
| 15 | No evolved strategy runs | MET |
| 16 | No final draft advice is published | MET |

## 18. Remaining weaknesses

1. RB-overweight root cause is identified with high confidence but not remediated -- the fix requires a real decision (re-derive `data/projections_2026.csv`'s RB point model, adjust how VBD converts to `base_value`, or apply a documented, disclosed dampening) that this phase deliberately did not make on its own authority.
2. Concentration is now measured correctly but reveals the simulator is MORE top-heavy (65.3%) than either historical (23.7-25.5%) or public (30-32%) benchmarks -- a real market-shape problem, separate from the RB position-share problem, not yet root-caused.
3. Counterfactual bid-ceiling accuracy remains far outside the required thresholds; the engine should not be trusted for real per-player price ceilings.
4. No calibration was attempted; the model's behavioral parameters (archetype weights, willingness formula, nomination scoring) remain hand-set defaults, not fitted against any benchmark.
5. Price-distribution reporting only covers a small watchlist, not the full player pool.
6. The full item-16 simulation gate (200 calibration + 200 validation + 200 held-out, times ~8 scenario axes) was not run.

## 19. Exact rerun commands

```bash
cd fantasy-football-klar

# Full test suite (159 tests)
python3 -m pytest tests/ -q
python3 -m pytest tests/test_phase3b_concentration.py tests/test_phase3b_market_metrics.py -q

# Concentration metric fix + manual reconciliation
python3 scripts/build_phase3b_concentration_audit.py

# Budget gap terminology
python3 scripts/build_phase3b_budget_gap_audit.py

# Historical concentration (6 versions)
python3 scripts/build_phase3b_historical_concentration.py

# Projection audit
python3 scripts/build_phase3b_projection_audit.py

# Public + keeper-adjusted benchmarks
python3 scripts/build_phase3b_public_benchmarks.py
python3 scripts/build_phase3b_keeper_adjusted_benchmark.py

# RB root-cause decomposition (5 controlled experiments, ~5 min)
python3 scripts/build_phase3b_position_decomposition.py

# Sam label audit (60-seed market-price batch, ~2 min)
python3 scripts/build_phase3b_sam_label_audit.py
```

## 20. Recommended Phase 3C scope

1. **Decide and implement a remediation for the RB point-projection asymmetry** -- now that the cause is conclusively isolated (not theorized), this is the highest-leverage single fix available: re-examine `data/projections_2026.csv`'s RB generation, or apply a documented, disclosed adjustment to how RB's VBD converts to `base_value`, then re-measure against the keeper-adjusted benchmark.
2. **Root-cause the simulator's excess top-heaviness** (65.3% vs. 25-32% benchmarks) independent of the RB position-share issue -- likely a separate mechanism (e.g., `STAR_MAX_VALUE_MULTIPLE`, `EARLY_DRAFT_PREMIUM_MAX`, or `tier_aggression` compounding across archetypes) worth its own controlled-experiment treatment.
3. **Counterfactual accuracy engineering** -- position-aware replacement ordering, budget-aware tier selection, and/or caching exact solves for small remaining pools, re-measured against the $3/$8 median/p90 hard-ceiling-error targets.
4. **Build the actual calibration harness** (train/validation/held-out seed groups, a transparent grid or Bayesian search over the ~10 listed behavioral parameters) only after 1-3 above are addressed -- calibrating against a benchmark that's still known to be badly missed (RB share) would just be fitting noise.
5. Extend price-distribution reporting from the 8-player watchlist to the full auction-eligible pool.


---

## LABEL CORRECTION NOTICE (added retroactively, Phase 3G)

This report predates Phase 3D's price-label taxonomy fix
(`auction_model/labels.py`). Any dollar figure in this report that was
described in generic terms ("simulated price," "market value," "bid
ceiling," etc.) should be re-read under the corrected, mutually-exclusive
label set introduced in Phase 3D and still in force as of Phase 3G:
`UNCALIBRATED_SIMULATED_PRICE`, `CALIBRATED_EXPECTED_MARKET_PRICE`,
`PUBLIC_AUCTION_ANCHOR`, `HISTORICAL_LEAGUE_PRICE`, `TEAM_SPECIFIC_VALUE`,
`EXACT_TEAM_BID_CEILING`, `APPROXIMATE_TEAM_BID_CEILING`. In particular,
every simulated price figure quoted in this report should be treated as
`UNCALIBRATED_SIMULATED_PRICE` (this report predates any calibration
validation) unless a later phase's report explicitly re-labels a specific
number as `CALIBRATED_EXPECTED_MARKET_PRICE`. See
`outputs/auction_rebuild/phase3e/circularity_audit.csv` and
`outputs/auction_rebuild/phase3e/calibration_target_provenance.csv` for
why calibration status still matters for every number in this document.
This notice does not change any number in the report above; it only
corrects which label applies to it.
