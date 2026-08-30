"""College prospect tracking, debut detection, valuation, and conversion pipeline."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config, data_pipeline, valuation

HOLDINGS_COLUMNS = ["owner", "player", "position", "college", "notes"]
PROJECTION_COLUMNS = [
    "player", "projected_nfl_draft_round", "projected_nfl_draft_year",
    "projection_confidence", "projection_source", "talent_notes",
]

PLAYER_STATUSES = (
    "college",
    "nfl_drafted_not_debuted",
    "debuted_pending_conversion",
    "veteran_rostered",
    "data_conflict",
)


@dataclass
class DebutMatch:
    player: str
    nflverse_name: str
    nfl_team: str
    games_played: int
    match_method: str
    season: int


def _normalize(name: str) -> str:
    return data_pipeline._normalize_name(name)


def _name_tokens(name: str) -> tuple[str, str]:
    parts = _normalize(name).split()
    if not parts:
        return "", ""
    return parts[0], parts[-1]


def load_college_holdings(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in HOLDINGS_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df["canonical_key"] = df["player"].map(_normalize)
    return df


def load_prospect_projections(path: str | Path | None) -> pd.DataFrame:
    if path is None or not Path(path).exists():
        return pd.DataFrame(columns=PROJECTION_COLUMNS)
    df = pd.read_csv(path)
    df["canonical_key"] = df["player"].map(_normalize)
    return df


def _load_nflverse_debut_index(
    stats_path: Path,
    rosters_path: Path,
    draft_path: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build indexes for debut detection from local nflverse CSVs."""
    stats = pd.read_csv(stats_path) if stats_path.exists() else pd.DataFrame()
    rosters = pd.read_csv(rosters_path) if rosters_path.exists() else pd.DataFrame()
    draft = pd.read_csv(draft_path) if draft_path and draft_path.exists() else pd.DataFrame()

    debut_rows = []
    if not stats.empty and "player_display_name" in stats.columns:
        fantasy = stats[stats["position"].isin({"QB", "RB", "WR", "TE"})].copy()
        for _, row in fantasy.iterrows():
            games = int(row.get("games") or 0)
            if games < 1:
                continue
            debut_rows.append({
                "canonical_key": _normalize(row["player_display_name"]),
                "nflverse_name": row["player_display_name"],
                "nfl_team": row.get("recent_team", ""),
                "games_played": games,
                "season": int(row.get("season", 0)),
                "debut_type": "regular_season_game",
            })
    debut_idx = pd.DataFrame(debut_rows).drop_duplicates("canonical_key", keep="first")

    roster_rows = []
    if not rosters.empty and "full_name" in rosters.columns:
        active = rosters[rosters["status"].isin(["ACT", "RES"])].copy()
        for _, row in active.iterrows():
            pos = str(row.get("position", ""))
            if pos not in {"QB", "RB", "WR", "TE"}:
                continue
            roster_rows.append({
                "canonical_key": _normalize(row["full_name"]),
                "nflverse_name": row["full_name"],
                "nfl_team": row.get("team", ""),
                "roster_status": row.get("status", ""),
                "draft_number": row.get("draft_number"),
                "rookie_year": row.get("rookie_year"),
            })
    roster_idx = pd.DataFrame(roster_rows).drop_duplicates("canonical_key", keep="first")

    draft_rows = []
    if not draft.empty and "pfr_player_name" in draft.columns:
        for _, row in draft.iterrows():
            draft_rows.append({
                "canonical_key": _normalize(row["pfr_player_name"]),
                "nflverse_name": row["pfr_player_name"],
                "draft_season": int(row.get("season", 0)),
                "draft_round": int(row.get("round", 0)),
                "draft_pick": int(row.get("pick", 0)),
                "draft_team": row.get("team", ""),
            })
    draft_idx = pd.DataFrame(draft_rows).drop_duplicates("canonical_key", keep="first")

    return debut_idx, roster_idx, draft_idx


def match_prospect_to_nflverse(
    player: str,
    debut_idx: pd.DataFrame,
    roster_idx: pd.DataFrame,
    draft_idx: pd.DataFrame,
) -> dict:
    """Match a college prospect name to nflverse records."""
    key = _normalize(player)
    result: dict = {
        "exact_debut": None,
        "exact_roster": None,
        "exact_draft": None,
        "fuzzy_debut": None,
    }

    if not debut_idx.empty and key in set(debut_idx["canonical_key"]):
        result["exact_debut"] = debut_idx[debut_idx["canonical_key"] == key].iloc[0].to_dict()
    if not roster_idx.empty and key in set(roster_idx["canonical_key"]):
        result["exact_roster"] = roster_idx[roster_idx["canonical_key"] == key].iloc[0].to_dict()
    if not draft_idx.empty and key in set(draft_idx["canonical_key"]):
        result["exact_draft"] = draft_idx[draft_idx["canonical_key"] == key].iloc[0].to_dict()

    if result["exact_debut"] is None and not debut_idx.empty:
        first, last = _name_tokens(player)
        if first and last:
            candidates = debut_idx[
                debut_idx["canonical_key"].str.startswith(first)
                & debut_idx["canonical_key"].str.endswith(last)
            ]
            if len(candidates) == 1:
                result["fuzzy_debut"] = candidates.iloc[0].to_dict()

    return result


def _parse_manual_flags(notes: str) -> dict:
    n = str(notes or "").upper()
    return {
        "manual_debut_confirmed": "DEBUTED" in n or "ALREADY DEBUTED" in n,
        "manual_still_college": "STILL IN COLLEGE" in n or "CONFIRMED STILL" in n,
        "needs_conversion": "NEEDS CONVERSION" in n,
    }


def classify_prospect_status(
    row: pd.Series,
    nfl_match: dict,
) -> tuple[str, str, str]:
    """Return (status, status_reason, debut_evidence)."""
    flags = _parse_manual_flags(row.get("notes", ""))
    exact_debut = nfl_match.get("exact_debut") or nfl_match.get("fuzzy_debut")
    exact_roster = nfl_match.get("exact_roster")
    exact_draft = nfl_match.get("exact_draft")

    if exact_debut and int(exact_debut.get("games_played", 0)) >= 1:
        evidence = (
            f"{exact_debut['nflverse_name']}: {exact_debut['games_played']} reg-season games "
            f"({exact_debut.get('season', '?')}, {exact_debut.get('nfl_team', '?')})"
        )
        return "debuted_pending_conversion", "nflverse_regular_season_games", evidence

    if flags["manual_debut_confirmed"] and flags["manual_still_college"]:
        return "data_conflict", "notes_contradict_debut_and_college", str(row.get("notes", ""))

    if flags["manual_debut_confirmed"]:
        if exact_roster and not exact_debut:
            evidence = (
                f"Manual debut flag; nflverse roster ({exact_roster.get('nfl_team')}) "
                f"but 0 reg-season games — verify debut trigger with commissioner"
            )
            return "debuted_pending_conversion", "manual_flag_rostered_no_reg_games", evidence
        return "debuted_pending_conversion", "manual_sheet_flag", str(row.get("notes", ""))

    if exact_roster or exact_draft:
        parts = []
        if exact_draft:
            parts.append(
                f"NFL draft rd {exact_draft.get('draft_round')} "
                f"({exact_draft.get('draft_season')}, {exact_draft.get('draft_team')})"
            )
        if exact_roster:
            parts.append(f"NFL roster {exact_roster.get('nfl_team')} ({exact_roster.get('roster_status')})")
        evidence = "; ".join(parts) + " — 0 reg-season games per nflverse"
        return "nfl_drafted_not_debuted", "on_nfl_roster_or_drafted_no_reg_game", evidence

    if flags["manual_still_college"]:
        return "college", "manual_sheet_confirmed_college", str(row.get("notes", ""))

    return "college", "no_nfl_debut_detected", ""


def audit_debut_status(
    holdings: pd.DataFrame,
    nflverse_dir: Path,
) -> pd.DataFrame:
    """Audit every college holding against nflverse + manual notes."""
    stats_path = nflverse_dir / "player_stats_reg_2025.csv"
    rosters_path = nflverse_dir / "rosters_2025.csv"
    draft_path = nflverse_dir / "draft_picks.csv"
    debut_idx, roster_idx, draft_idx = _load_nflverse_debut_index(stats_path, rosters_path, draft_path)

    rows = []
    for _, row in holdings.iterrows():
        match = match_prospect_to_nflverse(row["player"], debut_idx, roster_idx, draft_idx)
        status, reason, evidence = classify_prospect_status(row, match)
        sheet_says_college = True  # entire holdings file is the college stash sheet
        stale_sheet = status in {"debuted_pending_conversion", "nfl_drafted_not_debuted", "data_conflict"}

        rows.append({
            "owner": row["owner"],
            "player": row["player"],
            "position": row["position"],
            "college": row["college"],
            "sheet_notes": row.get("notes", ""),
            "status": status,
            "status_reason": reason,
            "debut_evidence": evidence,
            "stale_sheet_flag": stale_sheet,
            "nflverse_debut_match": (match.get("exact_debut") or match.get("fuzzy_debut") or {}).get("nflverse_name", ""),
            "nflverse_games": (match.get("exact_debut") or match.get("fuzzy_debut") or {}).get("games_played"),
        })
    return pd.DataFrame(rows)


def _round_score(round_val) -> float:
    if pd.isna(round_val):
        return float(config.NFL_DRAFT_ROUND_BASE_SCORE["unknown"])
    s = str(round_val).strip().upper()
    if s in {"UDFA", "FA", "UND"}:
        return float(config.NFL_DRAFT_ROUND_BASE_SCORE["UDFA"])
    try:
        r = int(float(s))
        return float(config.NFL_DRAFT_ROUND_BASE_SCORE.get(r, config.NFL_DRAFT_ROUND_BASE_SCORE["unknown"]))
    except ValueError:
        if "1" in s and "2" in s:
            return 55.0  # e.g. "2nd-4th round"
        return float(config.NFL_DRAFT_ROUND_BASE_SCORE["unknown"])


def _confidence_factor(confidence: str) -> float:
    c = str(confidence or "medium").lower()
    if c in {"high", "confirmed"}:
        return 1.0
    if c in {"medium", "moderate"}:
        return 0.9
    return config.PROSPECT_BUST_DISCOUNT


def compute_prospect_value(row: pd.Series, audit_year: int = 2026) -> dict:
    """Debut-probability-weighted prospect value and pick equivalent."""
    pos = str(row.get("position", "WR")).upper()
    pos_mult = config.PROSPECT_POSITION_MULTIPLIER.get(pos, 1.0)
    base = _round_score(row.get("projected_nfl_draft_round"))
    conf = _confidence_factor(row.get("projection_confidence"))

    draft_year = row.get("projected_nfl_draft_year")
    years_out = 0
    if pd.notna(draft_year):
        try:
            years_out = max(0, int(draft_year) - audit_year)
        except ValueError:
            years_out = 0
    time_mult = config.PROSPECT_YEAR_DISCOUNT ** years_out

    # Debut status discount: college stash worth less if already NFL-rostered elsewhere
    status = row.get("status", "college")
    status_mult = 1.0
    if status == "nfl_drafted_not_debuted":
        status_mult = 0.15  # rights likely void or in dispute — flag in output
    elif status == "debuted_pending_conversion":
        status_mult = 0.0

    score = round(base * pos_mult * conf * time_mult * status_mult, 2)

    # Map score → college pick equivalent (1–36, or stash)
    pick_eq = _score_to_pick_equivalent(score)

    return {
        "prospect_value_score": score,
        "pick_equivalent": pick_eq,
        "pick_equivalent_numeric": pick_eq if isinstance(pick_eq, (int, float)) else 99,
    }


def _score_to_pick_equivalent(score: float) -> int | str:
    """Higher score → earlier college pick equivalent."""
    if score >= 95:
        return 1
    if score >= 85:
        return 4
    if score >= 75:
        return 8
    if score >= 65:
        return 12
    if score >= 55:
        return 18
    if score >= 45:
        return 24
    if score >= 35:
        return 30
    if score >= 25:
        return 36
    return "no_draft_list_stash"


def build_prospect_board(
    holdings: pd.DataFrame,
    audit: pd.DataFrame,
    projections: pd.DataFrame,
) -> pd.DataFrame:
    merged = holdings.merge(
        audit[["player", "status", "status_reason", "debut_evidence", "stale_sheet_flag"]],
        on="player", how="left",
    )
    if not projections.empty:
        proj = projections.drop_duplicates("canonical_key")
        merged = merged.merge(
            proj[[
                "canonical_key", "projected_nfl_draft_round", "projected_nfl_draft_year",
                "projection_confidence", "projection_source", "talent_notes",
            ]],
            on="canonical_key", how="left",
        )
    else:
        for col in PROJECTION_COLUMNS[1:]:
            merged[col] = pd.NA

    values = merged.apply(compute_prospect_value, axis=1, result_type="expand")
    board = pd.concat([merged, values], axis=1)
    board = board.sort_values(["prospect_value_score", "player"], ascending=[False, True])

    out_cols = [
        "owner", "player", "position", "college", "status",
        "projected_nfl_draft_round", "projected_nfl_draft_year",
        "prospect_value_score", "pick_equivalent",
        "projection_confidence", "projection_source", "talent_notes",
        "debut_evidence", "stale_sheet_flag", "sheet_notes",
    ]
    return board[[c for c in out_cols if c in board.columns]]


def build_college_pick_table(
    pick_ownership: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Value all 36 college draft picks in dollar-equivalent terms."""
    rows = []
    for pick_num in range(1, config.COLLEGE_DRAFT_TOTAL_PICKS + 1):
        round_num = math.ceil(pick_num / config.COLLEGE_DRAFT_PICKS_PER_ROUND)
        slot_in_round = (pick_num - 1) % config.COLLEGE_DRAFT_PICKS_PER_ROUND
        original_team = config.COLLEGE_DRAFT_ORDER[slot_in_round]
        current_owner = original_team
        if pick_ownership is not None and not pick_ownership.empty:
            match = pick_ownership[pick_ownership["pick_number"] == pick_num]
            if len(match):
                current_owner = match.iloc[0].get("current_owner", original_team)

        decay = config.COLLEGE_PICK_VALUE_DECAY ** (pick_num - 1)
        estimated_value = round(config.COLLEGE_PICK1_DOLLAR_EQUIVALENT * decay, 2)
        rows.append({
            "pick_number": pick_num,
            "round": round_num,
            "slot_in_round": slot_in_round + 1,
            "original_team": original_team,
            "current_owner": current_owner,
            "estimated_value": estimated_value,
            "value_confidence": "low",
            "value_notes": "No historical college-draft hit rate; decay model only",
        })
    return pd.DataFrame(rows)


def build_conversion_alerts(audit: pd.DataFrame, prospect_board: pd.DataFrame) -> pd.DataFrame:
    """Players who must move from college → $1 veteran pool."""
    alerts = audit[audit["status"] == "debuted_pending_conversion"].copy()
    if alerts.empty:
        return pd.DataFrame(columns=[
            "owner", "player", "position", "debut_evidence", "conversion_fee",
            "alert_severity", "commissioner_action", "audited_at",
        ])

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    alerts["conversion_fee"] = config.COLLEGE_DEBUT_FEE
    alerts["alert_severity"] = alerts["stale_sheet_flag"].map(
        lambda x: "CRITICAL" if x else "WARNING"
    )
    alerts["commissioner_action"] = (
        "Move to veteran roster at $1 salary; re-run keeper/auction valuation immediately"
    )
    alerts["audited_at"] = now

    # Attach open-market value estimate for debuted players where possible
    if not prospect_board.empty:
        val_lookup = prospect_board.set_index("player")
        alerts["sheet_still_college"] = alerts["stale_sheet_flag"]

    return alerts[[
        "owner", "player", "position", "debut_evidence", "status_reason",
        "conversion_fee", "alert_severity", "stale_sheet_flag",
        "commissioner_action", "audited_at",
    ]]


def build_master_player_table(
    holdings: pd.DataFrame,
    audit: pd.DataFrame,
    veteran_salaries: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Unified player table with college / conversion / veteran status."""
    rows = []

    for _, a in audit.iterrows():
        rows.append({
            "team": a["owner"],
            "player": a["player"],
            "position": a["position"],
            "status": a["status"],
            "college": a.get("college", ""),
            "salary_2025": config.COLLEGE_DEBUT_FEE if a["status"] == "debuted_pending_conversion" else pd.NA,
            "salary_origin": "COLLEGE_DEBUT_PENDING" if a["status"] == "debuted_pending_conversion" else "COLLEGE_STASH",
            "source": "college_holdings",
            "debut_evidence": a.get("debut_evidence", ""),
        })

    if veteran_salaries is not None and not veteran_salaries.empty:
        for _, v in veteran_salaries.iterrows():
            rows.append({
                "team": v.get("team"),
                "player": v.get("player"),
                "position": v.get("position"),
                "status": "veteran_rostered",
                "college": "",
                "salary_2025": v.get("salary_2025"),
                "salary_origin": v.get("salary_origin", "UNKNOWN"),
                "source": "historical_salaries",
                "debut_evidence": "",
            })

    return pd.DataFrame(rows)


def estimate_converted_veteran_value(
    player: str,
    position: str,
    full_pool: pd.DataFrame,
    blend_weight: float = 0.6,
    manual_points: float | None = None,
) -> float | None:
    """Neutral open-market value for a $1-converted prospect (if projections exist)."""
    key = _normalize(player)
    pool = full_pool.copy()
    pool["_key"] = pool["player"].map(_normalize)
    match = pool[pool["_key"] == key]

    if match.empty and manual_points is not None:
        inject = pd.DataFrame([{
            "player": player,
            "position": position,
            "projected_points": manual_points,
            "salary_2025": config.COLLEGE_DEBUT_FEE,
            "_key": key,
        }])
        match = inject

    if match.empty:
        return None
    priced = valuation.price_neutral_value(match.drop(columns=["_key"], errors="ignore"), blend_weight)
    if priced.empty:
        return None
    val = float(priced.iloc[0].get("hypothetical_open_market_value", 0))
    if manual_points is not None and val < 5:
        # Single-player neutral pass can't allocate league budget; use points heuristic
        val = round(float(manual_points) * 0.42, 0)
    return val
