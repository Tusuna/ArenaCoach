"""CRUD helpers for advanced inferred events."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Iterable, Optional

from arena_coach.inference.spatial_models import AdvancedEvent


CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def delete_match_events(connection: sqlite3.Connection, match_id: int) -> int:
    cursor = connection.execute("DELETE FROM advanced_events WHERE match_id = ?", (match_id,))
    return int(cursor.rowcount)


def add_advanced_event(connection: sqlite3.Connection, match_id: int, event: AdvancedEvent) -> int:
    cursor = connection.execute(
        """
        INSERT INTO advanced_events (
            match_id, event_type, actor_player_id, target_player_id, assist_player_id,
            actor_alias, target_alias, assist_alias, team,
            start_sequence, end_sequence, start_game_clock, end_game_clock,
            confidence, confidence_score, directness, value, explanation,
            evidence_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            match_id,
            event.event_type,
            event.actor_player_id,
            event.target_player_id,
            event.assist_player_id,
            event.actor_alias,
            event.target_alias,
            event.assist_alias,
            event.team,
            event.start_sequence,
            event.end_sequence,
            event.start_game_clock,
            event.end_game_clock,
            event.confidence,
            event.confidence_score,
            event.directness,
            event.value,
            event.explanation,
            json.dumps(event.evidence, sort_keys=True),
            _now(),
        ),
    )
    event.event_id = int(cursor.lastrowid)
    event.match_id = match_id
    return event.event_id


def add_advanced_events(connection: sqlite3.Connection, match_id: int, events: Iterable[AdvancedEvent]) -> int:
    count = 0
    for event in events:
        add_advanced_event(connection, match_id, event)
        count += 1
    return count


def get_match_advanced_events(
    connection: sqlite3.Connection,
    match_id: int,
    *,
    min_confidence: str = "low",
    confidence_levels: Optional[Iterable[str]] = None,
    event_type: Optional[str] = None,
    player_id: Optional[int] = None,
    include_low_confidence: bool = True,
) -> list[sqlite3.Row]:
    rows = list(
        connection.execute(
            """
            SELECT *
            FROM advanced_events
            WHERE match_id = ?
            ORDER BY COALESCE(start_sequence, 0), id
            """,
            (match_id,),
        )
    )
    return [
        row
        for row in rows
        if _row_matches_filters(
            row,
            min_confidence=min_confidence,
            confidence_levels=confidence_levels,
            event_type=event_type,
            player_id=player_id,
            include_low_confidence=include_low_confidence,
        )
    ]


def get_player_advanced_events(
    connection: sqlite3.Connection,
    player_id: int,
    *,
    min_confidence: str = "low",
    confidence_levels: Optional[Iterable[str]] = None,
    event_type: Optional[str] = None,
    include_low_confidence: bool = True,
) -> list[sqlite3.Row]:
    rows = list(
        connection.execute(
            """
            SELECT *
            FROM advanced_events
            WHERE actor_player_id = ? OR target_player_id = ? OR assist_player_id = ?
            ORDER BY COALESCE(start_sequence, 0), id
            """,
            (player_id, player_id, player_id),
        )
    )
    return [
        row
        for row in rows
        if _row_matches_filters(
            row,
            min_confidence=min_confidence,
            confidence_levels=confidence_levels,
            event_type=event_type,
            player_id=player_id,
            include_low_confidence=include_low_confidence,
        )
    ]


def list_finalized_match_ids(connection: sqlite3.Connection) -> list[int]:
    return [
        int(row["id"])
        for row in connection.execute(
            """
            SELECT id
            FROM matches
            WHERE finalized = 1
            ORDER BY COALESCE(started_at, created_at) DESC, id DESC
            """
        )
    ]


def _row_matches_filters(
    row: sqlite3.Row,
    *,
    min_confidence: str,
    confidence_levels: Optional[Iterable[str]],
    event_type: Optional[str],
    player_id: Optional[int],
    include_low_confidence: bool,
) -> bool:
    confidence = str(row["confidence"] or "low").casefold()
    selected_levels = None if confidence_levels is None else {str(level).casefold() for level in confidence_levels}
    if selected_levels is not None:
        if confidence not in selected_levels:
            return False
    elif CONFIDENCE_ORDER.get(confidence, 0) < CONFIDENCE_ORDER.get(str(min_confidence).casefold(), 0):
        return False
    if not include_low_confidence and confidence == "low":
        return False
    if event_type and str(row["event_type"]).casefold() != str(event_type).casefold():
        return False
    if player_id is not None and player_id not in {
        row["actor_player_id"],
        row["target_player_id"],
        row["assist_player_id"],
    }:
        return False
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
