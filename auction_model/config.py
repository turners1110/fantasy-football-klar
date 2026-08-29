"""League-specific configuration for the Fancy Football League auction model.

Every assumption a generic auction calculator gets wrong for this league is
encoded here, in one place, so it can be checked and edited without hunting
through the rest of the codebase. Values marked ASSUMPTION are not stated in
the league rules and were estimated -- override them if you have better
information.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Budget / teams
# ---------------------------------------------------------------------------

NUM_TEAMS = 12
BUDGET_PER_TEAM = 400
TOTAL_LEAGUE_BUDGET = NUM_TEAMS * BUDGET_PER_TEAM  # $4,800

MIN_PRICE = 1
MAX_PRICE = 100

# ---------------------------------------------------------------------------
# Roster construction
# ---------------------------------------------------------------------------

STARTING_LINEUP = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "FLEX": 3,  # W/R/T eligible only -- QB is NOT flex eligible in this league
}
BENCH_SPOTS = 6
IR_SPOTS = 2
MAX_KEEPERS_PER_TEAM = 6

# ASSUMPTION: how the 3 FLEX spots split across RB/WR/TE league-wide.
# No K/DEF and 3 FLEX spots meaningfully flatten scarcity at these positions
# relative to a standard 1-FLEX build; this split drives the replacement-level
# math below. Edit if your league's actual flex usage looks different.
FLEX_SHARE = {"RB": 0.45, "WR": 0.45, "TE": 0.10}

# ASSUMPTION: bench/IR roster-building demand by position, expressed as
# "extra startable-quality bodies per team" beyond the starting lineup.
# QBs are rarely double-rostered in a 1-QB league; RB/WR dominate bench/IR
# stashing; TE bench demand is low outside of the elite-TE handcuff case.
BENCH_DEMAND_PER_TEAM = {"QB": 0.3, "RB": 2.4, "WR": 2.4, "TE": 0.4}


def replacement_rank(position: str) -> int:
    """Return the league-wide rank at which 'replacement level' sits for a
    position, given this league's actual starting lineup + bench demand
    (not a generic redraft assumption)."""
    dedicated = STARTING_LINEUP.get(position, 0) * NUM_TEAMS
    flex = STARTING_LINEUP["FLEX"] * NUM_TEAMS * FLEX_SHARE.get(position, 0)
    bench = BENCH_DEMAND_PER_TEAM.get(position, 0) * NUM_TEAMS
    return round(dedicated + flex + bench)


# ---------------------------------------------------------------------------
# Keeper rules
# ---------------------------------------------------------------------------

KEEPER_BUMP_STANDARD = 10
KEEPER_BUMP_TAGGED = 5
FRANCHISE_TAGS_PER_TEAM = 1
PAUL_RULE_MIN_GAMES = 4  # played < this many games last season -> keep at same salary, no bump

# ASSUMPTION: default "obvious keep" heuristic band (dollars). A player is a
# default keeper candidate if their 2025 salary sits in this range -- cheap
# enough to be clear surplus value, expensive enough to signal they were a
# real starter and not a $1 depth stash nobody would burn a keeper slot on.
# Already-tagged players and Paul Rule (injury) cases are candidates
# regardless of price. This is deliberately conservative (most teams will
# NOT hit all 6 keeper slots under this default) -- override with
# keeper_overrides.csv once real decisions are known.
KEEPER_HEURISTIC_MIN_SALARY = 15
KEEPER_HEURISTIC_MAX_SALARY = 45

# ---------------------------------------------------------------------------
# Scoring (0.5 PPR / 6pt rush+rec TD / 4pt pass TD)
# ---------------------------------------------------------------------------
# NOTE: "standard yardage bonuses" from the prompt was left unconfirmed.
# Defaulting to NO milestone yardage bonuses (100 rush/rec, 300 pass, etc.)
# since leagues vary widely on thresholds. Fill in BONUS_THRESHOLDS below and
# re-run if this league actually uses them.

@dataclass(frozen=True)
class ScoringConfig:
    pass_yd: float = 0.04       # 1 pt / 25 yds
    pass_td: float = 4.0
    interception: float = -2.0
    rush_yd: float = 0.1        # 1 pt / 10 yds
    rush_td: float = 6.0
    reception: float = 0.5      # 0.5 PPR
    rec_yd: float = 0.1         # 1 pt / 10 yds
    rec_td: float = 6.0
    fumble_lost: float = -2.0
    two_pt: float = 2.0
    # ASSUMPTION: empty by default -- see note above.
    bonus_thresholds: dict = field(default_factory=dict)


SCORING = ScoringConfig()

STAT_COLUMNS = [
    "pass_yd", "pass_td", "interception",
    "rush_yd", "rush_td",
    "reception", "rec_yd", "rec_td",
    "fumble_lost", "two_pt",
]


def score_from_stats(stat_row: dict) -> float:
    """Score a raw stat line under this league's exact scoring rules."""
    points = 0.0
    for stat in STAT_COLUMNS:
        value = stat_row.get(stat)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        points += value * getattr(SCORING, stat)
    return round(points, 2)


# ---------------------------------------------------------------------------
# Sanity-check thresholds
# ---------------------------------------------------------------------------

BUDGET_TOLERANCE = 0.15          # flag if projected spend is off total budget by >15%
LARGE_MOVE_MULTIPLE = 2.0        # flag if suggested price differs from last salary by >2x
