# Phase 3C Final Report

**Status: PHASE 3C FAILED** (see section 21 for the full 17-condition tally. Real progress, including a genuine bug found and fixed with a measured effect, but the calibration harness and full-pool price distributions -- required for several conditions -- were not attempted this pass.)

## 1. Files changed

New scripts (all in `scripts/`): `build_phase3c_concentration_root_cause.py`, `build_phase3c_replacement_level_comparison.py`, `build_phase3c_flex_allocation_audit.py`, `build_phase3c_public_value_import.py`, `build_phase3c_bid_decomposition.py`, `build_phase3c_missing_projection_audit.py`, `build_phase3c_sam_exact_shortlist.py`.

Modified (production code, not diagnostics-only):
- `mock_draft/valuation.py` -- **real bug fix**: moved the early-draft-premium multiplier to apply *before* the star-ceiling re-clamp (was after, defeating the cap it exists to enforce); added optional `diagnostics` param to `compute_willingness` (fully additive, default `None`, zero behavior change for existing callers).
- `mock_draft/auction.py` -- added optional `bid_diagnostics_log` param to `resolve_bid`/`run_single_auction` (reuses the willingness call the bidding loop already makes; no extra RNG draws, no behavior change when omitted).

New data: `data/external/auction_values_2026/` (raw WebSearch snapshot + manual-import template).

New tests: `tests/test_phase3c_market_repair.py` (25 required tests).

No phase 1/2/2B/3A/3B files were rewritten. All prior commits and reports are preserved.

## 2. Public sources imported

Attempted via WebFetch: RotoWire, FantasyNerds, RotoAlpha (2 pages), DraftExpertPro, PropsBot, Footballguys, Yahoo Sports -- **all 8 returned `EGRESS_BLOCKED`**, confirmed as a total network-egress-proxy restriction of this execution environment (tested against `espn.com`, an unrelated domain, with an identical result) rather than a per-source failure.

WebSearch (a separate tool, not subject to the block) returned real, attributable consensus dollar values for **33 top-tier players** (24 RB/WR, 5 QB, 5 TE-adjacent) plus an independently-cited position-spend-share rule of thumb (RB 45% / WR 38% / QB 6% / TE 7%), saved verbatim to `data/external/auction_values_2026/websearch_consensus_2026.json`. This is disclosed as **lower confidence** than a verbatim single-page scrape (WebSearch's own answer synthesizes multiple cited sites, not one verified table). A manual-import CSV template was built for a human with direct browser/account access to complete later. Of the 33 players, **27 were already this league's own keepers** -- only 7 were auction-eligible matches, a small sample.

## 3. Projection changes

**None.** Per the explicit instruction, `data/projections_2026.csv` was never touched. `test_01_raw_projections_remain_unchanged_by_auction_calibration` verifies known players' point totals remain in a plausible range. The phase 3B RB-rescaling experiment that dropped RB spend share from 65.8% to 34.8% was, and remains, an in-memory diagnostic ablation only -- it never wrote to the source file.

## 4. Replacement-level findings

Built and compared 3 methods (`replacement_level_comparison.csv`):

| Method | QB share | RB share | WR share | TE share | Top-12 | Top-24 |
|---|---|---|---|---|---|---|
| A: Fixed-rank legacy (current production) | 4.2% | 57.0% | 32.8% | 6.1% | 34.1% | 53.9% |
| B: Demand-derived (real post-keeper open slots) | 4.8% | 63.2% | 29.1% | 2.9% | 44.8% | 67.5% |
| C: Optimization-derived (single leaguewide greedy fill) | 5.2% | 44.4% | 38.8% | 11.6% | 36.9% | 56.1% |

**Method C is dramatically closer to every other benchmark gathered this phase** (keeper-adjusted blended: RB 40.8%/WR 38.3%; WebSearch consensus: RB 45%/WR 38%) than the CURRENT PRODUCTION method (A), which alone accounts for a large share of RB's static-valuation overweight even before any bidding dynamics apply. **Not switched to production this phase** -- see section 22 for why, and recommended phase 3D scope.

## 5. FLEX-allocation findings

The hardcoded `FLEX_SHARE = {RB: 0.45, WR: 0.45, TE: 0.10}` does not match real optimized-lineup FLEX fill: after keepers, actual FLEX allocation across simulated final rosters is **RB 39.6% / WR 50.6% / TE 9.9%** -- WR is under-credited and RB over-credited by the current constant. (The low/high projection-scenario sensitivity test used a uniform +/-15% scalar, which -- as expected and disclosed -- produced no change, since a uniform scalar never changes relative rank; a real sensitivity test would need position- or player-specific noise, not attempted this pass.)

## 6. Concentration root cause

Ran 15 controlled bidding-layer experiments (`concentration_root_cause.csv`), measured POST-FIX (see section 7). Baseline top-12 share: **57.6%** (down from 63.7% pre-fix, purely from the bug fix below). Effect of removing each mechanism alone:

| Experiment | Top-12 share | Delta from baseline |
|---|---|---|
| Baseline | 57.6% | -- |
| All archetype multipliers = 1 | 35.6% | **-22.0pp** |
| No star multiplier alone | 38.1% | **-19.5pp** |
| Shared base values only | 37.3% | -20.3pp |
| Public anchors only | 34.5% | -23.1pp (closest to the historical/public range) |
| Strict value ceiling everywhere | 42.6% | -15.0pp |
| No early-draft premium | 55.8% | -1.8pp |
| No tier aggression | 56.6% | -1.0pp |
| No position-run pressure | 57.2% | -0.4pp |
| No emotional noise | 57.5% | -0.1pp |
| Winner pays 2nd-highest+$1 (repriced) | 57.7% | +0.1pp |
| Equal budgets | 57.4% | -0.2pp |

**Conclusion**: the star-ceiling override mechanism is overwhelmingly the dominant driver (removing it alone nearly matches removing every archetype multiplier combined); tier-aggression, early-draft premium, noise, nomination pull, and budget inequality each contribute only 0-2 points individually. The pricing RULE itself (experiment 9 vs. baseline) is not the driver -- willingness computation is.

## 7. Bid-premium changes

**Real bug found and fixed**: `mock_draft/valuation.py`'s star-ceiling re-clamp (meant to bound star-eligible bids at 2.5x base value) ran *before* the early-draft-premium multiplier, so the premium multiplied the already-capped value right back past the cap. A sampled case (Jaylen Waddle, base_value $64) showed `final_willingness` $251.56 -- 3.93x base_value, despite the documented 2.5x hard cap. **Fixed** by moving the premium above the re-clamp. Verified: zero star-candidate sales now exceed 2.5x base_value across every batch run since (was previously routine -- 34.1% of top-24 sales still show 2+ active premiums stacking, but now bounded). This single fix alone dropped baseline top-12 concentration from 63.7% to 57.6%.

The broader item 8 ask (rebuild willingness as a bounded additive/log structure with configurable maximum adjustments) was **not attempted** -- only the one specific, measured, verified stacking bug was fixed. Non-star candidates still compound tier/tilt/position-fit/early-draft multipliers with no overall cap.

## 8. Historical and public target ranges

Combining all evidence gathered across phases 3B-3C:

| Signal | Top-12 share | RB | WR | QB | TE |
|---|---|---|---|---|---|
| Historical (6 versions, phase 3B) | 23.7-25.5% | -- | -- | -- | -- |
| Public rank/tier (FantasyPros, phase 3B) | 31.6% | -- | -- | -- | -- |
| Existing neutral valuation (phase 3B) | 30.7% | -- | -- | -- | -- |
| Replacement Method A (current production) | 34.1% | 57.0% | 32.8% | 4.2% | 6.1% |
| Replacement Method C (optimization-derived) | 36.9% | 44.4% | 38.8% | 5.2% | 11.6% |
| Keeper-adjusted blended (phase 3B) | -- | 40.8% | 38.3% | 13.1% | 7.8% |
| WebSearch public consensus | -- | 45% | 38% | 6% | 7% |
| **User's suggested initial range** | 24-36% | 34-48% | 32-46% | 8-16% | 5-11% |

The suggested initial range is **mostly well-supported** by this phase's new evidence, with two minor documented exceptions: Method C's top-12 (36.9%) sits just above the suggested 36% ceiling, and Method C's TE share (11.6%) sits just above the suggested 11% ceiling. Given only one additional data point pushes past each bound, **the suggested ranges are kept as-is** rather than widened on thin evidence -- this is disclosed, not silently overridden.

## 9. Before/after top-12 share

63.7% (pre-fix, this phase's own re-measurement) -> **57.6%** (post-fix) -- a real, verified 6.1-point reduction from one bug fix alone, still far above every benchmark range (24-36%).

## 10. Before/after top-24 share

82.3% (pre-fix) -> **78.0%** (post-fix).

## 11. Before/after position spending

Not re-measured with the willingness fix in this report (the concentration experiments tracked top-12/24 and spend-to-base ratios, not a full post-fix position-share recompute) -- flagged as a gap; the phase 3B figures (QB 3.2%/RB 65.8%/WR 28.9%/TE 2.2%) predate this fix and should be considered stale pending a fresh measurement.

## 12. Total spending and unused cash

Stable across every experiment: ~$2,890-2,920 spent per auction (~96-97% of the ~$3,021 league cash), consistent with phase 3A/3B's findings. Unaffected by the willingness fix or root-cause experiments (only the top-12/24 SHAPE changed, not the total amount).

## 13. Missing-projection handling

Audited all 158 rows in `data/projections_2026.csv` with no `projected_points` value (`missing_projection_audit.csv`): **95 unlikely to be drafted, 62 likely $1 players, only 1 (Zach Charbonnet) a genuinely relevant auction target** with real base_value but no matched projection. Confirms most of these 158 rows do not warrant further engineering effort, per the instruction not to spend equal effort on irrelevant players.

## 14. Counterfactual accuracy

**Not improved this phase.** The existing measurement stands unchanged from phase 3A: mean absolute error 63.59 points, max 263.14 points, still outside the required thresholds. The hybrid exact/approximate solver (item 13) was not built. What WAS done instead: a scoped exact-solver audit for Sam's 8-player phase-3B watchlist (`sam_exact_shortlist.csv`), using the real ILP solver at each player's actual simulated market price -- **Josh Jacobs at his real $201.50 market price shows an exact surplus of -$329** (strongly confirms the RB is dramatically overpriced relative to any legitimate roster-value gain), while Josh Allen at $21.95 shows +$31.36 (the only clearly positive QB scenario of the 8).

## 15. Calibration design

**Not attempted this phase.** No grid/Bayesian search was run. This is explicitly why conditions 6-8 (held-out ranges) cannot be assessed -- there is no held-out result to check.

## 16. Validation results / 17. Held-out results

**Not applicable** -- no calibration was run, so no validation or held-out seed groups exist. `validation_results.json` and `held_out_results.json` were not produced.

## 18. Full-pool price coverage

**Not built.** Only the 8-player phase-3B watchlist has price distributions with observation counts and percentiles. The full ~320-player auction-eligible pool (item 15) was not simulated for this purpose this phase.

## 19. Sam exact-shortlist coverage

**Partial.** 8 of the 8 required player GROUPS were not all covered (only the phase-3B watchlist subset, not top-20-overall/top-20-WR/top-15-TE/top-15-RB/top-10-QB/top-20-by-surplus/every-P50>=$20/every-prior-target as separately enumerated groups). What exists is real: 8 players, exact ILP solves (not approximated), at their real simulated market prices, with surplus, budget-after-purchase, and solver status/runtime all reported.

## 20. Tests

**184 of 184 tests pass** (159 prior + 25 new in `tests/test_phase3c_market_repair.py`, covering all 25 items required by item 17, including a direct regression test for the star-ceiling stacking fix).

## 21. PHASE 3C PASSED/FAILED determination

**PHASE 3C FAILED** -- tally against the 17 required conditions:

| # | Condition | Status |
|---|---|---|
| 1 | Raw projections not manipulated to hit market targets | MET |
| 2 | Replacement levels and FLEX allocation are demand-derived | **NOT MET in production** -- audited and a better method (C) identified, but production still uses the fixed-rank method |
| 3 | >=2 external public sources imported, or all failures documented with 1 valid source | MET |
| 4 | Top-heaviness root cause identified | MET |
| 5 | Compounding bid premiums removed or bounded | **PARTIAL** -- the one measured stacking bug (star reclamp vs. early-draft premium) is fixed and verified; the broader bounded-additive willingness redesign (item 8) was not built |
| 6 | Held-out top-12 share in supported range | **NOT MET** -- no calibration/held-out harness exists |
| 7 | Held-out top-24 share in supported range | **NOT MET** -- same reason |
| 8 | Held-out position shares in supported ranges | **NOT MET** -- same reason |
| 9 | Total spending remains plausible | MET |
| 10 | Unused cash remains plausible | MET |
| 11 | Counterfactual accuracy meets thresholds, or Sam's shortlist uses exact solves | **PARTIAL** -- broad-pool accuracy unimproved; a real but scoped (8-player) exact-solve shortlist exists |
| 12 | Full-pool price distributions exist | **NOT MET** -- only an 8-player watchlist exists |
| 13 | Every price includes uncertainty and sample count | **PARTIAL** -- true for the 8-player watchlist, not applicable at full-pool scale (doesn't exist) |
| 14 | Legal roster and lineup rate remains 100% | MET |
| 15 | All tests pass | MET (184/184) |
| 16 | No evolution ran | MET |
| 17 | No final draft strategy published | MET |

9 of 17 cleanly met, 4 partial, 4 not met -- consistent with this project's standing rule of reporting the honest tally rather than rounding a partial result up to a pass.

## 22. Remaining weaknesses

1. **Replacement-level Method C was identified as clearly superior but not adopted in production** -- deliberately, since switching `auction_model.valuation.compute_replacement_baseline`'s live behavior is a real, separate engineering change with its own regression risk, and this phase's priority (per the explicit "measure before tuning" instruction) was root-causing, not remediating every finding.
2. **The star-ceiling mechanism itself remains the dominant concentration driver** even after the ordering-bug fix (57.6% vs. a 24-36% target range) -- the *bug* is fixed, but the underlying design (a 2.5x-value override for up to 4 "stars" per team, depending on archetype) still produces excess concentration on its own. Bounding it further is calibration work, appropriately deferred to item 14.
3. **No calibration harness exists** -- the single highest-leverage remaining project. Every "held-out" acceptance condition is structurally unverifiable without it.
4. **Full-pool price distributions and the complete Sam shortlist remain unbuilt** at the scale requested.
5. **Counterfactual approximation accuracy is unchanged** and still well outside stated thresholds for broad-pool use.
6. **Position-spending shares were not re-measured** after the willingness fix -- the phase 3B figures now predate a real behavior change and should not be treated as current.

## 23. Exact rerun commands

```bash
cd fantasy-football-klar

# Full test suite (184 tests)
python3 -m pytest tests/ -q
python3 -m pytest tests/test_phase3c_market_repair.py -q

# Concentration root-cause experiments (15 configs x 30 seeds, ~9 min)
python3 scripts/build_phase3c_concentration_root_cause.py

# Bid-construction decomposition audit (50 seeds, ~1 min)
python3 scripts/build_phase3c_bid_decomposition.py

# Replacement-level comparison (3 methods)
python3 scripts/build_phase3c_replacement_level_comparison.py

# FLEX allocation audit
python3 scripts/build_phase3c_flex_allocation_audit.py

# Public value import + normalization (WebSearch-based, WebFetch blocked)
python3 scripts/build_phase3c_public_value_import.py

# Missing-projection audit
python3 scripts/build_phase3c_missing_projection_audit.py

# Sam exact-shortlist audit (8 players, real ILP solves, ~25s)
python3 scripts/build_phase3c_sam_exact_shortlist.py
```

## 24. Recommended Phase 3D scope

1. **Adopt replacement-level Method C (optimization-derived) in production** -- the single highest-confidence, lowest-risk remaining fix, already measured to bring RB share from 57.0% to 44.4% at the static-valuation level alone.
2. **Build the actual calibration harness** (item 14) -- the precondition for every held-out acceptance condition this phase could not verify. Use the star-ceiling mechanism (now confirmed as the dominant lever) as a primary calibration target, not a hand-tuned guess.
3. **Re-measure position spending and concentration together, post both fixes** (the willingness ordering bug AND, once adopted, replacement Method C) before drawing conclusions about what still needs calibration.
4. **Build the hybrid exact/approximate counterfactual solver** (item 13) -- needed before any broad-pool bid-ceiling claim can be trusted.
5. **Extend price-distribution and Sam-shortlist coverage to the full required scope** once the above are in place -- calibrating or publishing prices against a market that's still known to be over-concentrated would just formalize the wrong answer.
6. If pursuing better public-value coverage, a human with direct browser access should complete `data/external/auction_values_2026/manual_import_template.csv` from the sources this session's WebFetch could not reach.
