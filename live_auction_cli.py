#!/usr/bin/env python3
"""Live Auction MVP -- minimal CLI interface.

A thin REPL wrapper around the already-tested auction_engine backend
(auction_engine/live_values.py, market_adjustments.py,
live_recommendations.py, live_roster_paths.py, auction_state*.py). This
file does NOT reimplement or modify any of that logic -- it only calls
it and formats the results for a terminal.

Run: python3 live_auction_cli.py
Type `help` at the prompt for the command list.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from auction_engine.auction_state import AuctionState, TeamState
from auction_engine.auction_state_store import AuctionStateStore
from auction_engine.auction_reducer import IllegalEventError
from auction_engine.live_values import compute_live_sam_values
from auction_engine.live_roster_paths import compute_live_roster_paths
from auction_engine.market_adjustments import MarketAdjustmentState, live_expected_price
from auction_engine.live_recommendations import compute_recommended_bid
from mock_draft.data import load_confirmed_pool_and_teams

LIVE_MVP_DIR = BASE_DIR / "outputs" / "auction_rebuild" / "live_mvp"
SNAPSHOT_DIR = LIVE_MVP_DIR / "cli_snapshots"
DEFAULT_LOG_PATH = LIVE_MVP_DIR / "cli_session.jsonl"
DEFAULT_INITIAL_STATE_PATH = LIVE_MVP_DIR / "cli_initial_state.json"
ERROR_LOG_PATH = LIVE_MVP_DIR / "cli_error.log"

COLLEGE_RIGHTS = {"Fernando Mendoza", "Isaiah Bond"}

COMMANDS = {
    "status": "Show Sam's budget, open slots, position needs, reserve, legal max bid, roster.",
    "sale <player> <team> <price>": "Record a sale through the real event engine.",
    "check <player>": "Show live expected/conservative price, ceiling, recommended stop, and why.",
    "targets": "Show top live-recommended targets with current recommended stop for each.",
    "paths": "Show the 5 complete roster paths (spend, points, feasibility).",
    "undo": "Undo the last recorded sale.",
    "save <name>": "Save a named snapshot of the current auction state.",
    "load <name>": "Restore a named snapshot.",
    "emergency": "Print the static emergency bid sheet / Sunday plan.",
    "help": "List all commands.",
    "quit / exit": "Exit the tool.",
}


class AuctionCLI:
    """All command handlers as plain methods (return a string) so they
    can be tested directly without going through the REPL loop."""

    def __init__(self, budget_scenario: str = "primary", log_path: Path | None = DEFAULT_LOG_PATH):
        self.budget_scenario = budget_scenario
        self.players, teams, _ = load_confirmed_pool_and_teams(budget_scenario=budget_scenario)
        self.initial_state = self._build_initial_state(teams)
        self.log_path = log_path
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists():
                self.log_path.unlink()  # fresh session log each launch
        self.store = AuctionStateStore(self.initial_state, log_path=self.log_path)
        self.market_state = MarketAdjustmentState()
        if self.log_path is not None:
            DEFAULT_INITIAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            DEFAULT_INITIAL_STATE_PATH.write_text(json.dumps(self.initial_state.to_dict()))

    def _build_initial_state(self, teams) -> AuctionState:
        st = AuctionState(auction_id="cli-session", rules_version="v1", model_version="live-mvp-cli-v1", sam_team_id="Sam")
        for team_id, t in teams.items():
            roster = [{"player_id": n, "display_name": n, "position": p, "price": pr, "is_keeper": True, "projected_points": pts}
                      for n, p, pr, pts in t.roster]
            st.teams[team_id] = TeamState(team_id=team_id, budget_remaining=t.budget_remaining, roster=roster,
                                           keeper_ids={n for n, p, pr, pts in t.roster})
        st.available_pool = {name: {"display_name": name, "position": p.position, "projected_points": p.projected_points,
                                     "base_value": p.base_value} for name, p in self.players.items()}
        st.college_rights_excluded = set(COLLEGE_RIGHTS)
        return st

    # ---- helpers ----

    def _sam(self):
        return self.store.state.teams["Sam"]

    def _remaining_pool(self) -> dict:
        return dict(self.store.state.available_pool)

    def _avg_marginal_value(self, position: str) -> float | None:
        pool = {n: v for n, v in self._remaining_pool().items() if v["position"] == position}
        if not pool:
            return None
        rows = compute_live_sam_values(self._sam().roster, pool)
        if not rows:
            return None
        return sum(r.marginal_value for r in rows) / len(rows)

    # ---- commands ----

    def cmd_status(self) -> str:
        sam = self._sam()
        needs = sam.legal_starting_needs()
        counts = sam.position_counts
        lines = [
            f"Sam -- budget remaining: ${sam.budget_remaining:.2f}",
            f"Open roster slots: {sam.open_slots}  |  Min reserve: ${sam.min_reserve:.2f}  |  Legal max bid: ${sam.legal_max_bid:.2f}",
            f"Position needs (starters still open): {needs}",
            f"Current roster counts: {counts}",
            "Roster:",
        ]
        for p in sam.roster:
            lines.append(f"  {p['position']:3s} {p['display_name']:22s} ${p['price']:.0f}"
                         f"{'  (keeper)' if p.get('is_keeper') else ''}")
        return "\n".join(lines)

    def cmd_sale(self, player: str, team: str, price: str) -> str:
        # Check keeper/college-rights status BEFORE the "unknown player"
        # check -- keepers and college-rights players are deliberately
        # excluded from self.players / the available pool, so an
        # unknown-player check that runs first would misreport a refused
        # sale as "unknown" instead of "refused." (Bug found and fixed
        # while wiring this CLI up -- see final_report.md.)
        if player in COLLEGE_RIGHTS:
            return f"REFUSED: {player} is a college-rights asset and cannot enter the veteran auction."
        if any(player in t.keeper_ids for t in self.store.state.teams.values()):
            return f"REFUSED: {player} is a keeper and cannot be sold in the veteran auction."
        if player not in self.players and player not in self._remaining_pool():
            return f"ERROR: unknown player {player!r} -- check spelling (case-sensitive, matches the projections file)."
        try:
            price_f = float(price)
        except ValueError:
            return f"ERROR: price must be a number, got {price!r}."

        before_avg = None
        pos = self.players[player].position if player in self.players else self.store.state.available_pool.get(player, {}).get("position")
        if pos:
            before_avg = self._avg_marginal_value(pos)

        pts = self.players[player].projected_points if player in self.players else 0.0
        try:
            self.store.record("PLAYER_SOLD", {
                "player_id": player, "display_name": player, "position": pos, "winning_owner": team,
                "sale_price": price_f, "nominating_owner": None, "projected_points": pts,
            })
        except IllegalEventError as e:
            return f"REFUSED: {e}"

        # feed the market-adjustment signal with this real observation
        pre_draft_price = max(1.0, self.players[player].base_value) if player in self.players else price_f
        self.market_state.add_observation(pos or "UNKNOWN", "t1", price_f, pre_draft_price)

        lines = [f"Recorded: {player} to {team} for ${price_f:.0f}."]
        if team == "Sam" and pos:
            after_avg = self._avg_marginal_value(pos)
            if before_avg is not None and after_avg is not None:
                delta = after_avg - before_avg
                if abs(delta) >= 1:
                    lines.append(f"Impact: {pos} marginal value for Sam changed by ${delta:+.2f} "
                                 f"(now averaging ${after_avg:.2f} across remaining {pos}s).")
                else:
                    lines.append(f"Impact: no meaningful change to remaining {pos} value for Sam.")
        else:
            lines.append("No impact on Sam's own roster (someone else's purchase).")
        lines.append("")
        lines.append(self.cmd_status())
        return "\n".join(lines)

    def cmd_check(self, player: str) -> str:
        info = self.store.state.available_pool.get(player)
        if info is None:
            if player in self.store.state.sold_players:
                return f"{player} has already been sold -- see `status` or the sale log."
            return f"ERROR: unknown or unavailable player {player!r}."
        pos = info["position"]
        pre_draft_price = max(1.0, info.get("base_value", 1.0))

        try:
            open_starter = sum(1 for t in self.store.state.teams.values() if t.legal_starting_needs().get(pos, 0) > 0)
            open_flex = sum(1 for t in self.store.state.teams.values() if t.legal_starting_needs().get("FLEX", 0) > 0)
            cash_teams = sum(1 for t in self.store.state.teams.values() if t.legal_max_bid > 10)
            supply = sum(1 for n, v in self.store.state.available_pool.items() if v["position"] == pos)
            market = live_expected_price(pre_draft_price, pos, "t1", self.market_state, open_starter, open_flex, cash_teams, supply)
        except Exception as e:
            self._log_error("check/market", e)
            market = {"live_expected_price": pre_draft_price, "calculation_label": "SOLVER_FAILURE_FALLBACK"}
            print("SOLVER_FAILURE -- falling back to the pre-draft price as the approximate expected price.")

        try:
            rows = compute_live_sam_values(self._sam().roster, {player: info})
            marginal_value = rows[0].marginal_value if rows else 0.0
            expected_role = rows[0].expected_role if rows else "unknown"
            calc_label = "APPROXIMATE_LIVE_ROSTER_VALUE"
        except Exception as e:
            self._log_error("check/values", e)
            marginal_value = 0.0
            expected_role = "unknown (SOLVER_FAILURE)"
            calc_label = "SOLVER_FAILURE"

        sam = self._sam()
        rec = compute_recommended_bid(
            player=player, safety_adjusted_ceiling=max(1.0, marginal_value), legal_max_bid=sam.legal_max_bid,
            portfolio_feasibility_limit=None, confidence=6, live_expected_price=market["live_expected_price"],
        )

        lines = [
            f"{player} ({pos})",
            f"  Live expected price: ${market['live_expected_price']:.0f}  [{market.get('calculation_label', '?')}]",
            f"  Conservative estimate: ${market['live_expected_price'] * 1.15:.0f} (heuristic markup)",
            f"  Sam marginal roster value: ${marginal_value:.2f}  [{calc_label}]  (expected role: {expected_role})",
            f"  Legal max bid: ${sam.legal_max_bid:.2f}",
            f"  RECOMMENDED STOP: ${rec.recommended_final_bid:.0f}  [{rec.recommendation_type}]",
            f"  Reason: {rec.reason}",
        ]
        return "\n".join(lines)

    def cmd_targets(self, n: int = 10) -> str:
        pool = self._remaining_pool()
        if not pool:
            return "No remaining players in the pool."
        rows = compute_live_sam_values(self._sam().roster, pool)
        rows_sorted = sorted(rows, key=lambda r: -r.marginal_value)[:n]
        sam = self._sam()
        lines = [f"Top {len(rows_sorted)} live targets by Sam marginal value:"]
        for r in rows_sorted:
            info = pool[[k for k, v in pool.items() if v["display_name"] == r.player][0]] if any(
                v["display_name"] == r.player for v in pool.values()) else None
            pre_draft_price = max(1.0, info.get("base_value", 1.0)) if info else 1.0
            rec = compute_recommended_bid(
                player=r.player, safety_adjusted_ceiling=max(1.0, r.marginal_value), legal_max_bid=sam.legal_max_bid,
                portfolio_feasibility_limit=None, confidence=6, live_expected_price=pre_draft_price,
            )
            lines.append(f"  {r.player:22s} {r.position:3s} marginal=${r.marginal_value:6.2f} "
                        f"role={r.expected_role:16s} stop=${rec.recommended_final_bid:.0f} [{rec.recommendation_type}]")
        return "\n".join(lines)

    def cmd_paths(self) -> str:
        pool = self._remaining_pool()
        remaining_for_paths = {n: {"display_name": n, "position": v["position"], "projected_points": v["projected_points"],
                                    "expected_price": max(1.0, v.get("base_value", 1.0)),
                                    "conservative_price": max(1.0, v.get("base_value", 1.0) * 1.15)}
                                for n, v in pool.items()}
        try:
            paths = compute_live_roster_paths(self._sam(), remaining_for_paths)
        except Exception as e:
            self._log_error("paths", e)
            return "SOLVER_FAILURE -- roster paths unavailable right now. Use `emergency` for the static fallback plan."
        lines = ["Complete roster paths:"]
        for style, r in paths.items():
            pts = f"{r['starting_points']:.1f}" if r.get("starting_points") is not None else "n/a"
            lines.append(f"  {style:26s} status={r['status']:10s} spend=${r['spend']:<4} starting_pts={pts}")
        return "\n".join(lines)

    def cmd_undo(self) -> str:
        if not self.store.events or all(e.event_type == "EVENT_UNDONE" for e in self.store.events):
            return "Nothing to undo."
        try:
            self.store.undo_last()
        except ValueError as e:
            return f"Nothing to undo: {e}"
        return "Undo complete.\n\n" + self.cmd_status()

    def cmd_save(self, name: str) -> str:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        target_log = SNAPSHOT_DIR / f"{name}.jsonl"
        target_initial = SNAPSHOT_DIR / f"{name}_initial.json"
        target_initial.write_text(json.dumps(self.initial_state.to_dict()))
        with target_log.open("w") as f:
            for e in self.store.events:
                f.write(json.dumps(e.to_dict()) + "\n")
        return f"Saved snapshot '{name}' ({len(self.store.events)} events) to {SNAPSHOT_DIR}/"

    def cmd_load(self, name: str) -> str:
        target_log = SNAPSHOT_DIR / f"{name}.jsonl"
        target_initial = SNAPSHOT_DIR / f"{name}_initial.json"
        if not target_log.exists() or not target_initial.exists():
            return f"ERROR: no snapshot named '{name}' found in {SNAPSHOT_DIR}/"
        initial_state = AuctionState.from_dict(json.loads(target_initial.read_text()))
        self.initial_state = initial_state
        self.store = AuctionStateStore.recover(initial_state, target_log)
        self.store.log_path = self.log_path  # continue logging new events to the live session log
        return f"Loaded snapshot '{name}'.\n\n" + self.cmd_status()

    def cmd_emergency(self) -> str:
        parts = []
        sheet_path = LIVE_MVP_DIR / "static_emergency_bid_sheet.csv"
        plan_path = BASE_DIR / "outputs" / "auction_rebuild" / "phase4" / "sam_sunday_plan.txt"
        if plan_path.exists():
            parts.append("=== SAM SUNDAY PLAN ===\n" + plan_path.read_text())
        if sheet_path.exists():
            parts.append("=== STATIC EMERGENCY BID SHEET (raw CSV) ===\n" + sheet_path.read_text())
        if not parts:
            return "ERROR: no emergency sheet found on disk."
        return "\n\n".join(parts)

    def cmd_help(self) -> str:
        lines = ["Commands:"]
        for cmd, desc in COMMANDS.items():
            lines.append(f"  {cmd:28s} {desc}")
        return "\n".join(lines)

    def _log_error(self, where: str, exc: Exception):
        ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG_PATH.open("a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {where}: {exc}\n")
            f.write(traceback.format_exc() + "\n")

    def dispatch(self, line: str) -> str:
        """Parse and run one command line. Never raises -- catches and
        logs any unexpected exception, returning an error string instead."""
        line = line.strip()
        if not line:
            return ""
        parts = line.split()
        cmd, args = parts[0].lower(), parts[1:]
        try:
            if cmd == "status":
                return self.cmd_status()
            if cmd == "sale":
                if len(args) != 3:
                    return "Usage: sale <player> <team> <price>  (player name may need quotes if it has spaces -- use underscores if unsure, e.g. Josh_Allen)"
                player = args[0].replace("_", " ")
                return self.cmd_sale(player, args[1], args[2])
            if cmd == "check":
                if len(args) < 1:
                    return "Usage: check <player>"
                return self.cmd_check(" ".join(args).replace("_", " "))
            if cmd == "targets":
                return self.cmd_targets()
            if cmd == "paths":
                return self.cmd_paths()
            if cmd == "undo":
                return self.cmd_undo()
            if cmd == "save":
                if len(args) != 1:
                    return "Usage: save <name>"
                return self.cmd_save(args[0])
            if cmd == "load":
                if len(args) != 1:
                    return "Usage: load <name>"
                return self.cmd_load(args[0])
            if cmd == "emergency":
                return self.cmd_emergency()
            if cmd == "help":
                return self.cmd_help()
            if cmd in ("quit", "exit"):
                return "__QUIT__"
            return f"Unknown command {cmd!r}. Type `help` for the command list."
        except Exception as e:
            self._log_error(f"dispatch({line!r})", e)
            return f"SOLVER_FAILURE / unexpected error (logged to {ERROR_LOG_PATH}): {e}"


def main():
    print("Live Auction MVP CLI -- type `help` for commands, `quit` to exit.")
    print("Building initial state from the confirmed pre-draft pool ($223 primary budget)...")
    t0 = time.time()
    cli = AuctionCLI()
    print(f"Ready in {time.time()-t0:.2f}s.\n")
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        result = cli.dispatch(line)
        if result == "__QUIT__":
            print("Goodbye.")
            break
        if result:
            print(result)
            print()


if __name__ == "__main__":
    main()
