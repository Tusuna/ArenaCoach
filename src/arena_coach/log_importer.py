"""Import parsed raw logs into SQLite."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from arena_coach.match_context import normalize_private_match_type
from arena_coach.database import connect_database, initialize_database
from arena_coach.models import ImportLogResult
from arena_coach.parsing.afk_detector import detect_afk_players
from arena_coach.parsing.event_deriver import derive_events
from arena_coach.parsing.match_context import apply_primary_teams, derive_round_context, derive_team_split_stats
from arena_coach.parsing.raw_log_reader import read_raw_log
from arena_coach.repositories import events_repo, matches_repo
from arena_coach.services.match_display import build_match_display_name, classify_match


def import_raw_log(raw_log_path: Path, database_path: Path) -> ImportLogResult:
    raw_log_path = Path(raw_log_path)
    initialize_database(database_path)

    read_result = read_raw_log(raw_log_path)
    derived = derive_events(read_result.records)
    event_counts = derived.event_counts()
    detected_players = derived.detected_player_list()
    detected_teams = derived.detected_team_list()
    afk_assessments = detect_afk_players(read_result.records)
    match_classification = _match_classification(read_result, derived)
    private_match_type = _default_private_match_type(match_classification)
    split_player_stats = derive_team_split_stats(read_result.records, afk_assessments)
    detected_players = apply_primary_teams(detected_players, split_player_stats)
    round_context = derive_round_context(read_result.records, derived.latest_blue_score, derived.latest_orange_score)
    display_name = build_match_display_name(
        {
            "finalized": False,
            "match_classification": match_classification,
            "private_match_type": private_match_type,
            "match_type": derived.detected_match_type,
            "started_at": read_result.summary.first_captured_at,
            "blue_score": derived.latest_blue_score,
            "orange_score": derived.latest_orange_score,
            "blue_round_wins": round_context.blue_round_wins,
            "orange_round_wins": round_context.orange_round_wins,
            "total_rounds_played": round_context.total_rounds_played,
        }
    )

    metadata = _match_metadata(
        read_result,
        derived,
        afk_assessments,
        match_classification,
        private_match_type,
        round_context,
    )

    connection = connect_database(database_path)
    try:
        with connection:
            match_id = matches_repo.create_match(
                connection,
                display_name=display_name,
                started_at=read_result.summary.first_captured_at,
                ended_at=read_result.summary.last_captured_at,
                sessionid=derived.detected_sessionid,
                sessionip=derived.detected_sessionip,
                match_type=derived.detected_match_type,
                match_classification=match_classification,
                private_match_type=private_match_type,
                map_name=derived.detected_map_name,
                blue_score=derived.latest_blue_score,
                orange_score=derived.latest_orange_score,
                blue_round_wins=round_context.blue_round_wins,
                orange_round_wins=round_context.orange_round_wins,
                total_rounds_played=round_context.total_rounds_played,
                round_summary=round_context.round_summary,
                points_carry_over=round_context.points_carry_over,
                raw_log_path=str(raw_log_path.resolve()),
                finalized=False,
                metadata=metadata,
            )
            match_players_saved = matches_repo.add_match_players(connection, match_id, detected_players)
            events_saved = events_repo.add_events(connection, match_id, derived.events)
            match_player_stats_saved = matches_repo.add_match_player_stats(
                connection,
                match_id,
                _stats_with_event_counts(derived, split_player_stats).values(),
            )
    finally:
        connection.close()

    return ImportLogResult(
        match_id=match_id,
        raw_log_path=str(raw_log_path.resolve()),
        detected_players=detected_players,
        detected_teams=detected_teams,
        blue_score=derived.latest_blue_score,
        orange_score=derived.latest_orange_score,
        blue_round_wins=round_context.blue_round_wins,
        orange_round_wins=round_context.orange_round_wins,
        total_rounds_played=round_context.total_rounds_played,
        points_carry_over=round_context.points_carry_over,
        event_counts=event_counts,
        finalized=False,
        events_saved=events_saved,
        match_players_saved=match_players_saved,
        match_player_stats_saved=match_player_stats_saved,
    )


def apply_afk_detection_to_match(match_id: int, raw_log_path: Path, database_path: Path) -> Dict[str, Any]:
    read_result = read_raw_log(raw_log_path)
    assessments = detect_afk_players(read_result.records)
    updated = 0
    suspected = []
    connection = connect_database(database_path)
    try:
        with connection:
            rows = matches_repo.get_match_player_stats(connection, match_id)
            by_name = _afk_by_name(assessments)
            for row in rows:
                assessment = by_name.get(str(row["match_alias"]).casefold())
                if assessment is None:
                    continue
                updated += matches_repo.merge_match_player_stat_metadata(
                    connection,
                    match_id,
                    row["match_alias"],
                    {"afk_detection": assessment},
                )
                if assessment.get("suspected"):
                    suspected.append(row["match_alias"])
    finally:
        connection.close()
    return {"match_id": match_id, "updated_stats": updated, "suspected_afk": suspected}


def _match_metadata(
    read_result: Any,
    derived: Any,
    afk_assessments: Dict[str, Dict[str, Any]],
    match_classification: str,
    private_match_type: str | None,
    round_context: Any,
) -> Dict[str, Any]:
    return {
        "raw_log_summary": {
            "total_lines": read_result.summary.total_lines,
            "valid_snapshots": read_result.summary.valid_snapshots,
            "invalid_lines": read_result.summary.invalid_lines,
            "first_captured_at": read_result.summary.first_captured_at,
            "last_captured_at": read_result.summary.last_captured_at,
        },
        "invalid_lines": [
            {"line_number": invalid.line_number, "error": invalid.error}
            for invalid in read_result.invalid_lines[:50]
        ],
        "detected_client_name": derived.detected_client_name,
        "match_classification": match_classification,
        "private_match_type": private_match_type,
        "event_counts": derived.event_counts(),
        "afk_detection": {
            "suspected_count": sum(1 for assessment in afk_assessments.values() if assessment.get("suspected")),
            "players": afk_assessments,
        },
        "round_context": round_context.to_dict(),
        "identity_mapping_status": "unresolved",
    }


def _stats_with_event_counts(derived: Any, split_player_stats: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    players = {key: dict(value) for key, value in split_player_stats.items()}
    by_name = {}
    by_name_and_team = {}
    for key, value in players.items():
        name = str(value.get("name", "")).casefold()
        team = str(value.get("team", "")).casefold()
        if name:
            by_name.setdefault(name, []).append(key)
            if team:
                by_name_and_team[(name, team)] = key
    stat_event_map = {
        "goal": "goals",
        "assist": "assists",
        "save": "saves",
        "stun": "stuns",
        "steal": "steals",
        "shot": "shots_taken",
        "pass": "passes",
        "catch": "catches",
        "interception": "interceptions",
        "block": "blocks",
    }

    event_counts: Dict[str, Dict[str, int]] = {}
    for event in derived.events:
        if not event.actor_name or event.event_type not in stat_event_map:
            continue
        actor_key = str(event.actor_name).casefold()
        player_key = by_name_and_team.get((actor_key, str(event.team or "").casefold()))
        if not player_key:
            player_keys = by_name.get(actor_key) or []
            player_key = player_keys[0] if player_keys else None
        if not player_key:
            continue
        event_counts.setdefault(player_key, {})
        stat_key = stat_event_map[event.event_type]
        increment = int(event.value or 1)
        if event.event_type in {"goal", "assist"} and event.metadata.get("source") != "player_stat_delta":
            increment = 1
        event_counts[player_key][stat_key] = event_counts[player_key].get(stat_key, 0) + increment

    for player_key, counts in event_counts.items():
        if player_key not in players:
            continue
        stats = dict(players[player_key].get("stats", {}))
        for stat_key, count in counts.items():
            stats[stat_key] = max(_int(stats.get(stat_key)), count)
        players[player_key]["stats"] = stats

    return players


def _match_classification(read_result: Any, derived: Any) -> str:
    private_match = None
    tournament_match = None
    for record in read_result.records:
        snapshot = record.snapshot
        if private_match is None and "private_match" in snapshot:
            private_match = snapshot.get("private_match")
        if tournament_match is None and "tournament_match" in snapshot:
            tournament_match = snapshot.get("tournament_match")
        if private_match is not None and tournament_match is not None:
            break
    return classify_match(derived.detected_match_type, private_match, tournament_match)


def _default_private_match_type(match_classification: str) -> str | None:
    if str(match_classification or "").casefold() == "private":
        return normalize_private_match_type("Unknown", allow_none=False)
    return None


def _afk_by_name(afk_assessments: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_name = {}
    for assessment in afk_assessments.values():
        name = assessment.get("name")
        if name:
            by_name[str(name).casefold()] = assessment
            continue
        userid = assessment.get("userid")
        # The detector keys are stable, but stats rows are currently alias-based.
        if userid:
            by_name[str(userid).casefold()] = assessment
    return by_name


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
