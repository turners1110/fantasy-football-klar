"""Phase 3D item 1: the label taxonomy every Phase 3D script/report must use
when describing a dollar figure, so a reader can never mistake a simulated
number for an observed one.

Prior phases (3A/3B/3C) repeatedly called simulated auction outputs a
"real simulated market price," "true market price," or "real market
price" -- self-contradictory phrasing (a simulated number cannot also be
"real") that materially overstated confidence in numbers like Josh
Jacobs's $201.50 phase-3B `market_price_p50`, which came from a known,
badly over-concentrated pre-3D model. That $201.50 figure is an
UNCALIBRATED_SIMULATED_PRICE, not a real market price, full stop.

These constants are the only vocabulary Phase 3D code should use for
dollar figures with a source-and-confidence claim attached. Historical
phase 3A/3B/3C reports and CSVs are NOT rewritten (preserved as-is per
this project's audit-trail policy) -- each one that used the retired
phrasing carries a prepended correction notice pointing back here instead.
"""

from __future__ import annotations

# A price produced by running the auction simulator BEFORE calibration
# against any real-world target. Carries no claim of matching an actual
# market -- it is a model output, nothing more, however plausible it looks.
UNCALIBRATED_SIMULATED_PRICE = "UNCALIBRATED_SIMULATED_PRICE"

# A price produced by the simulator AFTER its parameters have been fit to
# calibration targets (item 9-11) and validated on held-out seeds. Still a
# model output, not an observation -- but one with a measured, disclosed
# accuracy against real targets, unlike UNCALIBRATED_SIMULATED_PRICE.
CALIBRATED_EXPECTED_MARKET_PRICE = "CALIBRATED_EXPECTED_MARKET_PRICE"

# A value sourced from a public auction-value tool/site/consensus (item 7's
# anchor hierarchy) -- an external estimate of market price, not this
# league's own history, and not this simulator's output.
PUBLIC_AUCTION_ANCHOR = "PUBLIC_AUCTION_ANCHOR"

# An actual, observed price this specific league paid in a prior season.
# The only category in this list that is a real transaction record.
HISTORICAL_LEAGUE_PRICE = "HISTORICAL_LEAGUE_PRICE"

# A dollar value representing what a player is worth to ONE specific team
# given that team's roster, needs, and budget -- not a market-clearing
# price, and not comparable across teams without that context.
TEAM_SPECIFIC_VALUE = "TEAM_SPECIFIC_VALUE"

# The maximum price one specific team would pay for one specific player,
# computed by solving that team's roster-construction problem exactly
# (e.g. via exact_roster_solver.py). Exact for the given inputs; still a
# model output conditioned on this simulator's projections/rules.
EXACT_TEAM_BID_CEILING = "EXACT_TEAM_BID_CEILING"

# The same quantity as EXACT_TEAM_BID_CEILING but produced by a heuristic
# or bounded approximation rather than an exact solve -- must never be
# reported without this qualifier once an exact figure is available for
# the same player/team.
APPROXIMATE_TEAM_BID_CEILING = "APPROXIMATE_TEAM_BID_CEILING"

ALL_LABELS = (
    UNCALIBRATED_SIMULATED_PRICE,
    CALIBRATED_EXPECTED_MARKET_PRICE,
    PUBLIC_AUCTION_ANCHOR,
    HISTORICAL_LEAGUE_PRICE,
    TEAM_SPECIFIC_VALUE,
    EXACT_TEAM_BID_CEILING,
    APPROXIMATE_TEAM_BID_CEILING,
)
