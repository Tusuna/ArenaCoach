"""Manual identity mapping and match finalization workflow."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Optional

from arena_coach.repositories import events_repo, matches_repo, players_repo, profiles_repo


VALID_TEAMS = {"blue", "orange", "spectator"}


class MappingError(RuntimeError):
    """Raised when a manual identity mapping command cannot be applied."""


@dataclass
class FinalizeResult:
    match_id: int
    user_profile_id: int
    user_team: Optional[str]
    result: Optional[str]
    stats_rows_updated: int
    event_roles_updated: int


def map_match_alias(
    connection: sqlite3.Connection,
    match_id: int,
    alias: str,
    player_id: int,
) -> None:
    match_player = _require_match_player(connection, match_id, alias)
    player = players_repo.get_player(connection, player_id)
    if player is None:
        raise MappingError(f"Player id {player_id} does not exist.")

    existing_userid = players_repo.find_userid(connection, match_player["userid"]) if match_player["userid"] else None
    if existing_userid is not None and int(existing_userid["player_id"]) != int(player_id):
        raise MappingError(
            f"User ID {match_player['userid']} already belongs to "
            f"#{existing_userid['player_id']} {existing_userid['canonical_name']}."
        )

    conflict = players_repo.find_alias_owned_by_other(connection, match_player["match_alias"], player_id)
    if conflict is not None:
        raise MappingError(
            "Alias is already assigned to another player: "
            f"{conflict['alias_name']} -> #{conflict['player_id']} {conflict['canonical_name']}."
        )

    players_repo.add_alias(
        connection,
        player_id,
        match_player["match_alias"],
        userid=match_player["userid"],
        playerid=None,
        confidence=1.0,
    )
    players_repo.add_userid(connection, player_id, match_player["userid"], source="match_mapping", confidence=1.0)
    if not matches_repo.map_match_player(connection, match_id, alias, player_id):
        raise MappingError(f"Could not map alias {alias}.")


def create_player_from_alias(
    connection: sqlite3.Connection,
    match_id: int,
    alias: str,
    canonical_name: str,
) -> int:
    _require_match_player(connection, match_id, alias)
    player_id = players_repo.create_player(connection, canonical_name)
    map_match_alias(connection, match_id, alias, player_id)
    return player_id


def mark_self(connection: sqlite3.Connection, match_id: int, alias: str) -> None:
    _require_match_player(connection, match_id, alias)
    if not matches_repo.mark_self(connection, match_id, alias):
        raise MappingError(f"Could not mark {alias} as self.")


def confirm_guest(connection: sqlite3.Connection, match_id: int, alias: str) -> None:
    _require_match_player(connection, match_id, alias)
    if not matches_repo.confirm_guest(connection, match_id, alias):
        raise MappingError(f"Could not confirm {alias} as guest/unmapped.")


def set_team(connection: sqlite3.Connection, match_id: int, alias: str, team: str) -> None:
    normalized_team = _normalize_team(team)
    _require_match_player(connection, match_id, alias)
    if not matches_repo.set_team(connection, match_id, alias, normalized_team):
        raise MappingError(f"Could not set team for {alias}.")


def finalize_match(connection: sqlite3.Connection, match_id: int) -> FinalizeResult:
    match = matches_repo.get_match(connection, match_id)
    if match is None:
        raise MappingError(f"Match id {match_id} does not exist.")

    active_profile = profiles_repo.get_active_profile(connection)
    if active_profile is None:
        raise MappingError("No active profile. Create one with profile create.")

    match_players = matches_repo.get_match_players(connection, match_id)
    if not match_players:
        raise MappingError("Match has no detected players to finalize.")

    missing_team = [player["match_alias"] for player in match_players if _normalize_optional_team(player["team"]) is None]
    if missing_team:
        raise MappingError("These aliases need a team before finalizing: " + ", ".join(missing_team))

    self_players = [player for player in match_players if int(player["is_user"] or 0) == 1]
    if len(self_players) != 1:
        raise MappingError("Exactly one match player must be marked self before finalizing.")

    self_player = self_players[0]
    if self_player["player_id"] is None:
        raise MappingError("The self player must be mapped to a canonical player before finalizing.")
    if self_player["userid"]:
        players_repo.add_userid(
            connection,
            int(self_player["player_id"]),
            self_player["userid"],
            source="self_finalization",
            confidence=1.0,
        )

    unresolved = [
        player["match_alias"]
        for player in match_players
        if _normalize_optional_team(player["team"]) != "spectator" and int(player["confirmed"] or 0) != 1
    ]
    if unresolved:
        raise MappingError(
            "These non-spectator aliases need mapping or guest confirmation before finalizing: "
            + ", ".join(unresolved)
        )

    user_team = _normalize_optional_team(self_player["team"])
    result = _result_for_user_team(match, user_team)
    stats_rows_updated = matches_repo.update_match_player_stats_player_ids(connection, match_id)
    event_roles_updated = events_repo.update_event_player_ids(connection, match_id)
    display_name = _display_name_for_finalized_match(match, user_team, result)
    if not matches_repo.finalize_match(
        connection,
        match_id,
        user_profile_id=int(active_profile["id"]),
        user_team=user_team,
        result=result,
        display_name=display_name,
        private_match_type=match["private_match_type"] if "private_match_type" in match.keys() else None,
    ):
        raise MappingError(f"Could not finalize match id {match_id}.")

    return FinalizeResult(
        match_id=match_id,
        user_profile_id=int(active_profile["id"]),
        user_team=user_team,
        result=result,
        stats_rows_updated=stats_rows_updated,
        event_roles_updated=event_roles_updated,
    )


def _require_match_player(connection: sqlite3.Connection, match_id: int, alias: str) -> sqlite3.Row:
    match_player = matches_repo.get_match_player_by_alias(connection, match_id, alias)
    if match_player is None:
        raise MappingError(f"Match id {match_id} has no player alias named {alias}.")
    return match_player


def _normalize_team(team: str) -> str:
    normalized = str(team or "").strip().casefold()
    if normalized not in VALID_TEAMS:
        raise MappingError("Team must be one of: blue, orange, spectator.")
    return normalized


def _normalize_optional_team(team: Optional[str]) -> Optional[str]:
    if team is None:
        return None
    normalized = str(team).strip().casefold()
    return normalized if normalized in VALID_TEAMS else None


def _result_for_user_team(match: sqlite3.Row, user_team: Optional[str]) -> Optional[str]:
    if user_team not in {"blue", "orange"}:
        return None
    blue_score = match["blue_score"]
    orange_score = match["orange_score"]
    if blue_score is None or orange_score is None:
        return None
    if blue_score == orange_score:
        return "tie"
    blue_won = int(blue_score) > int(orange_score)
    return "win" if (user_team == "blue" and blue_won) or (user_team == "orange" and not blue_won) else "loss"


def _display_name_for_finalized_match(match: sqlite3.Row, user_team: Optional[str], result: Optional[str]) -> str:
    from arena_coach.services.match_display import build_match_display_name

    return build_match_display_name(
        {
            "finalized": True,
            "match_classification": match["match_classification"],
            "private_match_type": match["private_match_type"] if "private_match_type" in match.keys() else None,
            "match_type": match["match_type"],
            "started_at": match["started_at"] or match["created_at"],
            "blue_score": match["blue_score"],
            "orange_score": match["orange_score"],
            "blue_round_wins": match["blue_round_wins"] if "blue_round_wins" in match.keys() else 0,
            "orange_round_wins": match["orange_round_wins"] if "orange_round_wins" in match.keys() else 0,
            "total_rounds_played": match["total_rounds_played"] if "total_rounds_played" in match.keys() else 0,
            "user_team": user_team,
            "result": result,
        }
    )
