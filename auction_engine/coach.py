"""Draft-stage coach: one or two plain-English sentences on what matters
RIGHT NOW, derived only from state the tool already computes (league
sales so far, Sam's open slots / cash / position counts and needs, the
budget-deployment monitor, and the best remaining player per position).

Pure function, no side effects, no new valuation -- this never changes a
stop or a ceiling. It exists because the practice-draft post-mortems kept
showing the same avoidable mistakes (cheap filler while cash remained, a
3rd TE / 3rd QB, money not reaching WR) that the numbers alone don't
shout about.
"""
from __future__ import annotations

TOTAL_AUCTION_OPENINGS = 113  # official commissioner data: league-wide open slots

# Roster shape the advice reasons about (matches config.STARTING_LINEUP +
# what Sam already holds as keepers / college rights). Kept local and
# small on purpose: these are conversational thresholds, not valuation.
_QB_CAP = 2          # Dart + Mendoza already; a 2nd auction QB only as a clear starter
_TE_CAP = 2
_RB_DEPTH_SET_AT = 3  # Walker/Montgomery/Skattebo: depth is set, only FLEX-level RBs add


def draft_phase(sales_so_far: int, total_openings: int = TOTAL_AUCTION_OPENINGS) -> str:
    frac = sales_so_far / max(1, total_openings)
    if frac < 0.25:
        return "EARLY"
    if frac < 0.65:
        return "MIDDLE"
    if frac < 0.90:
        return "LATE"
    return "ENDGAME"


def coach_message(
    *,
    sales_so_far: int,
    open_slots: int,
    budget_remaining: float,
    position_counts: dict,
    position_needs: dict,
    monitor_status: str,
    projected_unused: float | None,
    best_remaining: dict | None = None,
    total_openings: int = TOTAL_AUCTION_OPENINGS,
) -> dict:
    """best_remaining: {position: {"player": str, "recommended_stop": float}}
    for the top remaining player by Sam's own stop at each position."""
    phase = draft_phase(sales_so_far, total_openings)
    counts = {p: int(position_counts.get(p, 0)) for p in ("QB", "RB", "WR", "TE")}
    needs = {p: int(position_needs.get(p, 0)) for p in ("QB", "RB", "WR", "TE", "FLEX")}
    starter_holes = sum(needs.values())
    per_slot = budget_remaining / open_slots if open_slots > 0 else 0.0
    best = best_remaining or {}

    def best_at(pos: str) -> str:
        b = best.get(pos)
        return f" Best {pos} left: {b['player']} (stop ${b['recommended_stop']:.0f})." if b else ""

    lines: list[str] = []

    # ---- 1. Where you are, and what the cash needs to do ----
    if open_slots <= 0:
        lines.append(f"Roster full ({sales_so_far}/{total_openings} league sales). Nothing left to buy -- "
                     f"you're done; ${budget_remaining:.0f} unspent is now irrelevant.")
        return {"phase": phase, "headline": lines[0], "points": [], "sales_so_far": sales_so_far,
                "open_slots": open_slots, "budget_remaining": budget_remaining}

    if phase == "EARLY":
        head = (f"Early -- {sales_so_far}/{total_openings} sold. You have {open_slots} slots and "
                f"${budget_remaining:.0f} (${per_slot:.0f}/slot). This is the starters window: "
                f"go to your stop for the {starter_holes} starting hole(s), not $10 under it. "
                f"No bench buys yet.")
    elif phase == "MIDDLE":
        head = (f"Middle -- {sales_so_far}/{total_openings} sold. {open_slots} slots, ${budget_remaining:.0f} "
                f"(${per_slot:.0f}/slot). The top of the board is thinning: any remaining starter you "
                f"want, buy now at your stop. Value drops hard once your lineup is set.")
    elif phase == "LATE":
        head = (f"Late -- {sales_so_far}/{total_openings} sold. {open_slots} slots, ${budget_remaining:.0f} "
                f"(${per_slot:.0f}/slot). Spend it: bid AT your stops, pass on $1-3 filler unless "
                f"slots == dollars. Run Exact on anyone you'd pay $20+ for.")
    else:
        head = (f"Endgame -- {sales_so_far}/{total_openings} sold. {open_slots} slots, ${budget_remaining:.0f}. "
                f"Every remaining slot needs a body; take the best available at each nomination, "
                f"but do not leave cash: bid to your legal max on the last real player you want.")
    lines.append(head)

    points: list[str] = []

    # ---- 2. Budget-deployment monitor, translated ----
    if monitor_status == "FINAL_SLOTS_CASH_STRANDED":
        points.append(f"CASH STRANDING: {open_slots} slot(s) left and ~${projected_unused:.0f} projected unused. "
                      f"Bid at or above stop on the best player nominated -- Run Exact first, then go.")
    elif monitor_status == "SERIOUS_UNDERSPEND_RISK":
        points.append(f"Serious underspend risk (~${projected_unused:.0f} projected unused): you are winning too "
                      f"cheap. Bid to your stops, not under, and stop taking $1 fills.")
    elif monitor_status == "WATCH_SPEND":
        points.append(f"Watch spend (~${projected_unused:.0f} projected unused): lean toward buying at stop "
                      f"on players you actually want; no more sub-$5 filler.")

    # ---- 3. Positional discipline from what you already hold ----
    # (priority order: the first two survive)
    if 0 < open_slots <= 3 and budget_remaining > 20:
        points.append(f"Only {open_slots} slot(s) for ${budget_remaining:.0f}: each remaining buy should be "
                      f"~${per_slot:.0f}. A $1 pickup here strands ${per_slot - 1:.0f}.")
    if needs["TE"] > 0:
        points.append("You still need a starting TE." + best_at("TE"))
    wr_starting_gap = needs["WR"] + needs["FLEX"]
    if counts["WR"] < 4 or wr_starting_gap > 0:
        points.append(f"WR is where the money goes ({counts['WR']} rostered, {wr_starting_gap} WR/FLEX starter "
                      f"slot(s) open). Pay to your stop for the next real WR starter." + best_at("WR"))
    if counts["RB"] >= _RB_DEPTH_SET_AT and needs["RB"] == 0:
        points.append("RB depth is set -- only a FLEX-level starter adds anything; no bench RBs at $15+.")
    if needs["TE"] == 0 and counts["TE"] >= _TE_CAP:
        points.append("TE is done -- do not buy a 3rd.")
    if counts["QB"] >= 1:
        points.append("QB: Dart + Mendoza are enough. Only buy a QB who is a clear week-1 starter over Dart, "
                      "and never a 3rd.")

    # Keep it to the two most important -- the order above is the priority order.
    return {
        "phase": phase, "headline": head, "points": points[:2],
        "sales_so_far": sales_so_far, "open_slots": open_slots,
        "budget_remaining": budget_remaining, "per_slot": round(per_slot, 1),
    }
