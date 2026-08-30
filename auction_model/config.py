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
# v4: the $100 ceiling is REMOVED per methodology v4 Part 7 -- it was an
# arbitrary cap with no league-rule basis, and it was silently absorbing
# real value at the top of the board (see v3 changelog: 2 players hit it
# and their "excess" required a water-filling correction to get budget
# reconciliation back to exact). MAX_PRICE is kept only as an optional,
# explicitly-off ceiling for callers that want one -- None means no cap.
MAX_PRICE = None

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
TOTAL_ROSTER_SPOTS_PER_TEAM = (
    sum(v for k, v in {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 3}.items()) + BENCH_SPOTS + IR_SPOTS
)
# Active auction roster = starters + bench (IR is optional injured reserve, not required at auction)
ACTIVE_STARTER_SLOTS = sum(STARTING_LINEUP.values())  # 9
STARTING_ROSTER_SIZE = ACTIVE_STARTER_SLOTS  # 9
BENCH_SIZE = BENCH_SPOTS  # 6
ACTIVE_ROSTER_SIZE = STARTING_ROSTER_SIZE + BENCH_SIZE  # 15
IR_CAPACITY = IR_SPOTS  # 2 optional; not required auction purchases
REQUIRED_AUCTION_ROSTER_SIZE = ACTIVE_ROSTER_SIZE  # 15
AUCTION_PURCHASE_REQUIREMENT = REQUIRED_AUCTION_ROSTER_SIZE

assert STARTING_ROSTER_SIZE + BENCH_SIZE == ACTIVE_ROSTER_SIZE, (
    f"STARTING_ROSTER_SIZE ({STARTING_ROSTER_SIZE}) + BENCH_SIZE ({BENCH_SIZE}) "
    f"must equal ACTIVE_ROSTER_SIZE ({ACTIVE_ROSTER_SIZE})"
)

# Roster optimizer objective weights (starting points dominate)
BENCH_POINT_WEIGHT = 0.15
BENCH_UPSIDE_WEIGHT = 0.0
RISK_PENALTY_WEIGHT = 0.0
UNUSED_CASH_WEIGHT = 0.0
STARTING_POINT_TOLERANCE = 0.5
ROSTER_BENCH_WEIGHT = BENCH_POINT_WEIGHT
ROSTER_RISK_PENALTY = RISK_PENALTY_WEIGHT
ROSTER_UNUSED_CASH_WEIGHT = UNUSED_CASH_WEIGHT
ROSTER_UPSIDE_WEIGHT = BENCH_UPSIDE_WEIGHT
FINAL_SOLVER_MODE = "exact"  # exact | greedy_diagnostic
MAX_KEEPERS_PER_TEAM = 6
# v4 Part 3 / Part 17 #2: is 6 a MAXIMUM (teams may keep fewer) or an EXACT
# requirement? Not stated anywhere in the source league-rules data this
# project has -- UNRESOLVED. Defaulting to "maximum" (the safer read: it's
# what v1-v3 always assumed, and forcing exactly 6 could force a team to
# keep a negative-alpha player). Flip to True only once confirmed with the
# league.
KEEPER_COUNT_IS_EXACT = False

# v4 Part 2: franchise-tag carryover rules -- UNRESOLVED, not present
# anywhere in this repo's source data. Left explicit rather than guessed:
#   - Does a tag last exactly one season, or can it roll over?
#   - Does using a tag last year affect tag availability this year?
#   - Is tagging optional every year, or can a team be forced to tag?
# All three are None (unknown) rather than assumed True/False.
TAG_LASTS_ONE_YEAR: bool | None = None
PRIOR_YEAR_TAG_AFFECTS_CURRENT_YEAR: bool | None = None
TAG_IS_OPTIONAL: bool | None = None

# v4 Part 2: Paul Rule eligibility must come from VERIFIED games played,
# not an IR note alone. This repo's historical_salaries_2025_raw.csv has
# no games-played column at all -- only one free-text "on IR" note exists
# for the entire 191-player dataset. Games-played verification is
# therefore UNRESOLVED; the IR-note fallback below is kept but every row
# using it is flagged paul_rule_source="inferred_from_ir_note_unverified"
# rather than presented as confirmed.
PAUL_RULE_GAMES_DATA_AVAILABLE = False

# ASSUMPTION: how the 3 FLEX spots split across RB/WR/TE league-wide.
# No K/DEF and 3 FLEX spots meaningfully flatten scarcity at these positions
# relative to a standard 1-FLEX build; this split drives the replacement-level
# math below. Edit if your league's actual flex usage looks different.
FLEX_SHARE = {"RB": 0.45, "WR": 0.45, "TE": 0.10}

# ASSUMPTION: bench/IR roster-building demand by position, expressed as
# "extra startable-quality bodies per team" beyond the starting lineup.
# QBs are rarely double-rostered in a 1-QB league; RB/WR dominate bench/IR
# stashing; TE bench demand is low outside of the elite-TE handcuff case.
# Lowered from the original {QB:0.3, RB:2.4, WR:2.4, TE:0.4} -- this league
# has a separate mid-season 3-round college/rookie draft that can also fill
# bench/IR bodies without spending live-auction dollars, so not all bench
# demand actually competes for the live budget. Lowering it raises the
# effective replacement-level cutoff (fewer players are "relevant" to the
# live auction), concentrating more of the real live budget on tiered
# difference-makers rather than spreading it thin across bench filler that
# may get covered for free later.
BENCH_DEMAND_PER_TEAM = {"QB": 0.2, "RB": 1.2, "WR": 1.2, "TE": 0.2}


def replacement_rank(position: str) -> int:
    """Return the league-wide rank at which 'replacement level' sits for a
    position, given this league's actual starting lineup + bench demand
    (not a generic redraft assumption)."""
    dedicated = STARTING_LINEUP.get(position, 0) * NUM_TEAMS
    flex = STARTING_LINEUP["FLEX"] * NUM_TEAMS * FLEX_SHARE.get(position, 0)
    bench = BENCH_DEMAND_PER_TEAM.get(position, 0) * NUM_TEAMS
    return round(dedicated + flex + bench)


# ---------------------------------------------------------------------------
# College / rookie draft (separate from veteran auction)
# ---------------------------------------------------------------------------

COLLEGE_DRAFT_ROUNDS = 3
COLLEGE_DRAFT_PICKS_PER_ROUND = NUM_TEAMS
COLLEGE_DRAFT_TOTAL_PICKS = COLLEGE_DRAFT_ROUNDS * COLLEGE_DRAFT_PICKS_PER_ROUND  # 36
# Fixed non-snake order — same team picks same slot every round.
COLLEGE_DRAFT_ORDER = [
    "Sam", "Travis", "Reid", "James", "Shane", "CJ",
    "Brad", "Ryan J", "Evan", "Brandon", "Jason", "Coby",
]
COLLEGE_DEBUT_FEE = 1  # flat auction fee when college rights convert to veteran roster

# OPEN QUESTIONS — verify with commissioner; exposed in outputs, not assumed:
# 1. Does debut require a regular-season snap, or does preseason / roster activation count?
# 2. Does the "no draft list" stash protect rights pre-debut without auto-conversion?
# 3. Must the holding team actively claim a $1 conversion, or is it automatic on debut?
# 4. Do college draft picks trade independently of the prospect board?
COLLEGE_RULE_OPEN_QUESTIONS = [
    "debut_trigger: regular_season_game_vs_preseason_vs_roster_only",
    "no_draft_list_stash_protection_rules",
    "conversion_claim_required_vs_automatic",
    "pick_tradability_confirmation",
]

# ASSUMPTION: dollar-equivalent scale for college pick #1 in trade talks.
# Low confidence — no historical hit-rate data for this league's college draft yet.
COLLEGE_PICK1_DOLLAR_EQUIVALENT = 55.0
COLLEGE_PICK_VALUE_DECAY = 0.92  # per-pick decay from pick 1 through 36

# Prospect valuation: projected NFL draft round → base talent score (0–100)
NFL_DRAFT_ROUND_BASE_SCORE = {
    1: 100, 2: 78, 3: 62, 4: 48, 5: 35, 6: 26, 7: 20,
    "UDFA": 10, "unknown": 15,
}
PROSPECT_POSITION_MULTIPLIER = {"QB": 1.25, "RB": 1.0, "WR": 1.0, "TE": 0.85}
PROSPECT_YEAR_DISCOUNT = 0.88  # multiply per year until projected draft
PROSPECT_BUST_DISCOUNT = 0.85  # applied when projection confidence is low

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

# ---------------------------------------------------------------------------
# VBD-to-dollar convexity
# ---------------------------------------------------------------------------
# ASSUMPTION: real auctions price convexly, not linearly, against points
# above replacement -- a stud's marginal point is worth far more per dollar
# than a replacement-level player's. Distributing the budget in direct
# linear proportion to VBD_score spreads it too evenly across every
# positive-VBD player (in this pool, ~320+), which compresses the ceiling
# and props up the floor relative to what real auctions actually pay (e.g.
# a player with a real 2025 salary of $89 was pricing out near $53 before
# this was added -- the linear split gave him only ~1.3% of the VBD budget
# despite being a clear top-15 scorer). Raising VBD_score to this power
# before the proportional split concentrates dollars toward the top without
# touching the anchor (historical-salary) half of the blend. Retune by
# checking modeled prices for known high-salary historical players.
#
# Raised from 1.6 -- publicly documented auction strategy (FantasyPros,
# Fantasy Footballers, etc.) consistently reports 60-80% of a real budget
# going to just 2-4 elite players, with the bulk of a roster filled near
# the $1 floor once the "endgame" hits and most teams are budget-capped.
# 1.6 wasn't concentrating the top of the board nearly that hard.
#
# v2: chosen by backtest_2025.py (a real same-season test: 2025 preseason
# signals in, predicted price vs. actual 2025 salary out) rather than by
# feel. HONEST FINDING from that backtest, worth reading before retuning
# this by hand again: raising the power from 1.4 all the way to 4.0 barely
# moved the top12/top36 budget-concentration gap vs. real 2025 spending
# (stuck ~0.136-0.146 the whole range) while steadily WORSENING point-level
# MAE. This proportional-with-convexity-exponent mechanism is structurally
# capped in how top-heavy it can get -- real 2025 spending put 25.4% of the
# whole draft's dollars in the top 12 players and 59.4% in the top 36; the
# best this mechanism reached was ~11-14% and ~28-33% respectively. Getting
# genuinely closer to real "stars and scrubs" concentration would need a
# different allocation approach entirely (e.g. an explicit rank-based
# budget-share curve), not a bigger exponent -- flagged as a real v3 idea,
# not solved here. Given that ceiling, 1.4 is chosen because it's simply
# the best-tested point on backtest error; re-run backtest_2025.py if the
# input data changes.
VBD_DOLLAR_POWER = 1.4

# ---------------------------------------------------------------------------
# Tier shrinkage (partial, not full, tier flattening)
# ---------------------------------------------------------------------------
# ASSUMPTION: how much of a player's live-auction VBD_score gets pulled
# toward their FantasyPros (position, tier) group average vs. kept as their
# own individual score. 1.0 = full flattening (everyone in a tier prices
# identically); 0.0 = tiers ignored entirely. Chosen by backtest_2025.py
# (tested 25%-75%) -- see that script for the selection metric. NOTE: the
# backtest's tiers are a SYNTHETIC proxy (preseason ECR rank bucketed by
# 10), not real archived FantasyPros tier labels -- no 2025 preseason tier
# file exists in this repo. Treat this value as directionally tested, not
# precisely tested.
TIER_SHRINKAGE_PCT = 0.5

# ---------------------------------------------------------------------------
# v4: salary-origin reliability (Part 1 / Part 8)
# ---------------------------------------------------------------------------
# How much a player's reported salary_2025 should count as real MARKET
# EVIDENCE when blended into pricing, separate from whether it's used for
# keeper-cost math (which always uses the real reported number regardless
# of origin -- the escalating-cost rule doesn't care how a salary was set).
# 1.0 = fully trusted; 0.0 = ignored entirely as a valuation signal.
#
# This repo's historical_salaries_2025_raw.csv has NO recorded origin field
# -- origin is inferred heuristically (data_pipeline.classify_salary_origin):
# a flat $1 with no notes is presumed ROOKIE_ASSIGNMENT/administrative
# (confirmed pattern: 59 of 183 salaries, 3-8 per team, no notes -- a
# structural fill mechanic, not 59 independent minimum bids -- see v3
# changelog). This is inference, not verified origin data, and is flagged
# as such everywhere it's used. A genuine $1 AUCTION win (real tail-market
# evidence) is indistinguishable from an administrative $1 in this data --
# unresolved without a real origin field from the league's actual records.
SALARY_ORIGIN_RELIABILITY = {
    "AUCTION_CONFIRMED": 1.00,
    "KEEPER_ESCALATION_CONFIRMED": 0.35,
    "WAIVER_CONFIRMED": 0.25,
    "PAUL_RULE_CONFIRMED": 0.25,
    "ROOKIE_ASSIGNMENT_CONFIRMED": 0.00,
    "MANUAL_ASSIGNMENT_CONFIRMED": 0.00,
    "UNKNOWN_DOLLAR_ONE": 0.00,
    "UNKNOWN_NON_DOLLAR_ONE": 0.10,
    "UNKNOWN": 0.00,
    # Legacy aliases (tests / older CSVs)
    "AUCTION": 1.00,
    "KEEPER_ESCALATION": 0.35,
    "WAIVER": 0.25,
    "PAUL_RULE": 0.25,
    "ROOKIE_ASSIGNMENT": 0.00,
    "MANUAL_ASSIGNMENT": 0.00,
}
# Reliability for the tier-median-imputed fallback anchor (Priority 5, v2) --
# separate from the table above since it's not an observed salary at all.
IMPUTED_ANCHOR_RELIABILITY = 0.05

# ---------------------------------------------------------------------------
# v4: keeper selection + counterfactual settings (Part 3 / Part 10)
# ---------------------------------------------------------------------------
KEEPER_ALPHA_SELECTION_THRESHOLD = 0.0
MAX_KEEPER_MARKET_ITERATIONS = 20
KEEPER_MARKET_UPDATE_METHOD = "DAMPED_CONFIRMATION"  # SIMULTANEOUS, SEQUENTIAL_FIXED_ORDER, DAMPED_CONFIRMATION
KEEPER_CONSERVATIVE_MARGINS = [0, 3, 5, 10]
KEEPER_DECISION_MARGIN = 5  # default conservative $ margin for STRONG_KEEP
AUTHORITATIVE_KEEPERS_PATH = "outputs/keepers_2026.csv"
DEPLETED_ALPHA_COUNTERFACTUAL_MODE = "player_counterfactual"  # or "position_ratio_fallback"
FLEX_ALLOCATION_MODE = "marginal"  # or "fixed_share_fallback"
SAM_TEAM_NAME = "Sam"

# Scenario labels (active scenario printed on every run)
SCENARIO_KEEPER_COUNT = "A"  # A=max six, B=exact six
SCENARIO_TAG = "C"  # C=one optional tag, D=no tag

# Trade rule scenarios — UNRESOLVED until league confirms
TRADE_CONTRACT_TRANSFERS_WITH_PLAYER = True
TRADE_SALARY_TRANSFERS_WITH_PLAYER = True
TRADE_TAG_TRANSFERS_WITH_PLAYER = False
TRADE_DRAFT_ASSETS_TRADABLE = None  # UNRESOLVED
TRADE_ROSTER_SIZE_LIMIT_PRE_LOCK = None  # UNRESOLVED

# Decision confidence thresholds (Phase 6)
CONFIDENCE_LOCK_SELECTION_RATE = 0.90
CONFIDENCE_STRONG_KEEP_RATE = 0.70
CONFIDENCE_BORDERLINE_LOW = 0.30
CONFIDENCE_DATA_BLOCKED_ORIGINS = {
    "UNKNOWN", "UNKNOWN_DOLLAR_ONE", "UNKNOWN_NON_DOLLAR_ONE",
}

# Counterfactual: exact evaluation window ($ above standard keeper cost)
COUNTERFACTUAL_EXACT_ALPHA_WINDOW = 20.0

# ---------------------------------------------------------------------------
# v4: rounding / reconciliation (Part 9)
# ---------------------------------------------------------------------------
ROUNDING_METHOD = "largest_remainder"  # deterministic exact-reconciliation method
