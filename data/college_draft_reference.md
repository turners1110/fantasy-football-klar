# Fancy Football League — College Draft Data

**Source:** "2025 Fancy Football League Rosters" Google Sheet  
**Pulled:** 2026-08-29

## Data quality warning

The sheet lags real-world player status. Manual verification already confirmed:

- **Sam:** Fernando Mendoza, Isaiah Bond, Jordan James — all debuted in NFL despite "college" labels
- **Master tracker stale examples:** TreVeyon Henderson, Brock Bowers, Caleb Williams, Xavier Worthy, Kendall Milton listed "IN COLLEGE" but are active NFL players

Treat every "IN COLLEGE" / not-yet-debuted status as **unverified** until checked against nflverse or commissioner.

## Machine-readable files

| File | Contents |
|------|----------|
| `college_draft_order.csv` | Fixed 12-team order (non-snake, repeats each of 3 rounds) |
| `college_draft_completed_picks.csv` | All 144 picks across 4 completed draft classes |
| `college_holdings.csv` | Current college-rights stash by owner (not yet converted to veteran) |
| `college_prospect_projections.csv` | Optional prospect projection inputs |
| `college_pick_ownership.template.csv` | Template for pick-trade ownership overrides |

## Draft order (picks 1–12, 13–24, 25–36)

Sam → Travis → Reid → James → Shane → CJ → Brad → Ryan J → Evan → Brandon → Jason → Coby

(Sam picks **1st, 13th, and 25th** overall each year.)

## Legacy owner names

Historical picks reference **Paul** and **Ryan B**, who do not appear on the current 12-team roster. Rights from those picks may have transferred without the sheet being updated — verify with commissioner before trade decisions.

## Confirmed Sam debut conversions (pending $1 league conversion)

| Player | College | NFL status |
|--------|---------|------------|
| Isaiah Bond | Texas | Cleveland Browns, 2025 starter |
| Jordan James | Oregon | SF 49ers, 2025 draft |
| Fernando Mendoza | Indiana | #1 overall 2026, Las Vegas Raiders |

## Still college (Sam)

- Jojo Earle (WR, UNLV/TCU/Alabama)
- Nyck Harbor (WR, South Carolina)
- Eugene Wilson III (WR, Florida/LSU)
