"""Strict veteran auction eligibility classification and pool filtering."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import college_prospects, config, data_pipeline

# Canonical auction statuses (Part 1)
VETERAN_AUCTION_ELIGIBLE = "VETERAN_AUCTION_ELIGIBLE"
VETERAN_KEPT = "VETERAN_KEPT"
VETERAN_ROSTERED_RELEASED = "VETERAN_ROSTERED_RELEASED"
COLLEGE_RIGHTS_HELD = "COLLEGE_RIGHTS_HELD"
DEBUTED_PENDING_CONVERSION = "DEBUTED_PENDING_CONVERSION"
INELIGIBLE = "INELIGIBLE"
UNKNOWN_STATUS = "UNKNOWN_STATUS"

AUCTION_ALLOWED = frozenset({VETERAN_AUCTION_ELIGIBLE, VETERAN_ROSTERED_RELEASED})
AUCTION_BLOCKED = frozenset({
    VETERAN_KEPT, COLLEGE_RIGHTS_HELD, DEBUTED_PENDING_CONVERSION,
    INELIGIBLE, UNKNOWN_STATUS,
})

COLLEGE_TEAM_PATTERN = (
    r"\b(Alabama|Ohio State|Texas|Notre Dame|Georgia|Michigan|USC|LSU|Oregon|"
    r"Florida|Clemson|Penn State|Indiana|South Carolina|Auburn|BYU|Cal|"
    r"Nebraska|Miami|Ole Miss|Tennessee|Washington|Colorado|Minnesota|"
    r"Louisville|Mizzou|NC State|Arizona State|Sam Houston|Tulane|Utah State|"
    r"Michigan State|North Texas|South Florida|Texas A&M|Texas Tech|Florida State|"
    r"UNLV|TCU|Virginia Tech|Wisconsin|Iowa|Kansas|Oklahoma State)\b"
)


def _load_college_index(holdings_path: Path, nflverse_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (holdings, audit) for college-rights lookup."""
    if not holdings_path.exists():
        return pd.DataFrame(), pd.DataFrame()
    holdings = college_prospects.load_college_holdings(holdings_path)
    audit = college_prospects.audit_debut_status(holdings, nflverse_dir)
    return holdings, audit


def _nflverse_debut_keys(nflverse_dir: Path) -> dict[str, dict]:
    stats_path = nflverse_dir / "player_stats_reg_2025.csv"
    if not stats_path.exists():
        return {}
    stats = pd.read_csv(stats_path)
    if "player_display_name" not in stats.columns:
        return {}
    fantasy = stats[stats["position"].isin({"QB", "RB", "WR", "TE"})].copy()
    out: dict[str, dict] = {}
    for _, row in fantasy.iterrows():
        games = int(row.get("games") or 0)
        if games < 1:
            continue
        key = data_pipeline._normalize_name(row["player_display_name"])
        out[key] = {
            "nflverse_name": row["player_display_name"],
            "nfl_team": row.get("recent_team", ""),
            "games_played": games,
            "season": int(row.get("season", 0)),
        }
    return out


def _college_status_from_audit(audit_row: pd.Series | None) -> str | None:
    if audit_row is None:
        return None
    raw = str(audit_row.get("status", ""))
    if raw == "college":
        return COLLEGE_RIGHTS_HELD
    if raw == "debuted_pending_conversion":
        return DEBUTED_PENDING_CONVERSION
    if raw in {"nfl_drafted_not_debuted", "data_conflict"}:
        return INELIGIBLE
    return None


def classify_player_eligibility(
    player: str,
    position: str,
    nfl_team: str | None,
    source_roster: str,
    on_historical: bool,
    has_salary: bool,
    will_keep: bool,
    college_audit: pd.Series | None,
    debut_info: dict | None,
    fp_only: bool,
) -> dict:
    """Return eligibility record for one player."""
    canonical = data_pipeline._normalize_name(player)
    verified_debut = debut_info is not None and int(debut_info.get("games_played", 0)) >= 1

    college_status = _college_status_from_audit(college_audit)
    if college_status is not None:
        return _record(
            player, canonical, position, nfl_team, source_roster,
            college_status, False,
            f"college_holdings audit: {college_audit.get('status_reason', '')}",
            "college_holdings.csv",
            college_audit.get("debut_evidence", ""),
            confidence=0.95 if college_status == COLLEGE_RIGHTS_HELD else 0.85,
            warning="college_rights_block" if college_status != DEBUTED_PENDING_CONVERSION else "pending_conversion",
        )

    if will_keep and has_salary:
        return _record(
            player, canonical, position, nfl_team, source_roster,
            VETERAN_KEPT, False, "keeper decision removes from auction supply",
            source_roster, "", confidence=1.0,
        )

    if on_historical and has_salary:
        status = VETERAN_ROSTERED_RELEASED if not will_keep else VETERAN_KEPT
        eligible = status == VETERAN_ROSTERED_RELEASED
        return _record(
            player, canonical, position, nfl_team, source_roster,
            status, eligible,
            "2025 league roster with confirmed salary",
            "historical_salaries_2025_raw.csv", "",
            confidence=0.9,
        )

    if verified_debut:
        return _record(
            player, canonical, position, nfl_team, source_roster,
            VETERAN_AUCTION_ELIGIBLE, True,
            f"nflverse verified debut: {debut_info['games_played']} reg-season games",
            "nflverse/player_stats_reg_2025.csv",
            str(debut_info),
            confidence=0.95,
        )

    if fp_only:
        return _record(
            player, canonical, position, nfl_team, source_roster,
            UNKNOWN_STATUS, False,
            "FantasyPros-ranked only; no verified NFL debut or league salary",
            "FantasyPros_2026_Draft_ALL_Rankings.csv", "",
            confidence=0.3,
            warning="fp_only_no_debut_verification",
        )

    if has_salary:
        return _record(
            player, canonical, position, nfl_team, source_roster,
            VETERAN_AUCTION_ELIGIBLE, True,
            "confirmed salary without college-rights conflict",
            source_roster, "",
            confidence=0.85,
        )

    return _record(
        player, canonical, position, nfl_team, source_roster,
        UNKNOWN_STATUS, False,
        "insufficient evidence for veteran auction eligibility",
        source_roster, "",
        confidence=0.2,
        warning="unknown_status",
    )


def _record(
    player: str,
    canonical: str,
    position: str,
    nfl_team: str | None,
    source_roster: str,
    status: str,
    auction_eligible: bool,
    reason: str,
    evidence_source: str,
    debut_evidence: str,
    confidence: float = 0.5,
    warning: str = "",
) -> dict:
    return {
        "player": player,
        "canonical_player_id": canonical,
        "position": position,
        "nfl_team": nfl_team or "",
        "source_roster": source_roster,
        "source_status": status,
        "verified_nfl_regular_season_debut": debut_evidence,
        "league_veteran_status": status.startswith("VETERAN"),
        "league_college_rights_status": status in {COLLEGE_RIGHTS_HELD, DEBUTED_PENDING_CONVERSION},
        "conversion_status": "pending" if status == DEBUTED_PENDING_CONVERSION else "",
        "final_auction_status": status,
        "auction_eligible": auction_eligible,
        "eligibility_reason": reason,
        "evidence_source": evidence_source,
        "verification_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "confidence": confidence,
        "warning": warning,
    }


def build_eligibility_audit(
    pool: pd.DataFrame,
    salaries: pd.DataFrame,
    roster: pd.DataFrame | None = None,
    holdings_path: Path | None = None,
    nflverse_dir: Path | None = None,
) -> pd.DataFrame:
    """Classify every player in the combined universe."""
    holdings_path = holdings_path or Path("data/college_holdings.csv")
    nflverse_dir = nflverse_dir or Path("data/nflverse")

    _, college_audit = _load_college_index(holdings_path, nflverse_dir)
    college_by_key = {}
    if not college_audit.empty:
        college_audit = college_audit.copy()
        college_audit["_key"] = college_audit["player"].map(data_pipeline._normalize_name)
        college_by_key = college_audit.set_index("_key").to_dict("index")

    debut_keys = _nflverse_debut_keys(nflverse_dir)

    hist_keys = set(salaries["player"].map(data_pipeline._normalize_name))
    hist_salary = salaries.copy()
    hist_salary["_key"] = hist_salary["player"].map(data_pipeline._normalize_name)
    salary_lookup = hist_salary.set_index("_key")["salary_2025"].to_dict()

    keep_lookup: dict[tuple[str, str], bool] = {}
    if roster is not None and "will_keep" in roster.columns:
        for _, r in roster.iterrows():
            keep_lookup[(r["team"], r["player"])] = bool(r.get("will_keep", False))

    rows = []
    seen: set[str] = set()
    for _, row in pool.drop_duplicates("player").iterrows():
        player = row["player"]
        key = data_pipeline._normalize_name(player)
        if key in seen:
            continue
        seen.add(key)

        on_hist = key in hist_keys
        has_salary = pd.notna(salary_lookup.get(key)) or bool(row.get("has_confirmed_salary", False))
        fp_only = str(row.get("keep_source", "")) == "not_prev_rostered" or (
            not on_hist and pd.isna(row.get("team"))
        )
        nfl_team = row.get("nfl_team", row.get("TEAM", ""))
        source = "historical_salaries" if on_hist else "fantasypros_expansion"

        will_keep = False
        if roster is not None:
            matches = roster[roster["player"].map(data_pipeline._normalize_name) == key]
            if not matches.empty:
                will_keep = bool(matches["will_keep"].astype(bool).any())

        rec = classify_player_eligibility(
            player=player,
            position=str(row.get("position", "")),
            nfl_team=str(nfl_team) if pd.notna(nfl_team) else None,
            source_roster=source,
            on_historical=on_hist,
            has_salary=has_salary,
            will_keep=will_keep,
            college_audit=pd.Series(college_by_key[key]) if key in college_by_key else None,
            debut_info=debut_keys.get(key),
            fp_only=fp_only and not on_hist,
        )
        rows.append(rec)

    return pd.DataFrame(rows)


def filter_veteran_auction_pool(
    pool: pd.DataFrame,
    audit: pd.DataFrame,
    fail_on_ineligible: bool = False,
) -> pd.DataFrame:
    """Return only auction-eligible veterans."""
    eligible_keys = set(
        audit.loc[audit["auction_eligible"].astype(bool), "canonical_player_id"]
    )
    out = pool.copy()
    out["_key"] = out["player"].map(data_pipeline._normalize_name)
    filtered = out[out["_key"].isin(eligible_keys)].drop(columns=["_key"])
    blocked = out[~out["_key"].isin(eligible_keys)]
    if fail_on_ineligible and not blocked.empty:
        names = blocked["player"].head(10).tolist()
        raise ValueError(f"Ineligible players in auction pool request: {names}")
    return filtered.reset_index(drop=True)


def assert_roster_eligibility(
    players: list[str],
    audit: pd.DataFrame,
) -> tuple[bool, list[str]]:
    """Verify every selected player is auction-eligible or a keeper."""
    audit_idx = audit.set_index("canonical_player_id")
    bad = []
    for p in players:
        key = data_pipeline._normalize_name(p)
        if key not in audit_idx.index:
            bad.append(f"{p}: not in eligibility audit")
            continue
        row = audit_idx.loc[key]
        status = row["final_auction_status"]
        if status not in AUCTION_ALLOWED and status != VETERAN_KEPT:
            bad.append(f"{p}: {status}")
    return len(bad) == 0, bad


def contamination_report(audit: pd.DataFrame, selected_players: list[str]) -> pd.DataFrame:
    """List ineligible players that appear in an optimized roster."""
    sel = {data_pipeline._normalize_name(p) for p in selected_players}
    blocked = audit[
        audit["canonical_player_id"].isin(sel)
        & ~audit["auction_eligible"].astype(bool)
        & (audit["final_auction_status"] != VETERAN_KEPT)
    ]
    return blocked
