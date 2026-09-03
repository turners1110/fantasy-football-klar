# Mock Auction Draft Simulator

Simulates the actual live auction — 12 teams, real budgets and keepers,
nominate-for-$1 ascending bidding — with archetype-driven AI owners, so you
can run it hundreds of times and compare where simulated market-clearing
prices land versus `suggested_auction_price` from the main valuation model.
That comparison is the point: gaps are a concrete signal for where
`auction_model/config.py`'s assumptions (replacement level, VBD convexity,
tier shrinkage, etc.) don't match how a real auction room would actually
behave.

## Quick start

```bash
python run_valuation.py --keeper-mode fallback_neutral --output-dir output_mock_draft_snapshot
python run_mock_draft.py --iterations 50
python run_mock_draft.py --iterations 1 --verbose   # watch one draft pick-by-pick
```

Outputs land in `output_mock_draft/`:
- `all_picks.csv` — every pick from every simulated draft
- `calibration_report.csv` — per player: real `base_value` vs. simulated
  mean/median/std clearing price, and the gap
- `leftover_budgets.csv` — per team per draft (should always be exactly
  $0.00 — see below)

## Why a separate snapshot dir, not `output/`

`output_mock_draft_snapshot/` is a supplementary regeneration of the real
model's pricing (`run_valuation.py --keeper-mode fallback_neutral`), kept
separate from `output/` (which the main pipeline's `run_keeper_decisions.py`
/ authoritative-keeper workflow owns) so this simulator never clobbers that
canonical output. Regenerate it any time the real model or its inputs
change; the simulator always reads from it fresh.

## Two hard rules, and how they're actually enforced

1. **Every team's roster reaches exactly 15 players** (starters + bench;
   IR is optional per league rules, not required at auction) — keepers
   count toward this, so a team's live-auction slots = 15 − their keeper
   count.
2. **Every team spends its entire $400 budget — no leftover.**

(1) falls out naturally from the auction loop (it keeps going until every
team is full). (2) does **not** fall out naturally from realistic
English-auction bidding: if nobody contests a nomination, it's won for $1
no matter how much cash the winner has sitting unused. Two attempts at
making this emerge organically (see git history / code comments in
`valuation.py`) either produced a $286 bid on a ~$21 real-value player or
had teams bidding $30+ on scrub backup QBs just because budget was
available. What actually guarantees rule (2): **the player that completes
a team's 15th and final roster slot costs that team's entire remaining
budget, period** — a deterministic rule, not a competitive outcome. These
transactions are flagged `forced_final_slot=True` in `all_picks.csv` and
excluded from `calibration_report.csv`'s aggregation, since they're not
organic market-clearing prices and would badly distort the comparison.

## The archetypes

Condensed from a longer description of real auction-drafter psychology
into one shared engine driven by a parameter table
(`archetypes.py`) rather than 8 bespoke behaviors: stars-and-scrubs,
balanced, value purist, anchor, two flavors of positional extremist
(RB-heavy / WR-heavy), tier controller, price enforcer, and emotional
drafter. Archetypes are reassigned **randomly to all 12 real teams on every
simulated draft** (not fixed per team) — the goal is a distribution of
plausible market outcomes for calibration, not a prediction of which real
owner behaves which way.

Key mechanics:
- **Stars**: a small number of players get "pay more than fair value"
  treatment, but only if they're in the **global top 30 by real dollar
  value** (not merely a position's local #1 — a shallow 1-QB league can
  make a $18 QB "tier 1 at QB," which is not the same thing as a star, and
  earlier testing caught exactly that misclassification). The premium is
  capped at 2.5x the player's real value (`STAR_MAX_VALUE_MULTIPLE`) even
  after position-fit and tier-cliff multipliers stack on top.
- **Tier cliffs**: players are bucketed into fixed-size tiers per position
  by real value (a lightweight stand-in for scouted tiers); the last or
  second-to-last player in a tier gets a bid-aggression bump.
- **Nomination strategy** (`nomination.py`): scores candidates by whether a
  cash-flush rival privately wants them (nominate to drain), whether
  they're at a tier cliff (nominate to trigger panic), and whether
  multiple rivals still need that position (nominate to start a run) —
  while generally avoiding nominating your own top targets.

## Known limitations / where to calibrate next

- Tiers are a synthetic value-based proxy, not real scouted tiers.
- Archetype parameters (`archetypes.py`) are a first-pass translation of a
  qualitative description into numbers — expect to retune `price_ceiling_pct`,
  `star_ceiling_pct`, and `STAR_MAX_VALUE_MULTIPLE` against real results the
  same way `auction_model/config.py`'s `VBD_DOLLAR_POWER` was backtested.
- Turn order is fixed round-robin regardless of who wins each pick. If this
  league actually uses "winner nominates next," that's a one-line change
  in `auction.py`.
- `calibration_report.csv`'s biggest gaps right now cluster among
  global-top-30 "star" players simulating 1.5-2.5x their real value when
  multiple star-hunting archetypes collide on the same player — a real,
  intentional design bound (not a bug), but worth checking against actual
  auction history if this league has ever seen a true bidding war blow
  past 2x consensus value for one player.
