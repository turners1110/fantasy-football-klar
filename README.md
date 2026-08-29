# Fantasy Football Klar — Auction Valuation Model

A from-scratch auction price sheet built for this league's actual rules
(12 teams, $400 budget, 2RB/2WR/TE/3FLEX, no K/DEF, 6-player keepers with
the $10/$5-tag bump, the Paul Rule, a separate mid-season college draft) —
not a generic redraft calculator with the numbers filed off.

## Quick start

```bash
pip install -r requirements.txt
python run_valuation.py
```

Outputs land in `output/`:

- `veteran_auction_price_sheet.csv` — dollar value for every non-keeper
  player: `player, position, nfl_team, projected_points, VBD_score,
  suggested_auction_price, historical_salary_if_known, notes`
- `keepers_2026.csv` — who's being kept and at what price, per team
- `college_rookie_draft_board.csv` — draft-slot ranking (not dollars) for
  the 3-round, 12-team mid-season college draft

Re-run any time news changes — new keeper decisions, updated projections,
injuries. Nothing here is a one-off script.

## How it actually works

### 1. Historical anchor (`data/historical_salaries_2025_raw.csv`)

The league's 2025 roster/salary data, cleaned by `auction_model/data_pipeline.py`:
blank salaries become real nulls (never guessed), and the one duplicate row
(Kyler Murray, Coby's team) is collapsed with a logged note. This dataset is
this league's actual market — it's the primary pricing signal, not a
footnote.

### 2. Keepers (`auction_model/keepers.py`)

- **Pricing**: prior salary + $10, or + $5 if the team's franchise tag is
  used, or unchanged if the Paul Rule applies (played < 4 games last season
  — flagged here as anyone noted "on IR").
- **Who's kept**: nobody has told the model real 2026 keeper decisions yet,
  so it ships a conservative, fully transparent default — a player
  qualifies if their 2025 salary is $15–$45 (a real starter, still cheap
  enough to be surplus at +$10), or they were tagged in 2025, or they're a
  Paul Rule case. Capped at 6/team, cheapest-first.
- **Override it**: copy `data/keeper_overrides.template.csv` to
  `data/keeper_overrides.csv` and fill in `team,player,will_keep,tag_used`
  for confirmed decisions as they're announced. Rows you specify win over
  the heuristic; anything you don't specify still uses it. The script
  enforces the 6-keeper and 1-tag-per-team caps and will error if an
  override sheet violates them.

### 3. Inflation

The league spends the same $4,800 total every year. Keeper spend
(prior salary + bump) comes off the top; what's left is the real live-auction
budget:

```
remaining_budget = $4,800 − total keeper cost
inflation_multiplier = remaining_budget / ($4,800 − historical salary of kept players)
```

Note this can come out **below** 1.0 (deflation), not just above it — every
dollar of keeper bump is a dollar that leaves the live-auction pool without
removing an equivalent amount of "value" from it. That's a real effect of
this league's specific $10/$5 bump rule, not a bug. Every remaining
player's historical-salary anchor is scaled by this multiplier before
pricing.

### 4. Valuation (`auction_model/valuation.py`)

Two signals, blended:

- **VBD dollars** — projected points above this league's real replacement
  level. Replacement rank per position is computed from the actual roster
  math (`auction_model/config.py`: 2RB/2WR/TE/3FLEX × 12 teams, no K/DEF,
  plus a bench/IR demand estimate), not a generic redraft baseline. The
  3-FLEX rule is exactly why remaining RB/WR/TE should price higher than a
  stock calculator — this is where that gets encoded.
- **Anchor dollars** — last year's real league salary × the inflation
  multiplier.

`--blend-weight` (default 0.6) sets how much a projected player's price
trusts VBD vs. the anchor. **Without a projections file, blend weight is
forced to 0 for everyone** — the model will not fabricate point projections
for real players. Supply real ones (see below) to turn VBD on.

Final prices are rescaled so the priced pool sums to `remaining_budget`,
then clipped to `[$1, $100]`.

### 5. Sanity checks

Printed on every run, matching the original spec:

- Total suggested prices vs. remaining budget (flagged if off by >15%)
- Any price outside `[$1, $100]`
- Any player's suggested price vs. their 2025 salary differing by >2x
- Any player with **no** historical salary and **no** projection — left
  `null`, listed by name, never guessed

### 6. College / rookie draft board (`auction_model/rookie_board.py`)

Kept as a genuinely separate track, ranked by draft slot (round + pick
range in the 3-round/12-team draft), not dollars — players who've already
debuted stay in the veteran auction; those who haven't go here. The
historical salary dataset only contains players who've already debuted (by
definition — they had an auction salary), so **this board is empty until
you supply prospect data**. Copy `data/rookie_pool.template.csv` to
`data/rookie_pool.csv`: `player, position, college_team, has_debuted,
external_rank, draft_projection_notes`. `external_rank` can be pasted
straight from any dynasty rookie big board.

## Supplying real 2026 projections

Copy `data/projections_2026.template.csv` to `data/projections_2026.csv`
and either:

- fill in `projected_points` directly (e.g. exported from FantasyPros/
  Sharp/ESPN/whatever source you like), or
- fill in the raw per-stat columns (`pass_yd, pass_td, interception,
  rush_yd, rush_td, reception, rec_yd, rec_td, fumble_lost, two_pt`) and
  let the model score them itself under this league's exact rules, instead
  of trusting whatever scoring format the source used.

Then re-run with `python run_valuation.py --projections data/projections_2026.csv`.

## Open assumptions worth confirming

These are called out in `auction_model/config.py` and change the output —
edit the constants and re-run rather than treating them as fixed:

1. **Scoring "standard yardage bonuses"** — currently OFF (pure
   0.5-per-reception / 0.1-per-yard / 6-pt rush+rec TD / 4-pt pass TD, no
   milestone bonuses). Set `bonus_thresholds` in `ScoringConfig` if this
   league actually uses them.
2. **Flex/bench positional split** (`FLEX_SHARE`, `BENCH_DEMAND_PER_TEAM`)
   — estimated RB 45% / WR 45% / TE 10% of flex usage and per-team bench
   depth. Drives replacement level directly.
3. **Keeper heuristic band** ($15–$45) — a placeholder until real 2026
   keeper decisions are known; override via `keeper_overrides.csv` as soon
   as they're announced (well before the "locks one week before auction"
   deadline).

## Layout

```
run_valuation.py              # CLI entrypoint — re-run this
auction_model/
  config.py                   # league rules, scoring, replacement level
  data_pipeline.py            # load/clean historical CSV
  keepers.py                  # keeper pricing + inflation
  valuation.py                # VBD + anchor blend + sanity checks
  rookie_board.py             # college/rookie draft board
data/
  historical_salaries_2025_raw.csv
  projections_2026.template.csv     # copy -> projections_2026.csv
  rookie_pool.template.csv          # copy -> rookie_pool.csv
  keeper_overrides.template.csv     # copy -> keeper_overrides.csv
output/                       # generated CSVs (checked in as a sample run)
```
