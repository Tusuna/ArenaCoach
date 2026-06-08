"""Database-backed stats service."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from arena_coach.match_context import has_meaningful_participation, participant_identity_key
from arena_coach.database import connect_database
from arena_coach.repositories import matches_repo, profiles_repo
from arena_coach.services.match_display import build_match_display_name, classify_match
from arena_coach.stats.quality_filters import classify_match_quality
from arena_coach.stats.stat_filters import StatsFilter
from arena_coach.stats.stat_models import LoadedMatch, MatchParticipant
from arena_coach.stats.stats_service import StatsEngine


class DatabaseStatsService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def preview(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        engine = self._engine()
        active_filters = filters or StatsFilter()
        payload = engine.preview(active_filters)
        filtered_matches = engine._apply_match_filters(engine.matches, active_filters)
        payload["recent_matches"] = [
            {
                "id": match.id,
                "display_name": match.display_name,
                "started_at": match.started_at or match.created_at,
                "map_name": match.map_name,
                "blue_score": match.blue_score,
                "orange_score": match.orange_score,
                "user_team": match.user_team,
                "result": match.result,
                "match_classification": match.match_classification,
                "private_match_type": match.private_match_type,
                "quality_label": match.quality.quality_label if match.quality else "Unknown",
            }
            for match in filtered_matches[:10]
        ]
        appearances: Counter[str] = Counter()
        guest_count = 0
        counted_appearances = set()
        counted_guests = set()
        for match in filtered_matches:
            for participant in match.participants:
                if (participant.team or "").casefold() == "spectator":
                    continue
                if participant.player_id is None:
                    guest_key = (match.id, participant.participant_key or participant.match_alias.casefold())
                    if guest_key not in counted_guests:
                        guest_count += 1
                        counted_guests.add(guest_key)
                    if not active_filters.include_guest_players:
                        continue
                if participant.afk_suspected and not active_filters.include_afk_players:
                    continue
                if not participant.meaningful_participation:
                    continue
                appearance_key = (match.id, participant.participant_key or participant.match_alias.casefold())
                if appearance_key in counted_appearances:
                    continue
                counted_appearances.add(appearance_key)
                appearances[participant.display_name] += 1
        payload["top_players_by_appearances"] = [
            {"name": name, "appearances": count}
            for name, count in appearances.most_common(10)
        ]
        payload["guest_unmapped_count"] = guest_count
        return payload

    def summary(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        return self._engine().profile_summary(filters or StatsFilter())

    def trends(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        return self._engine().trends(filters or StatsFilter())

    def matchups(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        conservative = (filters or StatsFilter()).with_updates(include_afk_players=False, include_low_quality=False)
        return self._engine().matchups(conservative)

    def teammates(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        conservative = (filters or StatsFilter()).with_updates(include_afk_players=False, include_low_quality=False)
        return self._engine().teammates(conservative)

    def quality(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        return self._engine().quality_summary(filters or StatsFilter(finalized_only=False))

    def player(self, player_id: int, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        return self._engine().player_summary(player_id, filters or StatsFilter())

    def playstyle(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        return self._engine().playstyle(filters or StatsFilter())

    def quality_for_match(self, match_id: int) -> Dict[str, object]:
        return self._engine().quality_for_match(match_id)

    def _engine(self) -> StatsEngine:
        with _connection(self.database_path) as connection:
            active = profiles_repo.get_active_profile(connection)
            matches = [_load_match(connection, row) for row in matches_repo.list_matches(connection)]
        return StatsEngine(matches, active_profile=_profile_dict(active) if active is not None else None)


@contextmanager
def _connection(database_path: Path):
    connection = connect_database(database_path)
    try:
        yield connection
    finally:
        connection.close()


def _load_match(connection: Any, row: Any) -> LoadedMatch:
    match = LoadedMatch(
        id=int(row["id"]),
        user_profile_id=int(row["user_profile_id"]) if row["user_profile_id"] is not None else None,
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        created_at=row["created_at"],
        display_name=row["display_name"] or build_match_display_name(
            {
                "finalized": bool(row["finalized"]),
                "match_classification": row["match_classification"] or classify_match(row["match_type"]),
                "private_match_type": row["private_match_type"] if "private_match_type" in row.keys() else None,
                "match_type": row["match_type"],
                "started_at": row["started_at"] or row["created_at"],
                "blue_score": row["blue_score"],
                "orange_score": row["orange_score"],
                "blue_round_wins": row["blue_round_wins"] if "blue_round_wins" in row.keys() else 0,
                "orange_round_wins": row["orange_round_wins"] if "orange_round_wins" in row.keys() else 0,
                "total_rounds_played": row["total_rounds_played"] if "total_rounds_played" in row.keys() else 0,
                "user_team": row["user_team"],
                "result": row["result"],
            }
        ),
        match_classification=row["match_classification"] or classify_match(row["match_type"]),
        private_match_type=row["private_match_type"] if "private_match_type" in row.keys() else None,
        match_type=row["match_type"],
        map_name=row["map_name"],
        blue_score=row["blue_score"],
        orange_score=row["orange_score"],
        blue_round_wins=int(row["blue_round_wins"] or 0) if "blue_round_wins" in row.keys() else 0,
        orange_round_wins=int(row["orange_round_wins"] or 0) if "orange_round_wins" in row.keys() else 0,
        total_rounds_played=int(row["total_rounds_played"] or 0) if "total_rounds_played" in row.keys() else 0,
        round_summary=_json_list(row["round_summary_json"]) if "round_summary_json" in row.keys() else [],
        points_carry_over=_optional_bool(row["points_carry_over"]) if "points_carry_over" in row.keys() else None,
        user_team=row["user_team"],
        result=row["result"],
        raw_log_path=row["raw_log_path"],
        finalized=bool(row["finalized"]),
        metadata=_json_load(row["metadata_json"]),
    )
    by_alias: Dict[str, Dict[str, Any]] = {}
    for participant_row in matches_repo.get_match_players(connection, match.id):
        by_alias[str(participant_row["match_alias"]).casefold()] = {
            "canonical_name": participant_row["canonical_name"],
            "player_id": participant_row["player_id"],
            "userid": participant_row["userid"],
            "team": participant_row["team"],
            "is_user": bool(participant_row["is_user"]),
            "confirmed": bool(participant_row["confirmed"]),
            "metadata": _json_load(participant_row["metadata_json"]),
        }
    participants: List[MatchParticipant] = []
    for stat_row in matches_repo.get_match_player_stats(connection, match.id):
        alias_key = str(stat_row["match_alias"]).casefold()
        alias_row = by_alias.get(alias_key, {})
        metadata = _json_load(stat_row["metadata_json"])
        afk = metadata.get("afk_detection") if isinstance(metadata, dict) else {}
        afk = afk if isinstance(afk, dict) else {}
        stats = {
            "points": int(stat_row["points"] or 0),
            "goals": int(stat_row["goals"] or 0),
            "assists": int(stat_row["assists"] or 0),
            "saves": int(stat_row["saves"] or 0),
            "stuns": int(stat_row["stuns"] or 0),
            "steals": int(stat_row["steals"] or 0),
            "shots": int(stat_row["shots"] or 0),
            "passes": int(stat_row["passes"] or 0),
            "catches": int(stat_row["catches"] or 0),
            "turnovers": int(stat_row["turnovers"] or 0),
            "interceptions": int(stat_row["interceptions"] or 0),
            "blocks": int(stat_row["blocks"] or 0),
            "possession_time": float(stat_row["possession_time"] or 0),
        }
        participant = MatchParticipant(
            match_id=match.id,
            match_alias=stat_row["match_alias"],
            canonical_name=stat_row["canonical_name"] or alias_row.get("canonical_name"),
            player_id=stat_row["player_id"] if stat_row["player_id"] is not None else alias_row.get("player_id"),
            userid=stat_row["userid"] or alias_row.get("userid"),
            team=stat_row["team"] or alias_row.get("team"),
            is_user=bool(alias_row.get("is_user")),
            confirmed=bool(alias_row.get("confirmed")),
            participant_key=participant_identity_key(
                player_id=stat_row["player_id"] if stat_row["player_id"] is not None else alias_row.get("player_id"),
                userid=stat_row["userid"] or alias_row.get("userid"),
                match_alias=stat_row["match_alias"],
            ),
            team_row_key=f"{str(stat_row['match_alias']).casefold()}|{str(stat_row['team'] or 'unknown').casefold()}",
            stats=stats,
            metadata=metadata or alias_row.get("metadata") or {},
            afk_suspected=bool(afk.get("suspected")),
            afk_confidence=float(afk.get("confidence") or 0.0),
            afk_reasons=list(afk.get("reasons") or []),
            live_samples=int(afk.get("live_samples") or 0),
            activity_total=float(afk.get("activity_total") or 0.0),
            meaningful_participation=has_meaningful_participation(stats, metadata),
        )
        participants.append(participant)
    match.participants = participants
    match.events = [_event_dict(item) for item in matches_repo.get_events(connection, match.id)]
    match.quality = classify_match_quality(match)
    return match


def _event_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "event_type": row["event_type"],
        "actor_player_id": row["actor_player_id"],
        "target_player_id": row["target_player_id"],
        "assist_player_id": row["assist_player_id"],
        "actor_alias": row["actor_alias"],
        "target_alias": row["target_alias"],
        "assist_alias": row["assist_alias"],
        "team": row["team"],
        "value": row["value"],
    }


def _profile_dict(row: Any) -> Dict[str, object]:
    return {
        "id": int(row["id"]),
        "display_name": row["display_name"],
        "primary_echo_name": row["primary_echo_name"],
    }


def _json_load(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: Optional[str]) -> List[Dict[str, Any]]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return str(value).strip().casefold() in {"true", "1", "yes"}
