# Phase 3A Final Report

**Status: PHASE 3A FAILED** (10 of 12 required conditions cleanly met -- see section 17. Consistent with this project's standing rule: a partial result is reported as failed, not rounded up.)

## 1. Files changed

New:
- `mock_draft/cash_value.py` -- item 8 terminal/marginal dollar value
- `mock_draft/counterfactual.py` -- item 7 counterfactual bid ceiling
- `scripts/build_phase3a_budget_reconciliation.py`
- `scripts/build_historical_qb_roster_audit.py`
- `scripts/build_eligibility_evidence_audit.py`
- `scripts/build_salary_origin_audit.py`
- `scripts/build_unspent_cash_decomposition.py`
- `scripts/build_market_clearing_diagnostics.py`
- `scripts/build_counterfactual_approximation_error.py`
- `scripts/build_formula_reconciled_team_states.py`
- `scripts/build_sam_sanity_tests.py`
- `scripts/build_phase3a_calibration_comparison.py`
- `scripts/run_phase3a_simulation_gate.py`
- `scripts/build_phase3a_acceptance_test.py`
- `tests/test_auction_rebuild_phase3a.py` (20 required tests)

Modified:
- `data/team_budget_adjustments_2026.csv` -- confirmed Brandon/Sam trade
- `auction_model/auction_eligibility.py` -- multi-source active-player evidence
- `mock_draft/auction.py` -- **root-cause fix**: `_incremental_utility` now uses `partial_lineup_value`
- `mock_draft/legal_lineup.py` -- added `partial_lineup_value`; reverted the bench-weight retune
- `mock_draft/data.py`, `mock_draft/points.py`, `mock_draft/feasibility.py` -- carried over from the eligibility/points-fallback fixes
- `scripts/build_historical_roster_position_counts.py` -- deduplication fix
- `scripts/build_eligibility_path_reconciliation.py` -- simplified now both paths agree
- `tests/test_auction_rebuild_phase2b.py` -- rebuilt `test_10` on a legal roster

## 2. Budget reconciliation

Confirmed trade (USER_CONFIRMED_TRADE): Sam sends $15 cash (+ a 3rd-round college pick, no auction-cash effect) to Brandon for Cam Skattebo. Sam's row: -$15. Brandon's row: +$15. Net internal transfer: **$0** (verified by test).

`outputs/auction_rebuild/phase3a/team_budget_reconciliation.csv`: Brad/CJ/Coby/James/Shane fully RESOLVED (sheet matches formula exactly). Sam is EXPLAINED_OVERRIDE ($223 = $400 - $162 keeper spend - $15 cash sent, authoritative over the sheet's stale $225 by user confirmation). Six teams (Brandon, Evan, Jason, Reid, Ryan J, Travis) remain UNRESOLVED_GAP, differences of -$23 to +$10 relative to the naive formula, with no confirmed trade to explain any of them.

## 3. Remaining unexplained budget difference

**$43 unresolved across 6 teams** (sum of absolute differences), unchanged from phase 2B except for Brandon's row, which now correctly includes the confirmed +$15 (previously inferred, now confirmed) -- this did NOT close his gap (formula says $194, sheet says $184, still -$10 unexplained). Not distributed across players or hidden in inflation, per instruction. Both REPORTED and FORMULA_RECONCILED scenarios were built and validated in the simulation gate (section 14).

## 4. Eligibility changes

Replaced the single-file nflverse-only active-player check with `_active_player_registry_evidence`, merging nflverse (absent in this environment), `data/actuals_2025.csv`, `data/fantasy_data_last_year_clean.csv`, and `data/projections_2026.csv` (current-season projection alone counts, covering real rookies with zero 2025 games). Decision order now: college rights > keeper > historical-with-salary > historical-no-salary > any active-player evidence > fp_only. Both production paths (`run_valuation.py`, mock-draft simulator) now agree with **zero differences** (was 39 explained differences under the old `fp_only_fallback_eligible` flag). `eligibility_evidence_audit.csv`: 320 included players, each with its evidence source attached.

## 5. Historical QB findings

No draft-day transaction/acquisition-method log exists anywhere in this repo (confirmed by search). Every non-keeper 2025 QB salary is UNKNOWN origin. After fixing a duplicate-row bug (a genuine duplicate Kyler Murray salary row had inflated one team's QB count from 3 to 4 -- this had directly caused phase 2B's incorrect `QB=4` default), the real 2025 maximum is **3 QB roster spots on one team**, with no acquisition-method evidence establishing any of them as a competitive-auction win. Corrected caps: `PRIMARY_QB_CAP=2`, `STRESS_TEST_QB_CAP=3`, `HISTORICAL_OBSERVED_QB_CAP=3` (not 4).

## 6. Unused-cash root cause

Root cause was **not** the bench-weight hypothesis tried first. Direct instrumentation of `_incremental_utility` found ~83-99.8% of "zero/negative incremental utility" blocks were teams whose roster hadn't yet completed a legal starting lineup -- `build_production_lineup`'s `total_roster_utility` is hard-zeroed to 0 for any illegal/incomplete roster, so `before=0` and `after=0` for nearly every bid regardless of bench weight. Fixed with `partial_lineup_value`, a best-effort, non-zeroing scorer used only by the live bid gate; `build_production_lineup` is untouched (still correct for final-roster fitness in `evolution.py`/`best_response.py`). The bench-weight retune (tried first, measured to have zero effect, per the log in `mock_draft/legal_lineup.py`) was reverted.

## 7. Counterfactual bid-ceiling design

`mock_draft/counterfactual.py`: Scenario A (win at price, complete via cached greedy fill) vs Scenario B (pass, complete via the same greedy fill excluding the candidate from both pools). Terminal cash value (`mock_draft/cash_value.py`) is credited to each scenario's leftover budget so price actually matters (a documented bug -- without it, marginal utility was price-invariant). `hard_bid_ceiling` uses a coarse grid + binary-search refinement. **Approximation error measured** against the exact ILP solver (`auction_model/exact_roster_solver.py`) on 15 sampled states: mean absolute error 63.59 points, max 263.14 points on starting-lineup points -- disclosed, not hidden. Deliberately **not** wired into live bidding: it has a documented rival-competition blind spot (assumes any remaining pool player is available to this team) that would make unspent cash worse, and the exact solver is computationally infeasible at 200-seed bid-loop scale.

## 8. Historical salary-data quality

`salary_origin_audit.csv` (191 rows): 10 FRANCHISE_TAG, 122 UNKNOWN, 59 ADMINISTRATIVE_DOLLAR_ONE. Zero rows are confirmed COMPETITIVE_AUCTION (no transaction log exists to confirm any). 113 included in market calibration (unadorned non-$1 salaries, reliability 0.5 -- limited weight); 78 excluded (mostly the $1 administrative rows, reliability 0, per "unknown $1 prices receive zero weight by default").

## 9. Before/after unused cash

| | Before (phase 2B/3A pre-fix) | After (this fix) |
|---|---|---|
| % league cash spent | 26.8% | 96.5% |
| Mean unused cash/team | $184.34 | $8.88 (40-seed), $8.52 (200-seed validation) |
| Median unused cash/team | $194.51 | $0.10-1.65 across all tested scenarios |
| Max unused cash/team | $315.00 | $57.65-88.19 across scenarios |
| Zero-utility blocks | 99.8% of all blocked bids | 0% (100% now legitimate budget exhaustion) |

## 10. Before/after QB counts

Before: `DEFAULT_POSITION_MAX["QB"]=4` (based on a duplicate-row data bug). After: primary cap 2, stress-test cap 3, historical-observed max 3 (corrected). 200-seed validation under cap=2: 525 team-seeds with 1 QB, 1,875 with 2 QB, **zero** exceeding the cap. Stress-test (cap=3, 50 seeds): distribution 183/220/197 across 1/2/3 QB, max observed exactly 3 -- cap respected in every configuration tested.

## 11. Before/after position spending

Before: QB $112.80, RB $294.64, TE $100.05, WR $301.37 per auction (on a mostly-unspent budget). After: QB $92.23, RB $1,917.22, TE $63.85, WR $841.14 per auction (reflecting the much higher total spend). As a **share** of total spend, simulated is RB 65.8% / WR 28.9% / QB 3.2% / TE 2.2%, vs. the historical reliable-subset shape of RB 42.1% / WR 39.0% / QB 11.6% / TE 7.4%. **Same rank order** (RB > WR > QB > TE in both) but a real, disclosed magnitude gap -- the simulator overweights RB and underweights QB/TE relative to history. This is condition 9's shortfall (section 17).

## 12. Before/after top-player concentration

Simulated top-12 spend share: 2.93% (before) -> 2.95% (after, effectively unchanged). Simulated top-24: 5.23% -> 5.76%. No comparable real top-12/24 concentration figure exists (most historical salaries are UNKNOWN origin) -- reported as a self-consistency figure only, not a validated before/after against real history.

## 13. Test results

**138 of 138 tests pass** (118 pre-existing + 20 new Phase 3A tests in `tests/test_auction_rebuild_phase3a.py`, covering all 20 items required by item 15). One pre-existing test (`test_10_third_quarterback_has_zero_incremental_utility`) was rebuilt on a fully legal roster after discovering it was a false positive (passing only because both sides of its illegal 2-player roster were hard-zeroed, not because the third-QB weight was actually being exercised).

## 14. 200-seed results

Primary configuration (Sam primary budget, REPORTED league budgets, QB cap 2, default nomination temperature), 200 seeds, 2,400 team-seed observations: **0 negative budgets, 0 illegal final lineups, 0 duplicate/wrong-size rosters, 0 accounting leaks, 100% legal-lineup rate.** Mean unused cash $8.52/team, median $0.10, max $67.65.

Cross-checks (50 seeds each, seed-disjoint from the primary run): Sam conversions-scenario budget, FORMULA_RECONCILED league budgets, QB cap stress-test (3), and two nomination-temperature variants (0.3 "sharper" and 1.2 "flatter") -- **all six configurations** show 0 negative budgets, 0 illegal lineups, 0 accounting leaks, 100% legal-lineup rate, and QB counts never exceeding their configured cap. Mean unused cash stable across all axes ($7.75-$9.79/team). Full 200-seed runs were not performed on every one of the ~24 possible axis combinations (disclosed scoping decision in `scripts/run_phase3a_simulation_gate.py`'s docstring) -- the primary configuration got the full 200 seeds exactly as required; every other axis got a real, seed-disjoint 50-seed cross-check.

## 15. Sam sanity-test results

Using Sam's real 6 keepers and $223 budget (`outputs/auction_rebuild/phase3a/sam_sanity_tests.json`): her roster holds **zero TEs**, making a TE acquisition her one hard structural need (illegal lineup, MISSING_TE, until filled). Results (marginal utility net of opportunity cost): cheap backup QB $13.07, mid-tier backup QB $24.18, expensive QB (Josh Allen) $66.36, premium WR (Rashee Rice) $179.43, premium TE (TJ Hockenson) $92.79, another premium RB (Josh Jacobs) $158.41 (worst per-dollar return of the three "premium" scenarios, since RB is her one position with real existing depth), two strong FLEX adds $310.68. **Confirms item 14's own prediction exactly**: the expensive 2nd QB ($66.36) loses to the similar-cost premium TE ($92.79) -- Dart is not displaced by enough margin to justify the QB spend over filling the real TE need.

## 16. Remaining blockers

1. **$43 leaguewide budget gap** across 6 teams remains genuinely unresolved -- no confirmed trade or adjustment explains it; both REPORTED and FORMULA_RECONCILED scenarios are built, tested, and simulation-validated as parallel tracks, but the gap itself is not closed.
2. **Market-spending shape**: same rank order as history (RB>WR>QB>TE) but real magnitude divergence (RB overweighted ~66% vs ~42% historical share; QB/TE underweighted). Root cause not yet diagnosed -- candidate causes (archetype valuation weights, willingness formula, nomination pull toward RB) not yet isolated.
3. **No real per-team historical unused-cash record exists** in this repo for any past season -- items 13's conditions 6/7 are structurally unverifiable, not merely unmeasured. This is a standing data limitation, not a code defect, but it means "falls in a history-supported range" cannot be proven true even though the current result (single-digit % unused) looks qualitatively healthy.
4. Calibration comparison (item 12) found no predictor with a clear edge -- expected, given only one, mostly-UNKNOWN-origin season of salary data exists; a real second data source (an actual draft-day log) would be needed to do better.

## 17. PASSED/FAILED determination

**PHASE 3A FAILED** -- 10 of 12 required conditions cleanly met:

| # | Condition | Status |
|---|---|---|
| 1 | Brandon's $15 recorded | MET |
| 2 | Sam's $223/$221 reconcile | MET |
| 3 | Eligibility no longer depends on nflverse alone | MET |
| 4 | Historical QB origins audited | MET |
| 5 | Counterfactual ceilings work | MET |
| 6 | Average unused cash falls in history-supported range | **NOT MET -- no historical range exists to test against (structural data gap); current result is qualitatively plausible but unverifiable** |
| 7 | No forced spending | MET |
| 8 | All rosters/lineups legal | MET (100% across 500+ simulated seeds) |
| 9 | Market-spending shape resembles history | **NOT MET -- same rank order, but real magnitude divergence (RB overweighted, QB/TE underweighted)** |
| 10 | All tests pass | MET (138/138) |
| 11 | Exact rerun commands included | MET (section 18) |
| 12 | No final strategy claim published | MET |

Per this project's standing rule (established in phase 2B: report the honest tally, never round a partial result up), a 10/12 result is reported as **FAILED**, not a qualified pass. The two unmet conditions are materially different in kind from phase 2B's failures (illegal rosters existing, a bypassed eligibility path, an untouched gap) -- these are a disclosed structural data limitation (no historical per-team cash record ever existed) and a measured, non-trivial spend-shape gap -- but the spec defines a binary gate, and 2 of 12 conditions are not cleanly met.

## 18. Exact rerun commands

```bash
cd fantasy-football-klar

# Full test suite (138 tests)
python3 -m pytest tests/ -q

# Just the 20 Phase 3A required tests
python3 -m pytest tests/test_auction_rebuild_phase3a.py -q

# Budget reconciliation
python3 scripts/build_phase3a_budget_reconciliation.py
python3 scripts/build_formula_reconciled_team_states.py

# Eligibility
python3 scripts/build_eligibility_evidence_audit.py
python3 scripts/build_eligibility_path_reconciliation.py

# Historical QB / salary-origin audits
python3 scripts/build_historical_qb_roster_audit.py
python3 scripts/build_historical_roster_position_counts.py
python3 scripts/build_salary_origin_audit.py

# Unused-cash diagnostics
python3 scripts/build_unspent_cash_decomposition.py
python3 scripts/build_market_clearing_diagnostics.py

# Counterfactual bid ceiling / approximation error
python3 scripts/build_counterfactual_approximation_error.py

# Calibration + Sam sanity tests
python3 scripts/build_phase3a_calibration_comparison.py
python3 scripts/build_sam_sanity_tests.py

# Full simulation gate (20 dev + 200 validation + 250 cross-check seeds, ~10-15 min)
python3 scripts/run_phase3a_simulation_gate.py
python3 scripts/build_phase3a_acceptance_test.py
```

## 19. Recommended Phase 3B scope

1. Diagnose and address the RB-overweight / QB-TE-underweight spend-shape gap (condition 9) -- likely in archetype valuation weights or the nomination-pull formula, not the bid gate this phase fixed.
2. Make a final, explicit decision on the $43 leaguewide budget gap: either obtain a real source resolving the 6 remaining teams, or formally adopt one of the two parallel scenarios (REPORTED or FORMULA_RECONCILED) as authoritative with the other retired.
3. If a real draft-day transaction log can be obtained from the league (Yahoo/ESPN/whatever platform hosted the actual live auction), re-run the salary-origin audit and calibration comparison against confirmed competitive-auction prices instead of the current mostly-UNKNOWN-origin subset -- this would meaningfully improve items 6, 9, 12, and 13's verifiability.
4. Do not publish final draft strategy or price advice until conditions 6 and 9 are resolved or the acceptance bar is explicitly revisited.
