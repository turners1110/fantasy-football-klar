## ⚠️ RETRACTED PENDING REVALIDATION (auction rebuild, phase 1+2)

**Do not use this strategy or its numbers to draft.** The entire
recommendation below was optimized against a fitness function phase 1's
audit (`outputs/auction_rebuild/audit/current_architecture.md`,
`audit_qb_arbitrage.py`) proved invalid:

1. The evolutionary search (`run_evolution.py`) and the best-response test
   (`run_best_response.py`) both scored a roster by **summing every one of
   its 15 rostered players' projected points equally** (`Team.total_points`)
   -- not by what that roster could actually start in this league's real
   1QB/2RB/2WR/1TE/3FLEX lineup.
2. Because "prioritize QB" was the winning genome's single biggest lever,
   this rewarded rostering as many quarterbacks as possible: the winning
   genome (`gen15_elite0`) averaged **4.5 QBs per roster** across the 40
   audited matches -- far beyond the 1 a roster can ever start.
3. That naive metric credited those rosters with **1,302.7 points from the
   QB position alone** (summing QB1 through QB4/5 as if all could start).
4. Re-scored under a legal-1QB-starting-lineup-aware metric
   (`mock_draft/legal_lineup.py`), the SAME rosters' real, startable QB
   credit drops to **228.7 points** -- an ~82% reduction, because only one
   QB per roster can ever actually start.
5. **15 of the 40 audited rosters were illegal** under this league's real
   lineup rules (missing a legal starter at some position) once actually
   checked -- rosters the old metric happily scored anyway.
6. **No strategic recommendation from the prior evolutionary run remains
   approved.** The "+813 points above baseline" best-response result cited
   below is retracted -- it was measured on the same invalid metric. The
   specific numeric parameters (`position_weight: {QB: 1.56}`,
   `max_stars: 0`, `price_ceiling_pct: ~10.5%`, etc.) are NOT validated
   findings and must not be used to draft.

Phase 2 (`outputs/auction_rebuild/`) fixed the fitness function
(`legal_lineup.build_production_lineup(...).total_roster_utility` now
drives `evolution.py` and `best_response.py` in place of
`Team.total_points`), removed forced-final-slot spending from the auction
engine, and established one authoritative confirmed-keeper/budget
pipeline -- but explicitly **did not re-run evolution or re-validate a
strategy** under the corrected metric (out of phase-2 scope by design).
A new strategic recommendation requires a fresh evolutionary run under the
corrected fitness function, followed by a fresh best-response test, before
anything below this notice can be trusted again.

The old findings are preserved verbatim below as an audit trail, NOT as
current guidance.

---

# Recommended Auction Strategy [RETRACTED -- see notice above]

**Source**: genome `gen15_elite0` from the co-evolutionary optimizer
(`run_evolution.py`), validated via a best-response test — see
`README.md`'s "Co-evolutionary bidding optimizer" section for the full
methodology and the honest history of how this result was reached (two
earlier self-play tournament runs showed no detectable improvement over
hand-designed archetypes; the improvement only became visible once tested
as "this strategy vs. a realistic field," not "this strategy vs. a
co-evolving population").

## Confidence level — read this before trusting the numbers below

This is based on **40 simulated drafts**, each with this strategy in one
random real-team slot against 11 opponents drawn from the hand-designed
archetype mix. The effect size is large and clears a real statistical bar
(+813 points above that team's typical baseline, ±46 standard error — over
17 standard errors from zero), which is a meaningfully strong signal, not
noise. But it is still one validated genome from one 15-generation
evolutionary run, tested against a *simulated* field, not this league's
actual 11 other owners. Before trusting this with real auction dollars:
- Re-run `run_best_response.py` with a larger `--matches-per-candidate`
  (100+) to tighten the confidence interval further.
- Treat the specific numeric parameters below as directional, not exact —
  "prioritize QB, don't chase RB/WR premiums" is the real finding; whether
  the ideal price ceiling is 8% or 13% of budget is less certain.

## The strategy, in plain English

1. **Never chase "stars."** `max_stars: 0` — this strategy refuses the
   whatever-it-takes treatment entirely, even for the global top-30
   players by value. It never pays a scarcity premium for anyone.
2. **Actively de-prioritize RB, WR, and TE premiums.**
   `position_weight: {RB: 0.5, WR: 0.5, TE: 0.5}` — the floor of the
   allowed range for all three. This doesn't mean "don't roster RB/WR" —
   the roster still needs to fill 2RB/2WR/3FLEX — it means **don't get
   drawn into bidding wars for them.** Let other owners overpay for the
   name-brand players; take the RB/WR value that's left at a discount.
3. **Actively prioritize QB.** `position_weight: {QB: 1.56}` — the
   highest weight of any position. This is a real, explainable market
   inefficiency this league's structure creates: it's a 1-QB league with
   no flex-eligibility for QB, so the real valuation model already prices
   QBs cheaply (≈7% of total draft dollars leaguewide, the smallest share
   of any position) — cheap because *demand* is low, not because *points*
   are low. A strategy willing to actually pay up for a good QB when
   almost nobody else is competing for one gets real points at a price
   nobody else is fighting over.
4. **Stay disciplined on price.** `price_ceiling_pct: ~10.5%` of remaining
   budget per non-priority player, `jump_bid_prob: 0.0` (always raise by
   the minimum $1 increment, never jump-bid), `tier_aggression: ~1.0`
   (no extra urgency at tier cliffs). This is close to the hand-designed
   **Price Enforcer** archetype's discipline — which also finished 2nd in
   the best-response test (+396 pts) — reinforcing that the core insight
   is "don't overpay," not some exotic timing trick.
5. **Some tolerance for a comeback after losing bids** (`tilt_after_losses:
   5, tilt_boost: ~1.26`) — a mild willingness bump after losing several
   nominations in a row, so discipline doesn't tip into never winning
   anything.

## What this looks like at the actual auction

- **Don't get pulled into a bidding war on the marquee RB/WR names.** If
  two other owners are fighting over a player, let them — that money
  isn't coming back, and this strategy's edge is specifically *not*
  spending it there.
- **Be willing to be the only bidder pushing a QB price up.** If a
  competent-but-not-superstar QB is sitting at $8-15 with no other bids,
  this is exactly the spot to be more aggressive than the room, not less.
- **Fill RB/WR/TE with volume, not premiums.** Plan to end up with more
  "pretty good for the price" RB/WR than "the one big name," consistent
  with this league's real flex-heavy structure (3 FLEX spots reward
  roster depth at RB/WR/TE, not necessarily one or two elite anchors).
- **Check `output_mock_draft/player_price_distributions.csv`** (built by
  `run_player_price_distributions.py`, simulating this exact strategy
  against a realistic field) for a specific player's likely price range —
  median, and how wide the P10-P90 band is. A wide band means that
  player's actual price will depend heavily on who else wants him; a
  narrow band means the market is more predictable for him.

## Regenerating this

```bash
python run_evolution.py --generations 15 --population 20 --matches-per-generation 25
python run_best_response.py --matches-per-candidate 100   # tighten confidence
python run_player_price_distributions.py --iterations 100 --genome-name <whichever genome wins>
```

If a future run finds a different genome winning the best-response test,
update `--genome-name` in `run_player_price_distributions.py`'s default
and re-check whether the plain-English summary above still holds.
