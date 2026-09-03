#!/usr/bin/env python3
"""Write outputs/auction_rebuild/audit/fitness_call_site_audit.csv -- every
call site the rebuild spec's search terms (total_points, projected_points.sum,
sum(projected_points, roster points, fitness, objective, score) turned up
across the repo, and what (if anything) changed in phase 2.

Not a re-run of the search itself (that was done by hand via grep across
the whole repo during phase 2) -- this records the audit's conclusions per
file/function so the trail survives independent of this session's grep
history.
"""

from __future__ import annotations

import csv
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
OUT_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "audit"
OUT_DIR.mkdir(parents=True, exist_ok=True)

ROWS = [
    {
        "file": "mock_draft/models.py", "function": "Team.total_points",
        "old_metric": "sum(points) over all 15 rostered players, equal weight",
        "new_metric": "n/a (property itself unchanged, now deprecated)",
        "changed": "PARTIAL",
        "reason": "Kept as a raw diagnostic figure (some external scripts may still read it), "
                  "but now emits DeprecationWarning on every access and its docstring names the "
                  "replacement. No production fitness/strategy-selection path calls it any more "
                  "(verified: exercising evolution.py + best_response.py's full call paths with "
                  "DeprecationWarning promoted to an error raised zero warnings).",
        "remaining_risk": "Any future script that calls team.total_points expecting it to represent "
                          "fitness would silently get the old bugged metric back (with a warning, "
                          "not a hard failure) -- acceptable since it's diagnostic-only, not a "
                          "silent-fallback risk for confirmed keeper/eligibility logic.",
    },
    {
        "file": "mock_draft/evolution.py", "function": "compute_team_baselines",
        "old_metric": "team.total_points (all-15-rostered-players sum)",
        "new_metric": "legal_lineup.build_production_lineup(team.roster).total_roster_utility",
        "changed": "YES",
        "reason": "Team-baseline normalization must be computed on the same objective the "
                  "genomes are scored against, or the baseline subtraction is comparing two "
                  "different metrics.",
        "remaining_risk": "None identified -- verified no DeprecationWarning fires when exercised.",
    },
    {
        "file": "mock_draft/evolution.py", "function": "evaluate_generation",
        "old_metric": "final_teams[team_name].total_points - baselines[team_name]",
        "new_metric": "legal_lineup.build_production_lineup(final_teams[team_name].roster)"
                      ".total_roster_utility - baselines[team_name]",
        "changed": "YES",
        "reason": "This is the actual genome fitness signal driving selection/crossover/mutation "
                  "-- the single most important call site in the rebuild spec's goal 2. An illegal "
                  "final roster now scores 0 utility for that match instead of an inflated illegal "
                  "point total.",
        "remaining_risk": "None identified.",
    },
    {
        "file": "run_evolution.py", "function": "benchmark_hand_designed_archetypes",
        "old_metric": "team.total_points - team_baselines[name]",
        "new_metric": "legal_lineup.build_production_lineup(team.roster).total_roster_utility - team_baselines[name]",
        "changed": "YES",
        "reason": "Sanity-check benchmark must use the same objective as the training loop it is "
                  "sanity-checking, or the '~0 by construction' invariant the docstring claims "
                  "no longer holds.",
        "remaining_risk": "None identified. NOTE: this script was not executed during phase 2 -- "
                          "phase 2 explicitly prohibits running evolution. The fix is code-only, "
                          "verified by inspection and by the shared build_production_lineup unit "
                          "tests, not by a live run of this script.",
    },
    {
        "file": "mock_draft/best_response.py", "function": "evaluate_best_response",
        "old_metric": "final_teams[my_team].total_points - team_baselines[my_team]",
        "new_metric": "build_production_lineup(final_teams[my_team].roster).total_roster_utility - team_baselines[my_team]",
        "changed": "YES",
        "reason": "This is the 'what strategy should I personally play' comparison the user asked "
                  "for -- must be judged on legally-startable points, not raw roster sum.",
        "remaining_risk": "None identified.",
    },
    {
        "file": "mock_draft/genome.py, mock_draft/archetypes.py, mock_draft/nomination.py, "
                "mock_draft/valuation.py, mock_draft/auction.py",
        "function": "n/a",
        "old_metric": "n/a", "new_metric": "n/a", "changed": "NO",
        "reason": "Grepped for total_points/fitness/objective/score -- no matches. These modules "
                  "define bidding/nomination behavior and genome representation; they consume "
                  "Player.projected_points as raw per-player input to willingness calculations "
                  "(a price/demand signal, not a roster-level fitness metric) and were never part "
                  "of the naive-sum-of-15 bug.",
        "remaining_risk": "None identified.",
    },
    {
        "file": "audit_qb_arbitrage.py", "function": "main",
        "old_metric": "old_objective_points = sum(pts for roster) -- computed deliberately, "
                      "for comparison against the new metric",
        "new_metric": "legal.total_roster_utility via mock_draft/legal_lineup.py's PHASE-1 "
                      "select_legal_lineup (preserved exactly, not the new production function)",
        "changed": "NO (by design -- preserved phase-1 file)",
        "reason": "This script's entire purpose is reproducing and quantifying the OLD bug "
                  "alongside the NEW corrected metric for the audit trail. Its 'old_objective_points' "
                  "is deliberately the retracted metric for comparison, not a live strategy-selection "
                  "path -- nothing downstream treats it as fitness.",
        "remaining_risk": "None -- explicitly a preserved phase-1 audit artifact per the rebuild spec.",
    },
    {
        "file": "auction_model/roster_optimizer.py", "function": "_objective, LineupResult",
        "old_metric": "n/a", "new_metric": "n/a", "changed": "NO",
        "reason": "This is a SEPARATE, pre-existing real-valuation pipeline (Sam-specific keeper/"
                  "auction portfolio optimizer used by run_sam_keeper_analysis.py and "
                  "run_keeper_decisions.py), not the mock_draft evolutionary simulator this phase's "
                  "goal 2 targets. It already restricts scoring to a legal 1QB/2RB/2WR/1TE/3FLEX "
                  "starting lineup (STARTER_REQ) plus a flat-weighted bench term "
                  "(config.ROSTER_UNUSED_CASH_WEIGHT / ROSTER_BENCH_WEIGHT) -- it never had the "
                  "all-15-rostered-players bug being fixed here.",
        "remaining_risk": "Its bench weighting is a single flat multiplier, not the new tiered "
                          "per-position-depth weights in legal_lineup.PRODUCTION_BENCH_WEIGHTS -- "
                          "a real inconsistency between the two pipelines' bench treatment, but "
                          "reconciling them is outside phase 2's three goals (which are scoped to "
                          "the mock-draft evolutionary simulator) and is flagged here for a future "
                          "phase rather than silently left unmentioned.",
    },
    {
        "file": "run_keeper_decisions.py, run_sam_keeper_analysis.py, backtest_2025.py, "
                "auction_model/confidence.py, auction_model/college_prospects.py, "
                "scripts/process_fantasy_data_xlsx.py",
        "function": "score / confidence_score / prospect_value_score / backtest score()",
        "old_metric": "n/a", "new_metric": "n/a", "changed": "NO",
        "reason": "These 'score' hits are unrelated confidence/backtest/prospect-grading scores "
                  "(0-10 confidence scales, backtest error metrics, college-prospect grades) -- not "
                  "roster fitness, not affected by the QB-arbitrage bug, and outside the mock-draft "
                  "evolutionary simulator this phase's goal 2 addresses.",
        "remaining_risk": "None identified.",
    },
]


def main() -> None:
    path = OUT_DIR / "fitness_call_site_audit.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "function", "old_metric", "new_metric", "changed", "reason", "remaining_risk"])
        w.writeheader()
        w.writerows(ROWS)
    print(f"Wrote {path} ({len(ROWS)} rows)")


if __name__ == "__main__":
    main()
