"""CRUD helpers for normalized events."""

from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from arena_coach.parsing.normalized_event import NormalizedEvent


def add_event(connection: sqlite3.Connection, match_id: int, event: NormalizedEvent) -> int:
    cursor = connection.execute(
        """
        INSERT INTO events (
            match_id, sequence, captured_at, game_clock, game_clock_display, event_type,
            actor_player_id, target_player_id, assist_player_id,
            actor_alias, target_alias, assist_alias,
            actor_userid, target_userid, assist_userid,
            actor_playerid, target_playerid, assist_playerid,
            team, value, raw_text, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            match_id,
            event.sequence,
            event.captured_at,
            event.game_clock,
            event.game_clock_display,
            event.event_type,
            None,
            None,
            None,
            event.actor_name,
            event.target_name,
            event.assist_name,
            event.actor_userid,
            event.target_userid,
            event.assist_userid,
            event.actor_playerid,
            event.target_playerid,
            event.assist_playerid,
            event.team,
            event.value,
            event.raw_text,
            json.dumps(event.metadata, sort_keys=True),
        ),
    )
    return int(cursor.lastrowid)


def add_events(connection: sqlite3.Connection, match_id: int, events: Iterable[NormalizedEvent]) -> int:
    count = 0
    for event in events:
        event.match_id = match_id
        event.event_id = add_event(connection, match_id, event)
        count += 1
    return count


def update_event_player_ids(connection: sqlite3.Connection, match_id: int) -> int:
    updated = 0
    updated += _update_event_role(connection, match_id, "actor")
    updated += _update_event_role(connection, match_id, "target")
    updated += _update_event_role(connection, match_id, "assist")
    return updated


def _update_event_role(connection: sqlite3.Connection, match_id: int, role: str) -> int:
    alias_column = f"{role}_alias"
    player_column = f"{role}_player_id"
    cursor = connection.execute(
        f"""
        UPDATE events
        SET {player_column} = (
            SELECT mp.player_id
            FROM match_players mp
            WHERE
                mp.match_id = events.match_id
                AND lower(mp.match_alias) = lower(events.{alias_column})
                AND mp.confirmed = 1
                AND mp.player_id IS NOT NULL
            ORDER BY mp.id
            LIMIT 1
        )
        WHERE
            match_id = ?
            AND {alias_column} IS NOT NULL
            AND EXISTS (
                SELECT 1
                FROM match_players mp
                WHERE
                    mp.match_id = events.match_id
                    AND lower(mp.match_alias) = lower(events.{alias_column})
                    AND mp.confirmed = 1
                    AND mp.player_id IS NOT NULL
            )
        """,
        (match_id,),
    )
    return cursor.rowcount
