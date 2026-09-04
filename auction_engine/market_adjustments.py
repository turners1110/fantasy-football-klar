"""Live MVP Part 3: simple, transparent, shrinkage-based market
adjustment model. Explicitly NOT Bayesian owner agents -- four flat
signals (league-wide, position, tier, demand), each shrunk toward its
parent signal with a documented prior weight, combined into a single
capped multiplier applied to each player's frozen pre-draft price.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Prior weight = "how many sales' worth of evidence" the prior (1.00, i.e.
# no adjustment) is worth before real sales start moving the signal.
# Documented per spec Part 3's explicit instruction. Chosen so a single
# early sale barely moves anything (1/(8+1) ~ 11% of the observed
# deviation leaks through) while 5+ sales move it meaningfully.
LEAGUE_PRIOR_WEIGHT = 8.0
POSITION_PRIOR_WEIGHT = 5.0
TIER_PRIOR_WEIGHT = 3.0

MIN_MULTIPLIER = 0.70
MAX_MULTIPLIER = 1.40


@dataclass
class MarketAdjustmentState:
    """All sold-player (actual_price, expected_price, position, tier)
    observations, rebuilt fully from the event log on every update (per
    spec: "Correcting a sale price should rebuild all adjustments from
    the event log.")."""
    observations: list[dict] = field(default_factory=list)  # {position, tier, actual, expected}

    def add_observation(self, position: str, tier: str, actual_price: float, expected_price: float):
        self.observations.append({"position": position, "tier": tier, "actual": actual_price, "expected": expected_price})

    @staticmethod
    def rebuild_from_sales(sales: list[dict]) -> "MarketAdjustmentState":
        """sales: [{"position", "tier", "actual_price", "expected_price"}]"""
        state = MarketAdjustmentState()
        for s in sales:
            state.add_observation(s["position"], s.get("tier", "unknown"), s["actual_price"], s["expected_price"])
        return state

    def league_ratio(self) -> tuple[float, int]:
        if not self.observations:
            return 1.0, 0
        total_actual = sum(o["actual"] for o in self.observations)
        total_expected = sum(o["expected"] for o in self.observations)
        raw_ratio = total_actual / total_expected if total_expected else 1.0
        n = len(self.observations)
        shrunk = (LEAGUE_PRIOR_WEIGHT * 1.00 + n * raw_ratio) / (LEAGUE_PRIOR_WEIGHT + n)
        return shrunk, n

    def position_ratio(self, position: str) -> tuple[float, int]:
        league_shrunk, _ = self.league_ratio()
        pos_obs = [o for o in self.observations if o["position"] == position]
        if not pos_obs:
            return league_shrunk, 0
        total_actual = sum(o["actual"] for o in pos_obs)
        total_expected = sum(o["expected"] for o in pos_obs)
        raw_ratio = total_actual / total_expected if total_expected else 1.0
        n = len(pos_obs)
        shrunk = (POSITION_PRIOR_WEIGHT * league_shrunk + n * raw_ratio) / (POSITION_PRIOR_WEIGHT + n)
        return shrunk, n

    def tier_ratio(self, position: str, tier: str) -> tuple[float, int]:
        pos_shrunk, _ = self.position_ratio(position)
        tier_obs = [o for o in self.observations if o["position"] == position and o["tier"] == tier]
        if not tier_obs:
            return pos_shrunk, 0
        total_actual = sum(o["actual"] for o in tier_obs)
        total_expected = sum(o["expected"] for o in tier_obs)
        raw_ratio = total_actual / total_expected if total_expected else 1.0
        n = len(tier_obs)
        shrunk = (TIER_PRIOR_WEIGHT * pos_shrunk + n * raw_ratio) / (TIER_PRIOR_WEIGHT + n)
        return shrunk, n


def demand_signal(position: str, teams_open_starter: int, teams_open_flex: int,
                   teams_with_cash: int, remaining_supply: int) -> float:
    """Simple, evidence-only demand multiplier: more open needs and cash
    relative to remaining supply raises expected price; more supply
    relative to demand lowers it. Bounded to +/-15% on its own before
    the overall cap is applied."""
    demand = teams_open_starter + 0.5 * teams_open_flex
    if remaining_supply <= 0:
        return 1.15
    pressure = min(demand, teams_with_cash) / max(1, remaining_supply)
    # pressure of 1.0 (one credible buyer per remaining player) -> neutral 1.0
    # pressure > 1 -> scarcity, price up; pressure < 1 -> oversupply, price down
    adj = 1.0 + 0.15 * (pressure - 1.0)
    return max(0.85, min(1.15, adj))


def live_expected_price(pre_draft_price: float, position: str, tier: str,
                         market_state: MarketAdjustmentState,
                         teams_open_starter: int, teams_open_flex: int,
                         teams_with_cash: int, remaining_supply: int) -> dict:
    league_ratio, league_n = market_state.league_ratio()
    position_ratio, position_n = market_state.position_ratio(position)
    tier_ratio, tier_n = market_state.tier_ratio(position, tier)
    demand_mult = demand_signal(position, teams_open_starter, teams_open_flex, teams_with_cash, remaining_supply)

    # Combine: league and position/tier already represent cumulative multiplicative
    # drift, so use the most specific (tier) ratio as the primary spending signal,
    # then apply the independent demand signal on top.
    combined = tier_ratio * demand_mult
    capped = max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, combined))
    final_price = round(pre_draft_price * capped)

    return {
        "pre_draft_price": pre_draft_price,
        "league_spending_ratio": round(league_ratio, 4), "league_sales_n": league_n,
        "position_spending_ratio": round(position_ratio, 4), "position_sales_n": position_n,
        "tier_spending_ratio": round(tier_ratio, 4), "tier_sales_n": tier_n,
        "demand_multiplier": round(demand_mult, 4),
        "combined_multiplier_uncapped": round(combined, 4),
        "combined_multiplier_capped": round(capped, 4),
        "live_expected_price": max(1, final_price),
        "calculation_label": "LIVE_MARKET_ADJUSTED",
    }
