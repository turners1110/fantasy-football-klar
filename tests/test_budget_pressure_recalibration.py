"""Draft-night budget-pressure repair (Sam's requests: "as other teams
spend money they should proportionally spend less"; "the team with the
most cash and most open slots should be outbidding and winning players").

Covers the three pieces added together:
  1. budget-state signal measured against the LIVE league cash-per-open-
     slot (Team.league_cash_per_open_slot) instead of a static $400/16;
  2. positional-saturation discount so the stronger budget signal cannot
     push a cash-rich team into stacking a position it can't start;
  3. a positive budget signal may lift a strict-ceiling (Value Purist)
     team above its anchor by exactly that amount, never more.
"""
from __future__ import annotations

import numpy as np

from auction_model import config as cfg
from mock_draft.models import Player, Team
from mock_draft.valuation import (
    _budget_state_adjustment, _position_saturation_adjustment, compute_willingness,
)


def _player(pos="RB", value=40.0):
    return Player(name=f"P-{pos}", position=pos, base_value=value, tier=1, tier_size=4,
                  tier_rank=1, is_star_eligible=True, projected_points=200.0)


def _roster(n_rb=0, n_wr=0):
    return ([("rb%d" % i, "RB", 5.0, 100.0) for i in range(n_rb)]
            + [("wr%d" % i, "WR", 5.0, 100.0) for i in range(n_wr)])


def test_budget_state_uses_live_league_reference_when_supplied():
    # Same team, same cash: rich relative to a $12/slot market, poor
    # relative to a $30/slot market.
    rich = Team(name="T", budget_remaining=200.0, roster=_roster(6), archetype="balanced",
                league_cash_per_open_slot=12.0)
    poor = Team(name="T", budget_remaining=200.0, roster=_roster(6), archetype="balanced",
                league_cash_per_open_slot=30.0)
    assert _budget_state_adjustment(rich) > 0 > _budget_state_adjustment(poor)
    assert _budget_state_adjustment(rich) <= cfg.MAX_BUDGET_STATE_ADJUSTMENT


def test_budget_state_grows_as_rest_of_league_spends_down():
    # A team holding cash while the league reference falls should read as
    # progressively more able to pay -- this is the "eventually outbids"
    # mechanism.
    team = lambda ref: Team(name="T", budget_remaining=150.0, roster=_roster(8),
                            archetype="balanced", league_cash_per_open_slot=ref)
    adjustments = [_budget_state_adjustment(team(ref)) for ref in (25.0, 18.0, 12.0, 8.0)]
    assert adjustments == sorted(adjustments)
    assert adjustments[-1] > adjustments[0]


def test_position_saturation_discount_is_zero_while_position_open_and_negative_when_full():
    open_team = Team(name="T", budget_remaining=150.0, roster=_roster(n_rb=1), archetype="balanced")
    full_team = Team(name="T", budget_remaining=150.0, roster=_roster(n_rb=6), archetype="balanced")
    assert _position_saturation_adjustment(open_team, _player("RB")) == 0.0
    assert _position_saturation_adjustment(full_team, _player("RB")) < 0.0
    assert _position_saturation_adjustment(full_team, _player("RB")) >= -cfg.MAX_POSITION_SATURATION_ADJUSTMENT
    # Saturation at RB says nothing about WR.
    assert _position_saturation_adjustment(full_team, _player("WR")) == 0.0


def test_cash_rich_but_rb_saturated_team_does_not_pay_up_for_another_rb():
    """The regression the gate-f suite caught when the budget signal was
    first strengthened: buying four cheap RBs raised cash-per-slot and
    therefore willingness for a FIFTH RB. Saturation must win."""
    before = Team(name="T", budget_remaining=200.0, roster=_roster(n_rb=1, n_wr=1),
                  archetype="balanced", league_cash_per_open_slot=14.0)
    after = Team(name="T", budget_remaining=180.0, roster=_roster(n_rb=5, n_wr=1),
                 archetype="balanced", league_cash_per_open_slot=14.0)
    rng_a, rng_b = np.random.default_rng(3), np.random.default_rng(3)
    w_before = compute_willingness(before, _player("RB"), rng_a, draft_progress=0.3)
    w_after = compute_willingness(after, _player("RB"), rng_b, draft_progress=0.3)
    assert w_after <= w_before


def test_value_purist_lifted_above_anchor_only_by_positive_budget_state():
    player = _player("WR", value=30.0)
    rich = Team(name="CJ", budget_remaining=200.0, roster=_roster(n_rb=4, n_wr=2),
                archetype="value_purist", league_cash_per_open_slot=8.0)
    poor = Team(name="CJ", budget_remaining=40.0, roster=_roster(n_rb=4, n_wr=2),
                archetype="value_purist", league_cash_per_open_slot=25.0)
    diag_rich, diag_poor = {}, {}
    w_rich = compute_willingness(rich, player, np.random.default_rng(1), diagnostics=diag_rich)
    w_poor = compute_willingness(poor, player, np.random.default_rng(1), diagnostics=diag_poor)
    anchor = diag_rich["base_market_anchor"]
    assert diag_rich["budget_state_adjustment"] > 0
    assert w_rich <= anchor + diag_rich["budget_state_adjustment"] + 0.01
    assert w_rich > anchor  # can now actually win a contested player
    assert w_poor <= anchor + 0.01  # discipline unchanged when not cash-rich


def test_practice_session_supplies_live_league_reference():
    from auction_engine.practice_draft_session import PracticeDraftSession
    sess = PracticeDraftSession(session_id="unit-budget-ref", seed=5)
    ref = sess._league_cash_per_open_slot()
    assert ref is not None and 15.0 < ref < 40.0  # ~$3,066 / 113 openings at the start
    team = sess._build_md_team(sess.ai_team_ids[0])
    assert team.league_cash_per_open_slot == ref
