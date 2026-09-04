"""Strict veteran auction eligibility classification and pool filtering."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import college_prospects, config, data_pipeline
from .confirmed_keeper_pipeline import normalize_name

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
            "evidence_source": "nflverse/player_stats_reg_2025.csv",
        }
    return out


def _active_player_registry_evidence(base_dir: Path, nflverse_dir: Path) -> dict[str, dict]:
    """PHASE 3A FIX: nflverse (via _nflverse_debut_keys) is not the only
    active-player registry in this repo -- data/nflverse/player_stats_reg_2025.csv
    is simply absent in this environment, which is a missing FILE, not
    missing EVIDENCE. Prior-season statistics measure performance; they
    do not define eligibility on their own, and this repo has several
    other real active-player registries. Merged here, in priority order
    (first match wins, each tagged with the source that actually provided
    it -- never silently blended):
      1. nflverse reg-season stats (if the file is ever present)
      2. data/actuals_2025.csv -- real 2025 season stats with a games column
      3. data/fantasy_data_last_year_clean.csv -- 2025 stats + roster_status
      4. data/projections_2026.csv -- current-season projection; presence
         alone (even with zero prior games, e.g. a real rookie) is
         evidence a real, currently-relevant NFL player is being tracked
    This replaces phase 2B's fp_only_fallback_eligible guess (which
    approximated "probably real" from FantasyPros-rank presence alone,
    confidence 0.3) with actual roster/production evidence wherever it
    exists in the repo."""
    evidence: dict[str, dict] = dict(_nflverse_debut_keys(nflverse_dir))

    actuals_path = base_dir / "data" / "actuals_2025.csv"
    if actuals_path.exists():
        actuals = pd.read_csv(actuals_path)
        for _, row in actuals.iterrows():
            key = data_pipeline._normalize_name(row["player"])
            games = row.get("games")
            if key in evidence or pd.isna(games) or games < 1:
                continue
            evidence[key] = {
                "games_played": int(games), "nfl_team": row.get("nfl_team", ""),
                "evidence_source": "data/actuals_2025.csv",
            }

    fdly_path = base_dir / "data" / "fantasy_data_last_year_clean.csv"
    if fdly_path.exists():
        fdly = pd.read_csv(fdly_path)
        for _, row in fdly.iterrows():
            key = data_pipeline._normalize_name(row["player"])
            games = row.get("games_played")
            if key in evidence or pd.isna(games) or games < 1:
                continue
            evidence[key] = {
                "games_played": int(games), "nfl_team": row.get("nfl_team", ""),
                "evidence_source": "data/fantasy_data_last_year_clean.csv",
            }

    proj_path = base_dir / "data" / "projections_2026.csv"
    if proj_path.exists():
        proj = pd.read_csv(proj_path)
        for _, row in proj.iterrows():
            key = data_pipeline._normalize_name(row["player"])
            if key in evidence:
                continue
            evidence[key] = {
                "games_played": 0, "nfl_team": row.get("nfl_team", ""),
                "evidence_source": "data/projections_2026.csv (current-season projection; no prior-season "
                                    "stats required -- covers real rookies and players with no 2025 record)",
            }

    return evidence


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
    fp_only_fallback_eligible: bool = False,
) -> dict:
    """Return eligibility record for one player.

    fp_only_fallback_eligible: PHASE 2B addition. This classifier's
    fp_only branch requires verified nflverse debut data
    (data/nflverse/player_stats_reg_2025.csv) to distinguish a real
    active veteran free agent (e.g. a well-known starter who simply isn't
    on any of this league's 12 rosters) from a genuinely retired/inactive
    player -- and that file does not exist in this environment. With it
    absent, hundreds of obviously-active real veterans (Mike Evans,
    Stefon Diggs, Courtland Sutton, etc., confirmed by manual spot-check)
    were misclassified UNKNOWN_STATUS/ineligible, shrinking the auction
    pool below what a 12-team/9-pick-per-team draft needs to complete.
    Defaults to False (preserves the strict, conservative original
    behavior for run_valuation.py's real production price sheet, where
    excluding an uncertain player is the safer error). Set True only for
    the mock-draft simulator, where pool depth/completion matters more
    than excluding a rare false positive -- see
    outputs/auction_rebuild/audit/eligibility_path_reconciliation.csv for
    this as the one documented, explained divergence between the two
    production paths."""
    canonical = data_pipeline._normalize_name(player)
    # PHASE 3A: any evidence from _active_player_registry_evidence counts,
    # not just a games_played>=1 debut -- a real rookie with a current
    # 2026 projection but no 2025 games is still a legitimate active
    # player, not "unverified." See that function's docstring for the
    # full evidence-source priority order.
    verified_active_player = debut_info is not None

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

    if on_historical and not has_salary and not will_keep:
        # PHASE 2B FIX: a player IS on this league's own real 2025 roster
        # (direct positive evidence they're a genuine, known player) but
        # has no recorded salary_2025 (e.g. a late-season waiver pickup
        # never priced) -- the pre-existing decision tree had no branch
        # for this and fell through to the generic "insufficient
        # evidence" UNKNOWN_STATUS bucket, which is wrong: we have BETTER
        # evidence for this player than for an unverified FantasyPros-only
        # name, not worse. Caught in testing via real examples (Stefon
        # Diggs, Courtland Sutton, Mike Evans -- all on Coby's actual 2025
        # roster with salary_2025 blank).
        return _record(
            player, canonical, position, nfl_team, source_roster,
            VETERAN_ROSTERED_RELEASED, True,
            "2025 league roster with NO recorded salary (e.g. a late-season waiver pickup never priced) -- "
            "still a known, real player with direct roster evidence, not treated as unverified",
            "historical_salaries_2025_raw.csv", "",
            confidence=0.7,
            warning="on_historical_missing_salary",
        )

    if verified_active_player:
        games = int(debut_info.get("games_played", 0))
        reason = (
            f"verified active player: {games} reg-season games played in 2025"
            if games >= 1 else
            "verified active player: current 2026 projection on record (no 2025 games required -- "
            "covers real rookies and players with no prior-season record)"
        )
        return _record(
            player, canonical, position, nfl_team, source_roster,
            VETERAN_AUCTION_ELIGIBLE, True,
            reason,
            debut_info.get("evidence_source", "unknown_registry"),
            str(debut_info),
            confidence=0.95 if games >= 1 else 0.6,
        )

    if fp_only:
        if fp_only_fallback_eligible:
            return _record(
                player, canonical, position, nfl_team, source_roster,
                VETERAN_AUCTION_ELIGIBLE, True,
                "FantasyPros-ranked, no verified NFL debut or league salary -- treated as eligible "
                "under the fp_only_fallback_eligible policy because data/nflverse/player_stats_reg_2025.csv "
                "is unavailable in this environment to verify debut status one way or the other",
                "FantasyPros_2026_Draft_ALL_Rankings.csv", "",
                confidence=0.3,
                warning="fp_only_treated_as_eligible_missing_nflverse_data",
            )
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
    fp_only_fallback_eligible: bool = False,
    base_dir: Path | None = None,
) -> pd.DataFrame:
    """Classify every player in the combined universe. See
    classify_player_eligibility's docstring for fp_only_fallback_eligible
    and _active_player_registry_evidence's docstring for base_dir (the
    repo root the additional active-player registries are read from --
    defaults to this file's own repo)."""
    holdings_path = holdings_path or Path("data/college_holdings.csv")
    nflverse_dir = nflverse_dir or Path("data/nflverse")
    base_dir = base_dir or Path(__file__).parent.parent

    _, college_audit = _load_college_index(holdings_path, nflverse_dir)
    college_by_key = {}
    if not college_audit.empty:
        college_audit = college_audit.copy()
        college_audit["_key"] = college_audit["player"].map(data_pipeline._normalize_name)
        college_by_key = college_audit.set_index("_key").to_dict("index")

    debut_keys = _active_player_registry_evidence(base_dir, nflverse_dir)

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
            fp_only_fallback_eligible=fp_only_fallback_eligible,
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


CONFIRMED_OVERRIDE_STATUS = "CONFIRMED_KEEPER_OR_COLLEGE_RIGHTS_OVERRIDE"


def build_confirmed_veteran_auction_pool(
    pool: pd.DataFrame,
    salaries: pd.DataFrame,
    confirmed_keepers: pd.DataFrame,
    roster: pd.DataFrame | None = None,
    holdings_path: Path | None = None,
    nflverse_dir: Path | None = None,
    fp_only_fallback_eligible: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """ONE shared function for building the veteran auction pool -- phase
    2B requires both run_valuation.py (confirmed mode) and
    mock_draft/data.py to call this same function so their eligibility
    decisions can never silently diverge.

    Runs the full classifier (build_eligibility_audit: nflverse-debut-
    aware, college-holdings-aware, historical-salary-aware) and THEN
    force-excludes every player named in `confirmed_keepers`
    (data/keepers_2026_confirmed.csv-shaped -- both veteran keepers and
    college-rights holds) as a hard override, since that tracked file is
    this league's highest-priority, commissioner/user-confirmed source
    for these specific 2026 rosters -- higher priority than the general
    classifier's own (necessarily more heuristic) nflverse/college-
    holdings inference for the same names. Name normalization
    (confirmed_keeper_pipeline.normalize_name) is used ONLY to match
    identity between the two sources; it never decides eligibility by
    itself -- an unmatched name still goes through the general classifier
    and is not silently treated as safe or excluded on name grounds alone.

    Returns (eligible_pool, audit). `eligible_pool` carries four new
    columns: auction_eligible (always True on rows that survive -- the
    column exists for schema consistency with `audit`),
    eligibility_status, eligibility_reason, eligibility_source. `audit` is
    the full classification for every player in `pool`, eligible or not.
    """
    audit = build_eligibility_audit(
        pool, salaries, roster=roster, holdings_path=holdings_path, nflverse_dir=nflverse_dir,
        fp_only_fallback_eligible=fp_only_fallback_eligible,
    )

    confirmed_norm = set(confirmed_keepers["player_name"].map(normalize_name))
    override_mask = audit["canonical_player_id"].isin(confirmed_norm)
    audit.loc[override_mask, "auction_eligible"] = False
    audit.loc[override_mask, "final_auction_status"] = CONFIRMED_OVERRIDE_STATUS
    audit.loc[override_mask, "eligibility_reason"] = (
        "excluded by data/keepers_2026_confirmed.csv (highest-priority tracked source for this league's "
        "real 2026 keepers/college-rights holds, overriding the general classifier's own inference for this name)"
    )
    audit.loc[override_mask, "evidence_source"] = "data/keepers_2026_confirmed.csv"

    eligible = filter_veteran_auction_pool(pool, audit, fail_on_ineligible=False).copy()
    audit_by_key = audit.set_index("canonical_player_id")
    eligible["_key"] = eligible["player"].map(data_pipeline._normalize_name)
    eligible["auction_eligible"] = True
    eligible["eligibility_status"] = eligible["_key"].map(audit_by_key["final_auction_status"])
    eligible["eligibility_reason"] = eligible["_key"].map(audit_by_key["eligibility_reason"])
    eligible["eligibility_source"] = eligible["_key"].map(audit_by_key["evidence_source"])
    eligible = eligible.drop(columns=["_key"])

    return eligible, audit
