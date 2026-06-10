"""Match browsing, review, and finalization service."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from arena_coach import match_mapping
from arena_coach.inference.advanced_inference_service import _build_frames, _infer_orientation
from arena_coach.inference.inference_config import DEFAULT_CONFIG
from arena_coach.inference.player_metrics_analysis import _goalie_alias
from arena_coach.inference.possession_chains import build_possession_chains
from arena_coach.match_context import (
    dominant_team,
    has_meaningful_participation,
    normalize_private_match_type,
    participant_identity_key,
    round_record_warning,
)
from arena_coach.database import connect_database
from arena_coach.parsing.normalized_event import NormalizedEvent
from arena_coach.parsing.raw_log_reader import read_raw_log
from arena_coach.repositories import advanced_events_repo, advanced_player_metrics_repo
from arena_coach.repositories import matches_repo, players_repo, profiles_repo
from arena_coach.services.advanced_analysis_service import (
    apply_hybrid_display_scores,
    build_match_only_category_breakdown,
    competitive_category_baselines_for_database,
)
from arena_coach.services.match_display import build_match_display_name


class MatchService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def list_matches(
        self,
        finalized: str | Iterable[str] | None = "all",
        result: str | Iterable[str] | None = "all",
        map_name: str | Iterable[str] | None = "all",
        search: str = "",
    ) -> List[Dict[str, Any]]:
        search_folded = search.casefold().strip()
        finalized_filter = _normalized_filter_values(finalized)
        result_filter = _normalized_filter_values(result)
        map_filter = _normalized_filter_values(map_name)
        with _connection(self.database_path) as connection:
            matches = [_match_dict(row) for row in matches_repo.list_matches(connection)]
        filtered: List[Dict[str, Any]] = []
        for match in matches:
            status_value = "finalized" if match["finalized"] else "unfinalized"
            if finalized_filter and status_value not in finalized_filter:
                continue
            if result_filter and str(match["result"] or "unknown") not in result_filter:
                continue
            if map_filter and str(match["map_name"] or "") not in map_filter:
                continue
            haystack = " ".join(
                str(match.get(key) or "")
                for key in ("id", "display_name", "started_at", "map_name", "match_classification", "raw_log_path")
            )
            haystack = f"{haystack} {match.get('private_match_type') or ''}"
            if search_folded and search_folded not in haystack.casefold():
                continue
            filtered.append(match)
        return filtered

    def list_maps(self) -> List[str]:
        with _connection(self.database_path) as connection:
            return matches_repo.list_maps(connection)

    def get_match_detail(self, match_id: int) -> Dict[str, Any]:
        with _connection(self.database_path) as connection:
            match = matches_repo.get_match(connection, match_id)
            if match is None:
                raise ValueError(f"Match id {match_id} does not exist.")
            match_data = _match_dict(match)
            players = [_match_player_dict(row) for row in matches_repo.get_match_players(connection, match_id)]
            stats = [_stat_dict(row) for row in matches_repo.get_match_player_stats(connection, match_id)]
            advanced_metric_rows = [
                _advanced_metric_dict(row) for row in advanced_player_metrics_repo.get_match_metrics(connection, match_id)
            ]
            advanced_rows = list(
                advanced_events_repo.get_match_advanced_events(
                    connection,
                    match_id,
                    min_confidence="low",
                    include_low_confidence=True,
                )
            )
            all_event_rows = matches_repo.get_events(connection, match_id)
            runtime_context = _build_runtime_match_context(match_data, players, all_event_rows)
            scoreboards = _scoreboards(
                stats,
                players,
                match_data,
                all_event_rows,
                advanced_rows,
                advanced_metric_rows,
                runtime_context,
                self.database_path,
            )
            return {
                "match": match_data,
                "players": players,
                "stats": stats,
                "advanced_player_metrics": advanced_metric_rows,
                "scoreboards": scoreboards,
                "event_counts": _event_counts(connection, match_id),
                "quality": _match_quality(stats, match_data),
                "events": [_event_dict(row) for row in all_event_rows[:500]],
            }

    def get_review_data(self, match_id: int) -> Dict[str, Any]:
        with _connection(self.database_path) as connection:
            match = matches_repo.get_match(connection, match_id)
            if match is None:
                raise ValueError(f"Match id {match_id} does not exist.")
            active = profiles_repo.get_active_profile(connection)
            players = []
            for row in matches_repo.get_match_players(connection, match_id):
                player = _match_player_dict(row)
                player["stats"] = _stats_for_alias(connection, match_id, row["match_alias"])
                player["suggestions"] = players_repo.suggest_players_for_alias(
                    connection,
                    row["match_alias"],
                    userid=row["userid"],
                    playerid=row["playerid"],
                )
                player["self_suggestion"] = _is_self_suggestion(active, row["match_alias"], player["suggestions"])
                players.append(player)
            return {
                "match": _match_dict(match),
                "active_profile": _profile_dict(active) if active is not None else None,
                "players": players,
                "event_counts": _event_counts(connection, match_id),
                "quality": _match_quality([player["stats"] for player in players], _match_dict(match)),
                "validation": self._validation_from_rows(match, active, players),
            }

    def list_player_options(self) -> List[Dict[str, Any]]:
        with _connection(self.database_path) as connection:
            return [_player_option(row) for row in players_repo.list_players(connection)]

    def map_player(self, match_id: int, alias: str, player_id: int) -> None:
        with _connection(self.database_path) as connection:
            with connection:
                match_mapping.map_match_alias(connection, match_id, alias, player_id)

    def create_player_from_alias(self, match_id: int, alias: str, canonical_name: str) -> int:
        with _connection(self.database_path) as connection:
            with connection:
                return match_mapping.create_player_from_alias(connection, match_id, alias, canonical_name)

    def confirm_guest(self, match_id: int, alias: str) -> None:
        with _connection(self.database_path) as connection:
            with connection:
                match_mapping.confirm_guest(connection, match_id, alias)

    def mark_self(self, match_id: int, alias: str) -> None:
        with _connection(self.database_path) as connection:
            with connection:
                match_mapping.mark_self(connection, match_id, alias)

    def set_team(self, match_id: int, alias: str, team: str) -> None:
        with _connection(self.database_path) as connection:
            with connection:
                match_mapping.set_team(connection, match_id, alias, team)

    def set_private_match_type(self, match_id: int, private_match_type: Optional[str]) -> None:
        with _connection(self.database_path) as connection:
            with connection:
                match = matches_repo.get_match(connection, match_id)
                if match is None:
                    raise ValueError(f"Match id {match_id} does not exist.")
                match_classification = str(match["match_classification"] or "").casefold()
                normalized = normalize_private_match_type(private_match_type, allow_none=(match_classification != "private"))
                if match_classification != "private":
                    normalized = None
                display_name = build_match_display_name(
                    {
                        **_match_dict(match),
                        "private_match_type": normalized,
                    }
                )
                matches_repo.update_match_context(
                    connection,
                    match_id,
                    private_match_type=normalized,
                    display_name=display_name,
                )

    def set_afk_suspected(self, match_id: int, alias: str, suspected: bool) -> None:
        with _connection(self.database_path) as connection:
            with connection:
                stats = _stats_for_alias(connection, match_id, alias)
                existing = stats.get("metadata", {}).get("afk_detection", {})
                if not isinstance(existing, dict):
                    existing = {}
                updated = dict(existing)
                updated["suspected"] = bool(suspected)
                updated["manual_review"] = True
                updated["confidence"] = 1.0 if suspected else 0.0
                reasons = list(updated.get("reasons") or [])
                reason = "manual_review_afk" if suspected else "manual_review_active"
                if reason not in reasons:
                    reasons.append(reason)
                updated["reasons"] = reasons
                updated["name"] = stats.get("match_alias") or alias
                matches_repo.merge_match_player_stat_metadata(
                    connection,
                    match_id,
                    alias,
                    {"afk_detection": updated},
                )

    def validate_finalize(self, match_id: int) -> Dict[str, Any]:
        review = self.get_review_data(match_id)
        return review["validation"]

    def finalize_match(self, match_id: int) -> Dict[str, Any]:
        validation = self.validate_finalize(match_id)
        if not validation["can_finalize"]:
            raise ValueError("; ".join(item["message"] for item in validation["items"] if not item["ok"]))
        with _connection(self.database_path) as connection:
            with connection:
                result = match_mapping.finalize_match(connection, match_id)
        return {
            "match_id": result.match_id,
            "user_profile_id": result.user_profile_id,
            "user_team": result.user_team,
            "result": result.result,
            "stats_rows_updated": result.stats_rows_updated,
            "event_roles_updated": result.event_roles_updated,
        }

    def raw_log_imported(self, raw_log_path: Path) -> Optional[Dict[str, Any]]:
        resolved = str(Path(raw_log_path).resolve())
        with _connection(self.database_path) as connection:
            row = matches_repo.raw_log_exists(connection, resolved)
            return _match_dict(row) if row is not None else None

    def process_log_for_review(self, raw_log_path: Path, import_service: Any) -> Dict[str, Any]:
        existing = self.raw_log_imported(raw_log_path)
        if existing is not None:
            return {"status": "existing", "match": existing, "match_id": existing["id"]}
        result = import_service.import_log(raw_log_path)
        return {"status": "created", "match_id": result["match_id"], "result": result}

    @staticmethod
    def _validation_from_rows(match: Any, active_profile: Any, players: List[Dict[str, Any]]) -> Dict[str, Any]:
        items = []
        items.append({"label": "active profile exists", "ok": active_profile is not None, "message": "No active profile."})
        self_players = [player for player in players if player["is_user"]]
        items.append(
            {
                "label": "exactly one self player",
                "ok": len(self_players) == 1,
                "message": "Exactly one match player must be marked self.",
            }
        )
        items.append(
            {
                "label": "self player mapped",
                "ok": len(self_players) == 1 and self_players[0]["player_id"] is not None,
                "message": "Self player must be mapped to a canonical player.",
            }
        )
        unresolved = [
            player["match_alias"]
            for player in players
            if player["team"] != "spectator" and not player["confirmed"]
        ]
        items.append(
            {
                "label": "non-spectator players confirmed",
                "ok": not unresolved,
                "message": "Unconfirmed players: " + ", ".join(unresolved),
            }
        )
        missing_teams = [player["match_alias"] for player in players if player["team"] not in {"blue", "orange", "spectator"}]
        items.append(
            {
                "label": "teams present",
                "ok": not missing_teams,
                "message": "Players missing teams: " + ", ".join(missing_teams),
            }
        )
        items.append(
            {
                "label": "match not already finalized",
                "ok": not bool(match["finalized"]),
                "message": "Match is already finalized.",
            }
        )
        impossible_self_guest = [
            player["match_alias"]
            for player in players
            if player["is_user"] and player["confirmed"] and player["player_id"] is None
        ]
        items.append(
            {
                "label": "self is not guest",
                "ok": not impossible_self_guest,
                "message": "Self player must be linked to an existing or new player: " + ", ".join(impossible_self_guest),
            }
        )
        return {"can_finalize": all(item["ok"] for item in items), "items": items}


@contextmanager
def _connection(database_path: Path):
    connection = connect_database(database_path)
    try:
        yield connection
    finally:
        connection.close()


def _match_dict(row: Any) -> Dict[str, Any]:
    data = {
        "id": int(row["id"]),
        "user_profile_id": row["user_profile_id"],
        "display_name": row["display_name"] if "display_name" in row.keys() else None,
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "sessionid": row["sessionid"],
        "sessionip": row["sessionip"],
        "match_type": row["match_type"],
        "match_classification": row["match_classification"] if "match_classification" in row.keys() else None,
        "private_match_type": row["private_match_type"] if "private_match_type" in row.keys() else None,
        "map_name": row["map_name"],
        "blue_score": row["blue_score"],
        "orange_score": row["orange_score"],
        "blue_round_wins": row["blue_round_wins"] if "blue_round_wins" in row.keys() else 0,
        "orange_round_wins": row["orange_round_wins"] if "orange_round_wins" in row.keys() else 0,
        "total_rounds_played": row["total_rounds_played"] if "total_rounds_played" in row.keys() else 0,
        "round_summary": _json_value_load(row["round_summary_json"], []) if "round_summary_json" in row.keys() else [],
        "points_carry_over": _optional_bool(row["points_carry_over"]) if "points_carry_over" in row.keys() else None,
        "user_team": row["user_team"],
        "result": row["result"],
        "raw_log_path": row["raw_log_path"],
        "finalized": bool(row["finalized"]),
        "created_at": row["created_at"],
    }
    data["round_warning"] = round_record_warning(data)
    data["display_name"] = data["display_name"] or build_match_display_name(data)
    return data


def _match_player_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "match_id": int(row["match_id"]),
        "player_id": row["player_id"],
        "canonical_name": row["canonical_name"],
        "match_alias": row["match_alias"],
        "userid": row["userid"],
        "playerid": row["playerid"],
        "team": row["team"],
        "is_user": bool(row["is_user"]),
        "confirmed": bool(row["confirmed"]),
    }


def _stat_dict(row: Any) -> Dict[str, Any]:
    metadata = _json_load(row["metadata_json"])
    afk = metadata.get("afk_detection") if isinstance(metadata, dict) else {}
    afk = afk if isinstance(afk, dict) else {}
    stats = {
        "match_alias": row["match_alias"],
        "player_id": row["player_id"],
        "canonical_name": row["canonical_name"],
        "userid": row["userid"],
        "playerid": row["playerid"],
        "team": row["team"],
        "points": row["points"],
        "goals": row["goals"],
        "assists": row["assists"],
        "saves": row["saves"],
        "stuns": row["stuns"],
        "steals": row["steals"],
        "shots": row["shots"],
        "passes": row["passes"],
        "catches": row["catches"],
        "turnovers": row["turnovers"],
        "interceptions": row["interceptions"],
        "blocks": row["blocks"],
        "possession_time": row["possession_time"],
        "metadata": metadata,
        "afk_suspected": bool(afk.get("suspected")),
        "afk_confidence": afk.get("confidence", 0.0),
        "afk_reasons": afk.get("reasons", []),
        "live_samples": afk.get("live_samples", 0),
        "activity_total": afk.get("activity_total", 0),
    }
    stats["meaningful_participation"] = has_meaningful_participation(stats, metadata)
    stats["suppressed_default"] = bool(metadata.get("suppressed_default")) or not stats["meaningful_participation"]
    stats["identity_key"] = participant_identity_key(
        player_id=stats["player_id"],
        userid=stats["userid"],
        match_alias=stats["match_alias"],
    )
    return stats


def _stats_for_alias(connection: Any, match_id: int, alias: str) -> Dict[str, Any]:
    rows = connection.execute(
        """
        SELECT mps.*, p.canonical_name
        FROM match_player_stats mps
        LEFT JOIN players p ON p.id = mps.player_id
        WHERE mps.match_id = ? AND lower(mps.match_alias) = lower(?)
        ORDER BY mps.id
        """,
        (match_id, alias),
    ).fetchall()
    stat_rows = [_stat_dict(row) for row in rows]
    return _aggregate_alias_stats(alias, stat_rows)


def _event_counts(connection: Any, match_id: int) -> Dict[str, int]:
    return {row["event_type"]: int(row["count"]) for row in matches_repo.get_event_counts(connection, match_id)}


def _event_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "sequence": row["sequence"],
        "captured_at": row["captured_at"],
        "game_clock_display": row["game_clock_display"],
        "event_type": row["event_type"],
        "actor_alias": row["actor_alias"],
        "target_alias": row["target_alias"],
        "assist_alias": row["assist_alias"],
        "team": row["team"],
        "value": row["value"],
        "raw_text": row["raw_text"],
    }


def _advanced_metric_dict(row: Any) -> Dict[str, Any]:
    metadata = _json_load(row["metadata_json"])
    offensive_transition_count = int(row["offensive_transition_count"] or 0)
    defensive_transition_count = int(row["defensive_transition_count"] or 0)
    offensive_transition_total = float(row["offensive_transition_total"] or 0.0)
    defensive_transition_total = float(row["defensive_transition_total"] or 0.0)
    return {
        "id": int(row["id"]),
        "match_id": int(row["match_id"]),
        "player_id": row["player_id"],
        "match_alias": row["match_alias"],
        "userid": row["userid"],
        "team": row["team"],
        "completed_passes": int(row["completed_passes"] or 0),
        "inferred_catches": int(row["inferred_catches"] or 0),
        "initiators": int(row["initiators"] or 0),
        "open_for_pass_samples": int(row["open_for_pass_samples"] or 0),
        "lane_blocked_samples": int(row["lane_blocked_samples"] or 0),
        "lane_blocks": int(row["lane_blocks"] or 0),
        "tight_man_coverage_samples": int(row["tight_man_coverage_samples"] or 0),
        "loose_man_coverage_samples": int(row["loose_man_coverage_samples"] or 0),
        "no_man_coverage_samples": int(row["no_man_coverage_samples"] or 0),
        "goalie_coverage_samples": int(row["goalie_coverage_samples"] or 0),
        "clear_attempts": int(row["clear_attempts"] or 0),
        "successful_clears": int(row["successful_clears"] or 0),
        "failed_clears": int(row["failed_clears"] or 0),
        "inferred_turnovers": int(row["inferred_turnovers"] or 0),
        "inferred_interceptions": int(row["inferred_interceptions"] or 0),
        "steal_takeaways": int(row["steal_takeaways"] or 0),
        "stun_takeaways": int(row["stun_takeaways"] or 0),
        "missed_shots": int(row["missed_shots"] or 0),
        "shots_saved_against": int(row["shots_saved_against"] or 0),
        "blocked_shots": int(row["blocked_shots"] or 0),
        "stuffed_shots": int(row["stuffed_shots"] or 0),
        "offensive_transition_count": offensive_transition_count,
        "offensive_transition_total": offensive_transition_total,
        "defensive_transition_count": defensive_transition_count,
        "defensive_transition_total": defensive_transition_total,
        "goals_2_open_net": int(row["goals_2_open_net"] or 0),
        "goals_2_guarded": int(row["goals_2_guarded"] or 0),
        "goals_3_open_net": int(row["goals_3_open_net"] or 0),
        "goals_3_guarded": int(row["goals_3_guarded"] or 0),
        "metadata": metadata,
        "average_time_to_offense": metadata.get("average_time_to_offense")
        if metadata.get("average_time_to_offense") is not None
        else (round(offensive_transition_total / offensive_transition_count, 3) if offensive_transition_count else None),
        "average_time_to_defense": metadata.get("average_time_to_defense")
        if metadata.get("average_time_to_defense") is not None
        else (round(defensive_transition_total / defensive_transition_count, 3) if defensive_transition_count else None),
        "open_for_pass_rate": metadata.get("open_for_pass_rate"),
        "shooting_percentage": metadata.get("shooting_percentage"),
        "active_seconds_observed": metadata.get("active_seconds_observed"),
        "inactive_seconds_observed": metadata.get("inactive_seconds_observed"),
        "active_rounds_estimated": metadata.get("active_rounds_estimated"),
        "round_length_seconds_estimated": metadata.get("round_length_seconds_estimated"),
        "movement_distance_observed": metadata.get("movement_distance_observed"),
        "active_signal_samples": metadata.get("active_signal_samples"),
    }


def _profile_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "display_name": row["display_name"],
        "primary_echo_name": row["primary_echo_name"],
    }


def _normalized_filter_values(value: str | Iterable[str] | None) -> Optional[set[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text or text.casefold() == "all":
            return None
        return {text}
    normalized = {str(item).strip() for item in value if str(item).strip()}
    if not normalized or "all" in {item.casefold() for item in normalized}:
        return None
    return normalized


def _is_self_suggestion(active_profile: Any, match_alias: str, suggestions: List[Dict[str, Any]]) -> bool:
    if active_profile is None:
        return False
    echo_name = str(active_profile["primary_echo_name"] or "").strip().casefold()
    display_name = str(active_profile["display_name"] or "").strip().casefold()
    known_names = {name for name in (echo_name, display_name) if name}
    if str(match_alias or "").strip().casefold() in known_names:
        return True
    for suggestion in suggestions:
        suggestion_names = {
            str(suggestion.get("canonical_name") or "").strip().casefold(),
            str(suggestion.get("alias_name") or "").strip().casefold(),
        }
        if known_names.intersection(name for name in suggestion_names if name):
            return True
    return False


def _player_option(row: Any) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "canonical_name": row["canonical_name"],
        "label": row["canonical_name"],
    }


def _json_load(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_value_load(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _match_quality(stats: List[Dict[str, Any]], match: Dict[str, Any]) -> Dict[str, Any]:
    active = set()
    suspected = set()
    team_switch_aliases = []
    by_identity: Dict[str, List[Dict[str, Any]]] = {}
    for stat in stats:
        by_identity.setdefault(str(stat.get("identity_key")), []).append(stat)
    for identity, rows in by_identity.items():
        first_name = rows[0].get("canonical_name") or rows[0].get("match_alias")
        if any(row.get("afk_suspected") for row in rows):
            suspected.add(first_name)
            continue
        if any(row.get("meaningful_participation") for row in rows):
            active.add(first_name)
    warning = None
    if len(active) < 6:
        warning = "This match has fewer than 6 active non-AFK players and may be excluded from future competitive stats."
    for rows in by_identity.values():
        meaningful_teams = {row.get("team") for row in rows if row.get("meaningful_participation")}
        if len(meaningful_teams) > 1:
            team_switch_aliases.append(rows[0].get("canonical_name") or rows[0].get("match_alias"))
    return {
        "active_non_afk_count": len(active),
        "active_non_afk_players": sorted(active),
        "suspected_afk_players": sorted(suspected),
        "warning": warning,
        "team_switch_aliases": sorted(set(filter(None, team_switch_aliases))),
        "round_warning": match.get("round_warning"),
    }


def _aggregate_alias_stats(alias: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}
    base = dict(rows[0])
    numeric_keys = (
        "points",
        "goals",
        "assists",
        "saves",
        "stuns",
        "steals",
        "shots",
        "passes",
        "catches",
        "turnovers",
        "interceptions",
        "blocks",
        "possession_time",
    )
    for key in numeric_keys:
        base[key] = sum(float(row.get(key) or 0) for row in rows)
        if key != "possession_time":
            base[key] = int(base[key])
    base["team_rows"] = rows
    base["observed_teams"] = sorted({row.get("team") for row in rows if row.get("team")})
    base["team"] = dominant_team(
        [
            {"team": row.get("team"), "stats": row}
            for row in rows
        ]
    ) or base.get("team")
    base["meaningful_participation"] = any(row.get("meaningful_participation") for row in rows)
    base["afk_suspected"] = any(row.get("afk_suspected") for row in rows)
    base["suppressed_default"] = all(row.get("suppressed_default") for row in rows)
    base["match_alias"] = alias
    return base


def _scoreboards(
    stats: List[Dict[str, Any]],
    players: List[Dict[str, Any]],
    match: Dict[str, Any],
    event_rows: List[Any],
    advanced_rows: List[Any],
    advanced_metric_rows: List[Dict[str, Any]],
    runtime_context: Optional[Dict[str, Any]],
    database_path: Path,
) -> Dict[str, Any]:
    alias_map = {str(player["match_alias"]).casefold(): player for player in players}
    advanced_by_row = _advanced_stats_by_row(stats, advanced_rows, advanced_metric_rows)
    detail_tooltips = _scoreboard_detail_tooltips(stats, event_rows, advanced_rows, advanced_by_row, runtime_context)
    category_baselines = competitive_category_baselines_for_database(database_path)
    result = {
        "blue": [],
        "orange": [],
        "spectator": [],
        "round_details": _round_details_by_team(match),
        "header_totals": _header_totals_by_team(match),
    }
    scored_entries: list[dict[str, Any]] = []
    for stat in stats:
        team = stat.get("team")
        if team not in result:
            continue
        player = alias_map.get(str(stat["match_alias"]).casefold(), {})
        entry = dict(stat)
        row_key = _scoreboard_row_key(stat)
        advanced_stats = dict(advanced_by_row.get(row_key, _empty_advanced_stats()))
        advanced_stats["detail_tooltips"] = detail_tooltips.get(row_key, {})
        advanced_stats["category_breakdown"] = build_match_only_category_breakdown(
            {
                **advanced_stats,
                "match_id": match["id"],
                "player_id": entry.get("player_id"),
                "match_alias": entry.get("match_alias"),
                "userid": entry.get("userid"),
                "team": entry.get("team"),
                "metadata_json": json.dumps(advanced_stats.get("metric_metadata") or {}),
                "total_rounds_played": match.get("total_rounds_played") or 1,
            },
            {
                **entry,
                "metadata_json": json.dumps(entry.get("metadata") or {}),
                "total_rounds_played": match.get("total_rounds_played") or 1,
            },
            total_rounds=float(match.get("total_rounds_played") or 1),
            baselines=category_baselines,
        )
        entry["advanced_stats"] = advanced_stats
        entry["display_name"] = stat.get("canonical_name") or player.get("canonical_name") or stat["match_alias"]
        result[team].append(entry)
        if team in {"blue", "orange"} and advanced_stats.get("category_breakdown"):
            scored_entries.append(entry)

    reference_breakdowns = [
        entry["advanced_stats"]["category_breakdown"]
        for entry in scored_entries
        if entry.get("meaningful_participation") or not entry.get("suppressed_default")
    ]
    for entry in scored_entries:
        advanced_stats = entry.get("advanced_stats") or {}
        display_payload = apply_hybrid_display_scores(
            advanced_stats.get("category_breakdown") or {},
            reference_breakdowns,
            context_label="this match",
        )
        advanced_stats["category_breakdown"] = display_payload["category_breakdown"]
        advanced_stats["absolute_overall_score"] = display_payload["absolute_overall_score"]
        advanced_stats["display_overall_score"] = display_payload["display_overall_score"]
        advanced_stats["display_percentile"] = display_payload["display_percentile"]
        advanced_stats["display_context"] = display_payload["display_context"]
        advanced_stats["display_reference_count"] = display_payload["display_reference_count"]
        advanced_stats["display_absolute_weight"] = display_payload["display_absolute_weight"]
        advanced_stats["display_percentile_weight"] = display_payload["display_percentile_weight"]

    for team_name in ("blue", "orange", "spectator"):
        team_rows = result[team_name]
        team_rows.sort(
            key=lambda row: (
                0 if row.get("meaningful_participation") else 1,
                -int(row.get("points") or 0),
                -int(row.get("assists") or 0),
                row.get("display_name", "").casefold(),
            )
        )
    return result


def _advanced_stats_by_row(
    stats: List[Dict[str, Any]],
    advanced_rows: List[Any],
    advanced_metric_rows: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    rows_by_player_and_team: Dict[tuple[int, str], str] = {}
    rows_by_userid_and_team: Dict[tuple[str, str], str] = {}
    rows_by_alias_and_team: Dict[tuple[str, str], str] = {}
    result = {
        _scoreboard_row_key(stat): _empty_advanced_stats(goals=int(stat.get("goals") or 0))
        for stat in stats
    }
    for stat in stats:
        row_key = _scoreboard_row_key(stat)
        team = str(stat.get("team") or "").casefold()
        player_id = stat.get("player_id")
        if player_id is not None and team:
            rows_by_player_and_team[(int(player_id), team)] = row_key
        userid = str(stat.get("userid") or "").strip().casefold()
        if userid and team:
            rows_by_userid_and_team[(userid, team)] = row_key
        alias = str(stat.get("match_alias") or "").casefold()
        if alias and team:
            rows_by_alias_and_team[(alias, team)] = row_key

    seen_turnover_events: set[tuple[Any, ...]] = set()
    for row in advanced_rows:
        event_type = str(row["event_type"] or "")
        team = str(row["team"] or "").casefold()
        actor_row_key = None
        actor_player_id = row["actor_player_id"]
        if actor_player_id is not None and team:
            actor_row_key = rows_by_player_and_team.get((int(actor_player_id), team))
        actor_userid = str(row["actor_userid"] or "").strip().casefold() if "actor_userid" in row.keys() else ""
        if actor_row_key is None and actor_userid and team:
            actor_row_key = rows_by_userid_and_team.get((actor_userid, team))
        if actor_row_key is None and row["actor_alias"] and team:
            actor_row_key = rows_by_alias_and_team.get((str(row["actor_alias"]).casefold(), team))

        target_row_key = None
        target_team = _opponent_team(team)
        target_player_id = row["target_player_id"]
        if target_player_id is not None and target_team:
            target_row_key = rows_by_player_and_team.get((int(target_player_id), target_team))
        target_userid = str(row["target_userid"] or "").strip().casefold() if "target_userid" in row.keys() else ""
        if target_row_key is None and target_userid and target_team:
            target_row_key = rows_by_userid_and_team.get((target_userid, target_team))
        if target_row_key is None and row["target_alias"] and target_team:
            target_row_key = rows_by_alias_and_team.get((str(row["target_alias"]).casefold(), target_team))

        stats_row = result.get(actor_row_key) if actor_row_key is not None else None
        if event_type == "clear":
            if stats_row is not None:
                stats_row["clears"] += 1
        elif event_type == "missed_shot":
            if stats_row is not None:
                stats_row["missed_shots"] += 1
        elif event_type == "shot_saved":
            if stats_row is not None:
                stats_row["shots_saved"] += 1
        elif event_type in {"turnover", "intercepted_pass"}:
            turnover_key = (
                int(row["match_id"]),
                row["start_sequence"],
                row["end_sequence"],
                str(row["actor_alias"] or "").casefold(),
                str(row["target_alias"] or "").casefold(),
                team,
            )
            if turnover_key in seen_turnover_events:
                continue
            seen_turnover_events.add(turnover_key)
            if stats_row is not None:
                stats_row["turnovers"] += 1
            target_stats = result.get(target_row_key) if target_row_key is not None else None
            if target_stats is not None:
                target_stats["interceptions"] += 1
        elif event_type == "offensive_transition_time" and row["value"] is not None:
            if stats_row is not None:
                stats_row["offense_values"].append(float(row["value"]))
        elif event_type == "defensive_transition_time" and row["value"] is not None:
            if stats_row is not None:
                stats_row["defense_values"].append(float(row["value"]))

    for stats_row in result.values():
        offense_values = stats_row["offense_values"]
        defense_values = stats_row["defense_values"]
        stats_row["avg_time_to_offense"] = (
            round(sum(offense_values) / len(offense_values), 2) if offense_values else None
        )
        stats_row["avg_time_to_defense"] = (
            round(sum(defense_values) / len(defense_values), 2) if defense_values else None
        )
        denominator = int(stats_row["missed_shots"]) + int(stats_row["shots_saved"])
        stats_row["shooting_percentage"] = (
            round((float(stats_row["goals"]) / float(denominator)) * 100.0, 1)
            if denominator > 0
            else None
        )

    for metric in advanced_metric_rows:
        row_key = _advanced_metric_row_key(metric, rows_by_player_and_team, rows_by_userid_and_team, rows_by_alias_and_team)
        if row_key is None or row_key not in result:
            continue
        stats_row = result[row_key]
        stats_row["completed_passes"] = int(metric.get("completed_passes") or 0)
        stats_row["inferred_catches"] = int(metric.get("inferred_catches") or 0)
        stats_row["initiators"] = int(metric.get("initiators") or 0)
        stats_row["open_for_pass_samples"] = int(metric.get("open_for_pass_samples") or 0)
        stats_row["lane_blocked_samples"] = int(metric.get("lane_blocked_samples") or 0)
        stats_row["lane_blocks"] = int(metric.get("lane_blocks") or 0)
        stats_row["tight_man_coverage_samples"] = int(metric.get("tight_man_coverage_samples") or 0)
        stats_row["loose_man_coverage_samples"] = int(metric.get("loose_man_coverage_samples") or 0)
        stats_row["no_man_coverage_samples"] = int(metric.get("no_man_coverage_samples") or 0)
        stats_row["goalie_coverage_samples"] = int(metric.get("goalie_coverage_samples") or 0)
        stats_row["clears"] = int(metric.get("clear_attempts") or 0)
        stats_row["successful_clears"] = int(metric.get("successful_clears") or 0)
        stats_row["failed_clears"] = int(metric.get("failed_clears") or 0)
        stats_row["turnovers"] = int(metric.get("inferred_turnovers") or 0)
        stats_row["interceptions"] = int(metric.get("inferred_interceptions") or 0)
        stats_row["steal_takeaways"] = int(metric.get("steal_takeaways") or 0)
        stats_row["stun_takeaways"] = int(metric.get("stun_takeaways") or 0)
        stats_row["missed_shots"] = int(metric.get("missed_shots") or 0)
        stats_row["shots_saved"] = int(metric.get("shots_saved_against") or 0)
        stats_row["blocked_shots"] = int(metric.get("blocked_shots") or 0)
        stats_row["stuffed_shots"] = int(metric.get("stuffed_shots") or 0)
        stats_row["goals_2_open_net"] = int(metric.get("goals_2_open_net") or 0)
        stats_row["goals_2_guarded"] = int(metric.get("goals_2_guarded") or 0)
        stats_row["goals_3_open_net"] = int(metric.get("goals_3_open_net") or 0)
        stats_row["goals_3_guarded"] = int(metric.get("goals_3_guarded") or 0)
        stats_row["avg_time_to_offense"] = metric.get("average_time_to_offense")
        stats_row["avg_time_to_defense"] = metric.get("average_time_to_defense")
        stats_row["open_for_pass_rate"] = metric.get("open_for_pass_rate")
        stats_row["shooting_percentage"] = metric.get("shooting_percentage")
        stats_row["active_seconds_observed"] = metric.get("active_seconds_observed")
        stats_row["inactive_seconds_observed"] = metric.get("inactive_seconds_observed")
        stats_row["active_rounds_estimated"] = metric.get("active_rounds_estimated")
        stats_row["round_length_seconds_estimated"] = metric.get("round_length_seconds_estimated")
        stats_row["movement_distance_observed"] = metric.get("movement_distance_observed")
        stats_row["active_signal_samples"] = int(metric.get("active_signal_samples") or 0)
        stats_row["metric_metadata"] = metric.get("metadata") or {}
    return result


def _scoreboard_detail_tooltips(
    stats: List[Dict[str, Any]],
    event_rows: List[Any],
    advanced_rows: List[Any],
    advanced_by_row: Dict[str, Dict[str, Any]],
    runtime_context: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, str]]:
    stats_by_key = {_scoreboard_row_key(stat): stat for stat in stats}
    rows_by_player_and_team, rows_by_userid_and_team, rows_by_alias_and_team = _scoreboard_lookup_maps(stats)

    assist_targets: Dict[str, Counter[str]] = {row_key: Counter() for row_key in stats_by_key}
    pass_targets: Dict[str, Counter[str]] = {row_key: Counter() for row_key in stats_by_key}
    save_targets: Dict[str, Counter[str]] = {row_key: Counter() for row_key in stats_by_key}
    inferred_save_targets: Dict[str, Counter[str]] = {row_key: Counter() for row_key in stats_by_key}
    non_save_targets: Dict[str, Counter[str]] = {row_key: Counter() for row_key in stats_by_key}
    chicago_targets: Dict[str, Counter[str]] = {row_key: Counter() for row_key in stats_by_key}
    turnover_targets: Dict[str, Counter[str]] = {row_key: Counter() for row_key in stats_by_key}
    interception_targets: Dict[str, Counter[str]] = {row_key: Counter() for row_key in stats_by_key}
    block_targets: Dict[str, Counter[str]] = {row_key: Counter() for row_key in stats_by_key}

    for row in event_rows:
        event_type = str(row["event_type"] or "").casefold()
        team = str(row["team"] or "").casefold()
        actor_row_key = _row_key_for_identity(
            team,
            row["actor_player_id"] if "actor_player_id" in row.keys() else None,
            row["actor_userid"] if "actor_userid" in row.keys() else None,
            row["actor_alias"] if "actor_alias" in row.keys() else None,
            rows_by_player_and_team,
            rows_by_userid_and_team,
            rows_by_alias_and_team,
        )
        if event_type == "assist" and actor_row_key is not None:
            target_name = _resolve_display_name(
                team,
                row["target_player_id"] if "target_player_id" in row.keys() else None,
                row["target_userid"] if "target_userid" in row.keys() else None,
                row["target_alias"] if "target_alias" in row.keys() else None,
                rows_by_player_and_team,
                rows_by_userid_and_team,
                rows_by_alias_and_team,
                stats_by_key,
            )
            if target_name:
                assist_targets[actor_row_key][target_name] += 1

    for actor_key, target_name in _completed_pass_targets(
        runtime_context or {},
        rows_by_player_and_team,
        rows_by_userid_and_team,
        rows_by_alias_and_team,
        stats_by_key,
    ):
        if actor_key in pass_targets and target_name:
            pass_targets[actor_key][target_name] += 1

    save_breakdown = _save_breakdown_details(
        runtime_context or {},
        rows_by_player_and_team,
        rows_by_userid_and_team,
        rows_by_alias_and_team,
        stats_by_key,
    )
    for row_key, counts in save_breakdown["confirmed"].items():
        save_targets[row_key].update(counts)
    for row_key, counts in save_breakdown["inferred"].items():
        inferred_save_targets[row_key].update(counts)
    for row_key, counts in save_breakdown["non_saves"].items():
        non_save_targets[row_key].update(counts)
    for row_key, counts in save_breakdown["chicagos"].items():
        chicago_targets[row_key].update(counts)

    seen_turnovers: set[tuple[Any, ...]] = set()
    for row in advanced_rows:
        event_type = str(row["event_type"] or "").casefold()
        team = str(row["team"] or "").casefold()
        actor_row_key = _row_key_for_identity(
            team,
            row["actor_player_id"] if "actor_player_id" in row.keys() else None,
            row["actor_userid"] if "actor_userid" in row.keys() else None,
            row["actor_alias"] if "actor_alias" in row.keys() else None,
            rows_by_player_and_team,
            rows_by_userid_and_team,
            rows_by_alias_and_team,
        )
        target_team = _opponent_team(team)
        target_row_key = _row_key_for_identity(
            target_team,
            row["target_player_id"] if "target_player_id" in row.keys() else None,
            row["target_userid"] if "target_userid" in row.keys() else None,
            row["target_alias"] if "target_alias" in row.keys() else None,
            rows_by_player_and_team,
            rows_by_userid_and_team,
            rows_by_alias_and_team,
        )
        if event_type in {"blocked_shot", "stuffed_shot"} and target_row_key is not None:
            shooter_name = _display_name_for_row_or_alias(actor_row_key, row["actor_alias"], stats_by_key)
            if shooter_name:
                block_targets[target_row_key][shooter_name] += 1
        elif event_type in {"turnover", "intercepted_pass"}:
            turnover_key = (
                int(row["match_id"]),
                row["start_sequence"],
                row["end_sequence"],
                str(row["actor_alias"] or "").casefold(),
                str(row["target_alias"] or "").casefold(),
                team,
            )
            if turnover_key in seen_turnovers:
                continue
            seen_turnovers.add(turnover_key)
            target_name = _display_name_for_row_or_alias(target_row_key, row["target_alias"], stats_by_key)
            actor_name = _display_name_for_row_or_alias(actor_row_key, row["actor_alias"], stats_by_key)
            if actor_row_key is not None and target_name:
                turnover_targets[actor_row_key][target_name] += 1
            if target_row_key is not None and actor_name:
                interception_targets[target_row_key][actor_name] += 1

    tooltips: Dict[str, Dict[str, str]] = {}
    for row_key, stat in stats_by_key.items():
        advanced_stats = advanced_by_row.get(row_key, _empty_advanced_stats(goals=int(stat.get("goals") or 0)))
        entries: Dict[str, str] = {}
        entries["goals"] = _goal_breakdown_tooltip(advanced_stats, int(stat.get("goals") or 0))
        entries["assists"] = _counter_tooltip(
            "Assisted",
            assist_targets.get(row_key, Counter()),
            int(stat.get("assists") or 0),
            empty_message="No assists in this match.",
        )
        entries["saves"] = _counter_tooltip(
            "Saved shots from",
            save_targets.get(row_key, Counter()),
            int(stat.get("saves") or 0),
            empty_message="No saves in this match.",
            inferred_counts=inferred_save_targets.get(row_key, Counter()),
            non_save_counts=non_save_targets.get(row_key, Counter()),
            chicago_counts=chicago_targets.get(row_key, Counter()),
            footer="Estimated save rate uses confirmed saves + inferred saves versus non-saves + chicagos.",
        )
        entries["passes"] = _counter_tooltip(
            "Completed passes to",
            pass_targets.get(row_key, Counter()),
            int(advanced_stats.get("completed_passes") or stat.get("passes") or 0),
            empty_message="No completed passes in this match.",
            footer="Pass targets are inferred from nearby catch events.",
        )
        entries["turnovers"] = _counter_tooltip(
            "Turned disc over to",
            turnover_targets.get(row_key, Counter()),
            int(advanced_stats.get("turnovers") or stat.get("turnovers") or 0),
            empty_message="No turnovers in this match.",
        )
        entries["interceptions"] = _counter_tooltip(
            "Intercepted from",
            interception_targets.get(row_key, Counter()),
            int(advanced_stats.get("interceptions") or stat.get("interceptions") or 0),
            empty_message="No interceptions in this match.",
        )
        block_total = int(advanced_stats.get("blocked_shots") or advanced_stats.get("stuffed_shots") or stat.get("blocks") or 0)
        entries["blocks"] = _counter_tooltip(
            "Blocked shots from",
            block_targets.get(row_key, Counter()),
            block_total,
            empty_message="No blocked shots in this match.",
        )
        stuns_total = int(stat.get("stuns") or 0)
        if stuns_total > 0:
            entries["stuns"] = (
                f"Stuns: {stuns_total}\n\n"
                "Detailed stun targets are not reliably available from the current Echo API event stream."
            )
        else:
            entries["stuns"] = "No stuns in this match."
        tooltips[row_key] = entries
    return tooltips


def _scoreboard_lookup_maps(
    stats: List[Dict[str, Any]],
) -> tuple[Dict[tuple[int, str], str], Dict[tuple[str, str], str], Dict[tuple[str, str], str]]:
    rows_by_player_and_team: Dict[tuple[int, str], str] = {}
    rows_by_userid_and_team: Dict[tuple[str, str], str] = {}
    rows_by_alias_and_team: Dict[tuple[str, str], str] = {}
    for stat in stats:
        row_key = _scoreboard_row_key(stat)
        team = str(stat.get("team") or "").casefold()
        player_id = stat.get("player_id")
        if player_id is not None and team:
            rows_by_player_and_team[(int(player_id), team)] = row_key
        userid = str(stat.get("userid") or "").strip().casefold()
        if userid and team:
            rows_by_userid_and_team[(userid, team)] = row_key
        alias = str(stat.get("match_alias") or "").strip().casefold()
        if alias and team:
            rows_by_alias_and_team[(alias, team)] = row_key
    return rows_by_player_and_team, rows_by_userid_and_team, rows_by_alias_and_team


def _row_key_for_identity(
    team: str,
    player_id: Any,
    userid: Any,
    alias: Any,
    rows_by_player_and_team: Dict[tuple[int, str], str],
    rows_by_userid_and_team: Dict[tuple[str, str], str],
    rows_by_alias_and_team: Dict[tuple[str, str], str],
) -> Optional[str]:
    folded_team = str(team or "").casefold()
    if player_id is not None and folded_team:
        row_key = rows_by_player_and_team.get((int(player_id), folded_team))
        if row_key is not None:
            return row_key
    folded_userid = str(userid or "").strip().casefold()
    if folded_userid and folded_team:
        row_key = rows_by_userid_and_team.get((folded_userid, folded_team))
        if row_key is not None:
            return row_key
    folded_alias = str(alias or "").strip().casefold()
    if folded_alias and folded_team:
        return rows_by_alias_and_team.get((folded_alias, folded_team))
    return None


def _resolve_display_name(
    team: str,
    player_id: Any,
    userid: Any,
    alias: Any,
    rows_by_player_and_team: Dict[tuple[int, str], str],
    rows_by_userid_and_team: Dict[tuple[str, str], str],
    rows_by_alias_and_team: Dict[tuple[str, str], str],
    stats_by_key: Dict[str, Dict[str, Any]],
) -> str:
    row_key = _row_key_for_identity(
        team,
        player_id,
        userid,
        alias,
        rows_by_player_and_team,
        rows_by_userid_and_team,
        rows_by_alias_and_team,
    )
    return _display_name_for_row_or_alias(row_key, alias, stats_by_key)


def _display_name_for_row_or_alias(
    row_key: Optional[str],
    alias: Any,
    stats_by_key: Dict[str, Dict[str, Any]],
) -> str:
    if row_key is not None and row_key in stats_by_key:
        row = stats_by_key[row_key]
        return str(row.get("canonical_name") or row.get("display_name") or row.get("match_alias") or alias or "")
    return str(alias or "")


def _completed_pass_targets(
    runtime_context: Dict[str, Any],
    rows_by_player_and_team: Dict[tuple[int, str], str],
    rows_by_userid_and_team: Dict[tuple[str, str], str],
    rows_by_alias_and_team: Dict[tuple[str, str], str],
    stats_by_key: Dict[str, Dict[str, Any]],
) -> List[tuple[str, str]]:
    completions: List[tuple[str, str]] = []
    chains = list(runtime_context.get("chains") or [])
    base_events = list(runtime_context.get("base_events") or [])
    for index, chain in enumerate(chains[:-1]):
        next_chain = chains[index + 1]
        team = str(chain.team or "").casefold()
        if not team or team != str(next_chain.team or "").casefold():
            continue
        if not chain.actor_alias or not next_chain.actor_alias:
            continue
        if str(chain.actor_alias).casefold() == str(next_chain.actor_alias).casefold():
            continue
        relevant = _events_between(base_events, chain.end_sequence, next_chain.start_sequence)
        if any(event.event_type in {"goal", "save", "shot"} for event in relevant):
            continue
        actor_row_key = _row_key_for_identity(
            team,
            chain.actor_player_id,
            chain.actor_userid,
            chain.actor_alias,
            rows_by_player_and_team,
            rows_by_userid_and_team,
            rows_by_alias_and_team,
        )
        if actor_row_key is None or not team:
            continue
        receiver_name = _resolve_display_name(
            team,
            next_chain.actor_player_id,
            next_chain.actor_userid,
            next_chain.actor_alias,
            rows_by_player_and_team,
            rows_by_userid_and_team,
            rows_by_alias_and_team,
            stats_by_key,
        )
        if receiver_name:
            completions.append((actor_row_key, receiver_name))
    return completions


def _build_runtime_match_context(
    match: Dict[str, Any],
    players: List[Dict[str, Any]],
    event_rows: List[Any],
) -> Optional[Dict[str, Any]]:
    raw_log_path = match.get("raw_log_path")
    if not raw_log_path:
        return None
    path = Path(str(raw_log_path))
    if not path.exists():
        return None
    try:
        records = read_raw_log(path).records
    except Exception:
        return None
    if not records:
        return None
    player_ids_by_alias = {
        str(player.get("match_alias") or "").casefold(): (int(player["player_id"]) if player.get("player_id") is not None else None)
        for player in players
        if player.get("match_alias")
    }
    frames = _build_frames(records, player_ids_by_alias)
    if not frames:
        return None
    base_events = [_normalized_event_from_row(row) for row in event_rows]
    return {
        "frames": frames,
        "frames_by_sequence": {int(frame.sequence): frame for frame in frames},
        "orientation": _infer_orientation(frames, DEFAULT_CONFIG),
        "chains": build_possession_chains(frames, base_events),
        "base_events": base_events,
        "config": DEFAULT_CONFIG,
    }


def _normalized_event_from_row(row: Any) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=int(row["id"]),
        match_id=int(row["match_id"]),
        sequence=row["sequence"],
        captured_at=row["captured_at"],
        game_clock=row["game_clock"],
        game_clock_display=row["game_clock_display"],
        event_type=row["event_type"],
        actor_name=row["actor_alias"],
        target_name=row["target_alias"],
        assist_name=row["assist_alias"],
        actor_userid=row["actor_userid"],
        target_userid=row["target_userid"],
        assist_userid=row["assist_userid"],
        actor_playerid=row["actor_playerid"],
        target_playerid=row["target_playerid"],
        assist_playerid=row["assist_playerid"],
        team=row["team"],
        value=row["value"],
        raw_text=row["raw_text"],
        metadata=_json_load(row["metadata_json"]),
    )


def _events_between(events: List[NormalizedEvent], start_sequence: int, end_sequence: int) -> List[NormalizedEvent]:
    return [
        event
        for event in events
        if event.sequence is not None and int(start_sequence) <= int(event.sequence) <= int(end_sequence)
    ]


def _save_breakdown_details(
    runtime_context: Dict[str, Any],
    rows_by_player_and_team: Dict[tuple[int, str], str],
    rows_by_userid_and_team: Dict[tuple[str, str], str],
    rows_by_alias_and_team: Dict[tuple[str, str], str],
    stats_by_key: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Counter[str]]]:
    result = {
        "confirmed": {row_key: Counter() for row_key in stats_by_key},
        "inferred": {row_key: Counter() for row_key in stats_by_key},
        "non_saves": {row_key: Counter() for row_key in stats_by_key},
        "chicagos": {row_key: Counter() for row_key in stats_by_key},
    }
    base_events = list(runtime_context.get("base_events") or [])
    frames_by_sequence = dict(runtime_context.get("frames_by_sequence") or {})
    orientation = runtime_context.get("orientation")
    config = runtime_context.get("config") or DEFAULT_CONFIG
    if not base_events:
        return result

    shots = [event for event in base_events if event.event_type == "shot" and event.actor_name and event.team]
    saves = [event for event in base_events if event.event_type == "save" and event.actor_name and event.team]
    used_shot_ids: set[int] = set()

    for save_event in saves:
        saver_key = _row_key_for_identity(
            str(save_event.team or "").casefold(),
            None,
            save_event.actor_userid,
            save_event.actor_name,
            rows_by_player_and_team,
            rows_by_userid_and_team,
            rows_by_alias_and_team,
        )
        if saver_key is None:
            continue
        paired = _find_recent_shot_for_save(save_event, shots, used_shot_ids, config.shot_save_window_seconds)
        shooter_name = _display_name_for_shot(paired, stats_by_key) if paired is not None else "Unknown / unattributed"
        result["confirmed"][saver_key][shooter_name] += 1
        if paired is not None and paired.event_id is not None:
            used_shot_ids.add(int(paired.event_id))

    for shot_event in shots:
        if shot_event.event_id is not None and int(shot_event.event_id) in used_shot_ids:
            continue
        defending_team = _opponent_team(shot_event.team)
        if defending_team not in {"blue", "orange"}:
            continue
        shooter_name = _display_name_for_shot(shot_event, stats_by_key)
        shot_frame = _latest_frame_at_or_before(frames_by_sequence, shot_event.sequence)
        goalie_alias = _goalie_alias(shot_frame, defending_team, orientation, config) if shot_frame is not None and orientation is not None else None
        goalie_key = _row_key_for_identity(
            defending_team,
            None,
            None,
            goalie_alias,
            rows_by_player_and_team,
            rows_by_userid_and_team,
            rows_by_alias_and_team,
        )
        save_event = _first_following_base_event(
            base_events,
            shot_event,
            {"save"},
            team=defending_team,
            within_seconds=config.shot_save_window_seconds,
        )
        goal_event = _first_following_goal(
            base_events,
            shot_event,
            team=shot_event.team,
            within_seconds=config.shot_miss_window_seconds,
        )
        if save_event is not None and goal_event is not None:
            saver_key = _row_key_for_identity(
                defending_team,
                None,
                save_event.actor_userid,
                save_event.actor_name,
                rows_by_player_and_team,
                rows_by_userid_and_team,
                rows_by_alias_and_team,
            ) or goalie_key
            if saver_key is not None:
                result["chicagos"][saver_key][shooter_name] += 1
            continue
        if save_event is not None:
            continue
        if goal_event is not None:
            if goalie_key is not None:
                result["non_saves"][goalie_key][shooter_name] += 1
            continue
        inferred_saver = _infer_save_recipient_from_shot(
            shot_event,
            runtime_context,
            defending_team,
            rows_by_player_and_team,
            rows_by_userid_and_team,
            rows_by_alias_and_team,
        )
        if inferred_saver is not None:
            result["inferred"][inferred_saver][shooter_name] += 1
    return result


def _find_recent_shot_for_save(
    save_event: NormalizedEvent,
    shots: List[NormalizedEvent],
    used_shot_ids: set[int],
    window_seconds: float,
) -> Optional[NormalizedEvent]:
    best: Optional[NormalizedEvent] = None
    best_gap: Optional[float] = None
    for shot_event in shots:
        if shot_event.team == save_event.team:
            continue
        if shot_event.event_id is not None and int(shot_event.event_id) in used_shot_ids:
            continue
        gap = _seconds_between_events(shot_event, save_event)
        if gap is None or gap > window_seconds:
            continue
        if best_gap is None or gap < best_gap:
            best = shot_event
            best_gap = gap
    return best


def _display_name_for_shot(event: Optional[NormalizedEvent], stats_by_key: Dict[str, Dict[str, Any]]) -> str:
    if event is None:
        return "Unknown / unattributed"
    for row in stats_by_key.values():
        if str(row.get("team") or "").casefold() != str(event.team or "").casefold():
            continue
        if event.actor_userid and str(row.get("userid") or "").strip() == str(event.actor_userid).strip():
            return str(row.get("canonical_name") or row.get("display_name") or row.get("match_alias") or event.actor_name or "Unknown")
        if event.actor_name and str(row.get("match_alias") or "").casefold() == str(event.actor_name).casefold():
            return str(row.get("canonical_name") or row.get("display_name") or row.get("match_alias") or event.actor_name or "Unknown")
    return str(event.actor_name or "Unknown / unattributed")


def _first_following_base_event(
    events: List[NormalizedEvent],
    start_event: NormalizedEvent,
    event_types: set[str],
    *,
    team: Optional[str] = None,
    within_seconds: float,
) -> Optional[NormalizedEvent]:
    for event in events:
        if event.sequence is None or start_event.sequence is None or int(event.sequence) < int(start_event.sequence):
            continue
        if event is start_event:
            continue
        if str(event.event_type or "").casefold() not in event_types:
            continue
        if team is not None and str(event.team or "").casefold() != str(team).casefold():
            continue
        gap = _seconds_between_events(start_event, event)
        if gap is None:
            continue
        if gap > within_seconds:
            break
        return event
    return None


def _first_following_goal(
    events: List[NormalizedEvent],
    start_event: NormalizedEvent,
    *,
    team: Optional[str],
    within_seconds: float,
) -> Optional[NormalizedEvent]:
    return _first_following_base_event(events, start_event, {"goal"}, team=team, within_seconds=within_seconds)


def _infer_save_recipient_from_shot(
    shot_event: NormalizedEvent,
    runtime_context: Dict[str, Any],
    defending_team: str,
    rows_by_player_and_team: Dict[tuple[int, str], str],
    rows_by_userid_and_team: Dict[tuple[str, str], str],
    rows_by_alias_and_team: Dict[tuple[str, str], str],
) -> Optional[str]:
    base_events = list(runtime_context.get("base_events") or [])
    chains = list(runtime_context.get("chains") or [])
    frames_by_sequence = dict(runtime_context.get("frames_by_sequence") or {})
    orientation = runtime_context.get("orientation")
    config = runtime_context.get("config") or DEFAULT_CONFIG
    next_chain = None
    for chain in chains:
        if chain.start_sequence is None or shot_event.sequence is None:
            continue
        if int(chain.start_sequence) < int(shot_event.sequence):
            continue
        if str(chain.team or "").casefold() != defending_team:
            continue
        next_chain = chain
        break
    if next_chain is None or not next_chain.actor_alias:
        return None
    gap = _seconds_between_values(shot_event.captured_at, next_chain.start_captured_at)
    if gap is None or gap > 1.75:
        return None
    goal_event = _first_following_goal(base_events, shot_event, team=shot_event.team, within_seconds=config.shot_miss_window_seconds)
    if goal_event is not None:
        return None
    shot_frame = _latest_frame_at_or_before(frames_by_sequence, shot_event.sequence)
    goalie_alias = _goalie_alias(shot_frame, defending_team, orientation, config) if shot_frame is not None and orientation is not None else None
    if goalie_alias and str(goalie_alias).casefold() != str(next_chain.actor_alias).casefold():
        return None
    return _row_key_for_identity(
        defending_team,
        next_chain.actor_player_id,
        next_chain.actor_userid,
        next_chain.actor_alias,
        rows_by_player_and_team,
        rows_by_userid_and_team,
        rows_by_alias_and_team,
    )


def _goal_breakdown_tooltip(advanced_stats: Dict[str, Any], total_goals: int) -> str:
    if total_goals <= 0:
        return "No goals in this match."
    open_2 = int(advanced_stats.get("goals_2_open_net") or 0)
    open_3 = int(advanced_stats.get("goals_3_open_net") or 0)
    guarded_3 = int(advanced_stats.get("goals_3_guarded") or 0)
    guarded_2 = int(advanced_stats.get("goals_2_guarded") or 0)
    lines = [
        f"G {total_goals}",
        "",
        f"o2 - {open_2}",
        f"o3 - {open_3}",
        f"g3 - {guarded_3}",
        f"g2 - {guarded_2}",
    ]
    classified = open_2 + open_3 + guarded_3 + guarded_2
    if classified < total_goals:
        lines.append(f"unknown / unclassified - {total_goals - classified}")
    dunk_open = int((advanced_stats.get("metric_metadata") or {}).get("dunk_like_open_2s") or 0)
    dunk_guarded = int((advanced_stats.get("metric_metadata") or {}).get("dunk_like_guarded_2s") or 0)
    if dunk_open or dunk_guarded:
        lines.extend(
            [
                "",
                f"dunk-like open 2s - {dunk_open}",
                f"dunk-like guarded 2s - {dunk_guarded}",
            ]
        )
    return "\n".join(lines)


def _counter_tooltip(
    heading: str,
    counts: Counter[str],
    total: int,
    *,
    empty_message: str,
    inferred_counts: Optional[Counter[str]] = None,
    non_save_counts: Optional[Counter[str]] = None,
    chicago_counts: Optional[Counter[str]] = None,
    footer: Optional[str] = None,
) -> str:
    extra_total = (
        sum(int(count) for count in (inferred_counts or Counter()).values())
        + sum(int(count) for count in (non_save_counts or Counter()).values())
        + sum(int(count) for count in (chicago_counts or Counter()).values())
    )
    if total <= 0 and extra_total <= 0:
        return empty_message
    lines = [f"{heading} ({total})", ""]
    detailed_total = 0
    for name, count in counts.most_common():
        detailed_total += int(count)
        lines.append(f"{name} - {int(count)}")
    if detailed_total < total:
        lines.append(f"Unknown / unattributed - {int(total - detailed_total)}")
    if len(lines) == 2:
        lines.append("Detailed target breakdown is not available for these events yet.")
    if inferred_counts:
        inferred_total = sum(int(count) for count in inferred_counts.values())
        if inferred_total:
            lines.extend(["", f"Inferred extra saves ({inferred_total})"])
            for name, count in inferred_counts.most_common():
                lines.append(f"{name} - {int(count)}")
    if non_save_counts:
        non_save_total = sum(int(count) for count in non_save_counts.values())
        if non_save_total:
            lines.extend(["", f"Non-saves / allowed goals ({non_save_total})"])
            for name, count in non_save_counts.most_common():
                lines.append(f"{name} - {int(count)}")
    if chicago_counts:
        chicago_total = sum(int(count) for count in chicago_counts.values())
        if chicago_total:
            lines.extend(["", f"Chicagos ({chicago_total})"])
            for name, count in chicago_counts.most_common():
                lines.append(f"{name} - {int(count)}")
    if inferred_counts or non_save_counts or chicago_counts:
        prevented = int(total) + sum(int(count) for count in (inferred_counts or Counter()).values())
        faced = prevented + sum(int(count) for count in (non_save_counts or Counter()).values()) + sum(
            int(count) for count in (chicago_counts or Counter()).values()
        )
        if faced > 0:
            lines.extend(["", f"Estimated save rate: {(float(prevented) / float(faced)) * 100.0:.1f}% ({prevented} prevented / {faced} faced)"])
    if footer:
        lines.extend(["", footer])
    return "\n".join(lines)


def _seconds_between_rows(left: Any, right: Any) -> Optional[float]:
    left_time = left["captured_at"] if "captured_at" in left.keys() else None
    right_time = right["captured_at"] if "captured_at" in right.keys() else None
    if not left_time or not right_time:
        return None
    try:
        return max(0.0, (datetime.fromisoformat(str(right_time)) - datetime.fromisoformat(str(left_time))).total_seconds())
    except ValueError:
        return None


def _seconds_between_events(left: NormalizedEvent, right: NormalizedEvent) -> Optional[float]:
    return _seconds_between_values(left.captured_at, right.captured_at)


def _seconds_between_values(left: Optional[str], right: Optional[str]) -> Optional[float]:
    if not left or not right:
        return None
    try:
        return max(0.0, (datetime.fromisoformat(str(right)) - datetime.fromisoformat(str(left))).total_seconds())
    except ValueError:
        return None


def _latest_frame_at_or_before(frames_by_sequence: Dict[int, Any], sequence: Optional[int]) -> Optional[Any]:
    if sequence is None or not frames_by_sequence:
        return None
    keys = [key for key in frames_by_sequence if int(key) <= int(sequence)]
    if not keys:
        return None
    return frames_by_sequence[max(keys)]


def _empty_advanced_stats(*, goals: int = 0) -> Dict[str, Any]:
    return {
        "goals": int(goals),
        "completed_passes": 0,
        "inferred_catches": 0,
        "initiators": 0,
        "open_for_pass_samples": 0,
        "lane_blocked_samples": 0,
        "lane_blocks": 0,
        "tight_man_coverage_samples": 0,
        "loose_man_coverage_samples": 0,
        "no_man_coverage_samples": 0,
        "goalie_coverage_samples": 0,
        "clears": 0,
        "successful_clears": 0,
        "failed_clears": 0,
        "missed_shots": 0,
        "shots_saved": 0,
        "turnovers": 0,
        "interceptions": 0,
        "steal_takeaways": 0,
        "stun_takeaways": 0,
        "blocked_shots": 0,
        "stuffed_shots": 0,
        "goals_2_open_net": 0,
        "goals_2_guarded": 0,
        "goals_3_open_net": 0,
        "goals_3_guarded": 0,
        "open_for_pass_rate": None,
        "shooting_percentage": None,
        "avg_time_to_offense": None,
        "avg_time_to_defense": None,
        "active_seconds_observed": None,
        "inactive_seconds_observed": None,
        "active_rounds_estimated": None,
        "round_length_seconds_estimated": None,
        "movement_distance_observed": None,
        "active_signal_samples": 0,
        "offense_values": [],
        "defense_values": [],
        "metric_metadata": {},
    }


def _advanced_metric_row_key(
    metric: Dict[str, Any],
    rows_by_player_and_team: Dict[tuple[int, str], str],
    rows_by_userid_and_team: Dict[tuple[str, str], str],
    rows_by_alias_and_team: Dict[tuple[str, str], str],
) -> Optional[str]:
    team = str(metric.get("team") or "").casefold()
    player_id = metric.get("player_id")
    if player_id is not None and team:
        row_key = rows_by_player_and_team.get((int(player_id), team))
        if row_key is not None:
            return row_key
    userid = str(metric.get("userid") or "").strip().casefold()
    if userid and team:
        row_key = rows_by_userid_and_team.get((userid, team))
        if row_key is not None:
            return row_key
    alias = str(metric.get("match_alias") or "").strip().casefold()
    if alias and team:
        return rows_by_alias_and_team.get((alias, team))
    return None


def _scoreboard_row_key(stat: Dict[str, Any]) -> str:
    team = str(stat.get("team") or "unknown").casefold()
    player_id = stat.get("player_id")
    if player_id is not None:
        return f"player:{int(player_id)}|team:{team}"
    userid = str(stat.get("userid") or "").strip().casefold()
    if userid:
        return f"userid:{userid}|team:{team}"
    alias = str(stat.get("match_alias") or "").strip().casefold()
    return f"alias:{alias}|team:{team}"


def _round_details_by_team(match: Dict[str, Any]) -> Dict[str, str]:
    details = {"blue": "", "orange": "", "spectator": ""}
    round_summary = match.get("round_summary") or []
    if not round_summary:
        return details
    blue_parts = []
    orange_parts = []
    for round_item in round_summary:
        round_number = round_item.get("round", "?")
        blue_parts.append(f"R{round_number} {int(round_item.get('blue_points') or 0)}")
        orange_parts.append(f"R{round_number} {int(round_item.get('orange_points') or 0)}")
    details["blue"] = " | ".join(blue_parts)
    details["orange"] = " | ".join(orange_parts)
    return details


def _header_totals_by_team(match: Dict[str, Any]) -> Dict[str, Any]:
    totals: Dict[str, Any] = {
        "blue": int(match.get("blue_score") or 0),
        "orange": int(match.get("orange_score") or 0),
        "spectator": "",
    }
    total_rounds = int(match.get("total_rounds_played") or 0)
    blue_round_wins = int(match.get("blue_round_wins") or 0)
    orange_round_wins = int(match.get("orange_round_wins") or 0)
    if total_rounds > 1 or (blue_round_wins + orange_round_wins) > 1:
        totals["blue"] = blue_round_wins
        totals["orange"] = orange_round_wins
    return totals


def _opponent_team(team: str) -> str:
    folded = str(team or "").casefold()
    if folded == "blue":
        return "orange"
    if folded == "orange":
        return "blue"
    return ""


def _optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return bool(int(value)) if isinstance(value, (int, bool)) else str(value).strip().casefold() in {"1", "true", "yes"}
