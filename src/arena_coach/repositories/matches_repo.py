"""CRUD helpers for matches, match players, and match stats."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, Iterable, Optional


def create_match(
    connection: sqlite3.Connection,
    *,
    user_profile_id: Optional[int] = None,
    display_name: Optional[str] = None,
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
    sessionid: Optional[str] = None,
    sessionip: Optional[str] = None,
    match_type: Optional[str] = None,
    match_classification: Optional[str] = None,
    private_match_type: Optional[str] = None,
    map_name: Optional[str] = None,
    blue_score: Optional[int] = None,
    orange_score: Optional[int] = None,
    blue_round_wins: Optional[int] = None,
    orange_round_wins: Optional[int] = None,
    total_rounds_played: Optional[int] = None,
    round_summary: Optional[Iterable[Dict[str, Any]]] = None,
    points_carry_over: Optional[bool] = None,
    user_team: Optional[str] = None,
    result: Optional[str] = None,
    raw_log_path: Optional[str] = None,
    finalized: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO matches (
            user_profile_id, display_name, started_at, ended_at, sessionid, sessionip, match_type, match_classification, private_match_type, map_name,
            blue_score, orange_score, blue_round_wins, orange_round_wins, total_rounds_played, round_summary_json, points_carry_over,
            user_team, result, raw_log_path, finalized, metadata_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_profile_id,
            display_name,
            started_at,
            ended_at,
            sessionid,
            sessionip,
            match_type,
            match_classification,
            private_match_type,
            map_name,
            blue_score,
            orange_score,
            blue_round_wins,
            orange_round_wins,
            total_rounds_played,
            _json(list(round_summary) if round_summary is not None else None),
            _bool_int(points_carry_over),
            user_team,
            result,
            raw_log_path,
            1 if finalized else 0,
            _json(metadata),
            _now(),
        ),
    )
    return int(cursor.lastrowid)


def add_match_player(
    connection: sqlite3.Connection,
    *,
    match_id: int,
    match_alias: str,
    player_id: Optional[int] = None,
    userid: Optional[str] = None,
    playerid: Optional[str] = None,
    team: Optional[str] = None,
    is_user: bool = False,
    confirmed: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO match_players (
            match_id, player_id, match_alias, userid, playerid, team,
            is_user, confirmed, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            match_id,
            player_id,
            match_alias,
            userid,
            playerid,
            team,
            1 if is_user else 0,
            1 if confirmed else 0,
            _json(metadata),
        ),
    )
    return int(cursor.lastrowid)


def add_match_players(connection: sqlite3.Connection, match_id: int, players: Iterable[Dict[str, Any]]) -> int:
    count = 0
    for player in players:
        name = player.get("name")
        if not name:
            continue
        add_match_player(
            connection,
            match_id=match_id,
            match_alias=str(name),
            userid=_optional_str(player.get("userid")),
            playerid=_optional_str(player.get("playerid")),
            team=player.get("team"),
            metadata={"aliases": player.get("aliases", []), "teams": player.get("teams", [])},
        )
        count += 1
    return count


def add_match_player_stat(
    connection: sqlite3.Connection,
    *,
    match_id: int,
    match_alias: str,
    player_id: Optional[int] = None,
    userid: Optional[str] = None,
    playerid: Optional[str] = None,
    team: Optional[str] = None,
    stats: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    stats = stats or {}
    cursor = connection.execute(
        """
        INSERT INTO match_player_stats (
            match_id, player_id, match_alias, userid, playerid, team,
            points, goals, assists, saves, stuns, steals, shots, passes, catches,
            turnovers, interceptions, blocks, possession_time, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            match_id,
            player_id,
            match_alias,
            userid,
            playerid,
            team,
            _int(stats.get("points")),
            _int(stats.get("goals")),
            _int(stats.get("assists")),
            _int(stats.get("saves")),
            _int(stats.get("stuns")),
            _int(stats.get("steals")),
            _int(stats.get("shots_taken")),
            _int(stats.get("passes")),
            _int(stats.get("catches")),
            _int(stats.get("turnovers")),
            _int(stats.get("interceptions")),
            _int(stats.get("blocks")),
            _float(stats.get("possession_time")),
            _json(metadata),
        ),
    )
    return int(cursor.lastrowid)


def add_match_player_stats(connection: sqlite3.Connection, match_id: int, player_stats: Iterable[Dict[str, Any]]) -> int:
    count = 0
    for player in player_stats:
        name = player.get("name")
        if not name:
            continue
        add_match_player_stat(
            connection,
            match_id=match_id,
            match_alias=str(name),
            userid=_optional_str(player.get("userid")),
            playerid=_optional_str(player.get("playerid")),
            team=player.get("team"),
            stats=player.get("stats", {}),
            metadata=player.get("metadata"),
        )
        count += 1
    return count


def get_match(connection: sqlite3.Connection, match_id: int) -> Optional[sqlite3.Row]:
    return connection.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()


def list_matches(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT
                m.*,
                up.display_name AS profile_display_name
            FROM matches m
            LEFT JOIN user_profiles up ON up.id = m.user_profile_id
            ORDER BY COALESCE(m.started_at, m.created_at) DESC, m.id DESC
            """
        )
    )


def raw_log_exists(connection: sqlite3.Connection, raw_log_path: str) -> Optional[sqlite3.Row]:
    return connection.execute(
        """
        SELECT *
        FROM matches
        WHERE raw_log_path = ?
        ORDER BY id
        LIMIT 1
        """,
        (raw_log_path,),
    ).fetchone()


def list_maps(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row["map_name"])
        for row in connection.execute(
            """
            SELECT DISTINCT map_name
            FROM matches
            WHERE map_name IS NOT NULL AND map_name != ''
            ORDER BY map_name
            """
        )
    ]


def get_match_players(connection: sqlite3.Connection, match_id: int) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT
                mp.*,
                p.canonical_name
            FROM match_players mp
            LEFT JOIN players p ON p.id = mp.player_id
            WHERE mp.match_id = ?
            ORDER BY
                CASE mp.team
                    WHEN 'blue' THEN 0
                    WHEN 'orange' THEN 1
                    WHEN 'spectator' THEN 2
                    ELSE 3
                END,
                lower(mp.match_alias),
                mp.id
            """,
            (match_id,),
        )
    )


def get_match_player_by_alias(
    connection: sqlite3.Connection,
    match_id: int,
    match_alias: str,
) -> Optional[sqlite3.Row]:
    return connection.execute(
        """
        SELECT
            mp.*,
            p.canonical_name
        FROM match_players mp
        LEFT JOIN players p ON p.id = mp.player_id
        WHERE mp.match_id = ? AND lower(mp.match_alias) = lower(?)
        ORDER BY mp.id
        LIMIT 1
        """,
        (match_id, match_alias),
    ).fetchone()


def get_event_counts(connection: sqlite3.Connection, match_id: int) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT event_type, COUNT(*) AS count
            FROM events
            WHERE match_id = ?
            GROUP BY event_type
            ORDER BY event_type
            """,
            (match_id,),
        )
    )


def get_events(connection: sqlite3.Connection, match_id: int, limit: Optional[int] = None) -> list[sqlite3.Row]:
    sql = """
        SELECT *
        FROM events
        WHERE match_id = ?
        ORDER BY COALESCE(sequence, 0), id
    """
    params: tuple[Any, ...]
    if limit is not None:
        sql += " LIMIT ?"
        params = (match_id, limit)
    else:
        params = (match_id,)
    return list(connection.execute(sql, params))


def get_match_player_stats(connection: sqlite3.Connection, match_id: int) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT
                mps.*,
                p.canonical_name
            FROM match_player_stats mps
            LEFT JOIN players p ON p.id = mps.player_id
            WHERE mps.match_id = ?
            ORDER BY
                CASE mps.team
                    WHEN 'blue' THEN 0
                    WHEN 'orange' THEN 1
                    WHEN 'spectator' THEN 2
                    ELSE 3
                END,
                lower(mps.match_alias),
                mps.id
            """,
            (match_id,),
        )
    )


def merge_match_player_stat_metadata(
    connection: sqlite3.Connection,
    match_id: int,
    match_alias: str,
    metadata: Dict[str, Any],
) -> int:
    rows = connection.execute(
        """
        SELECT id, metadata_json
        FROM match_player_stats
        WHERE match_id = ? AND lower(match_alias) = lower(?)
        """,
        (match_id, match_alias),
    ).fetchall()
    updated = 0
    for row in rows:
        existing = _json_load(row["metadata_json"])
        existing.update(metadata)
        connection.execute(
            """
            UPDATE match_player_stats
            SET metadata_json = ?
            WHERE id = ?
            """,
            (_json(existing), row["id"]),
        )
        updated += 1
    return updated


def map_match_player(
    connection: sqlite3.Connection,
    match_id: int,
    match_alias: str,
    player_id: int,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE match_players
        SET player_id = ?, confirmed = 1
        WHERE match_id = ? AND lower(match_alias) = lower(?)
        """,
        (player_id, match_id, match_alias),
    )
    return cursor.rowcount > 0


def confirm_guest(connection: sqlite3.Connection, match_id: int, match_alias: str) -> bool:
    cursor = connection.execute(
        """
        UPDATE match_players
        SET player_id = NULL, confirmed = 1, is_user = 0
        WHERE match_id = ? AND lower(match_alias) = lower(?)
        """,
        (match_id, match_alias),
    )
    return cursor.rowcount > 0


def mark_self(connection: sqlite3.Connection, match_id: int, match_alias: str) -> bool:
    if get_match_player_by_alias(connection, match_id, match_alias) is None:
        return False
    connection.execute("UPDATE match_players SET is_user = 0 WHERE match_id = ?", (match_id,))
    connection.execute(
        """
        UPDATE match_players
        SET is_user = 1, confirmed = 1
        WHERE match_id = ? AND lower(match_alias) = lower(?)
        """,
        (match_id, match_alias),
    )
    return True


def set_team(connection: sqlite3.Connection, match_id: int, match_alias: str, team: str) -> bool:
    cursor = connection.execute(
        """
        UPDATE match_players
        SET team = ?
        WHERE match_id = ? AND lower(match_alias) = lower(?)
        """,
        (team, match_id, match_alias),
    )
    stat_rows = connection.execute(
        """
        SELECT id, metadata_json, points, goals, assists, saves, stuns, steals, shots, passes, catches, turnovers, interceptions, blocks, possession_time
        FROM match_player_stats
        WHERE match_id = ? AND lower(match_alias) = lower(?)
        ORDER BY id
        """,
        (match_id, match_alias),
    ).fetchall()
    if len(stat_rows) <= 1:
        connection.execute(
            """
            UPDATE match_player_stats
            SET team = ?
            WHERE match_id = ? AND lower(match_alias) = lower(?)
            """,
            (team, match_id, match_alias),
        )
    else:
        for row in stat_rows:
            metadata = _json_load(row["metadata_json"])
            active_participation = bool(metadata.get("active_participation"))
            stat_total = sum(
                float(row[key] or 0)
                for key in (
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
            )
            if stat_total > 0 or active_participation:
                continue
            connection.execute(
                """
                UPDATE match_player_stats
                SET team = ?
                WHERE id = ?
                """,
                (team, row["id"]),
            )
    return cursor.rowcount > 0


def update_match_player_stats_player_ids(connection: sqlite3.Connection, match_id: int) -> int:
    cursor = connection.execute(
        """
        UPDATE match_player_stats
        SET player_id = (
            SELECT mp.player_id
            FROM match_players mp
            WHERE
                mp.match_id = match_player_stats.match_id
                AND lower(mp.match_alias) = lower(match_player_stats.match_alias)
                AND mp.confirmed = 1
                AND mp.player_id IS NOT NULL
            ORDER BY mp.id
            LIMIT 1
        )
        WHERE
            match_id = ?
            AND EXISTS (
                SELECT 1
                FROM match_players mp
                WHERE
                    mp.match_id = match_player_stats.match_id
                    AND lower(mp.match_alias) = lower(match_player_stats.match_alias)
                    AND mp.confirmed = 1
                    AND mp.player_id IS NOT NULL
            )
        """,
        (match_id,),
    )
    return cursor.rowcount


def finalize_match(
    connection: sqlite3.Connection,
    match_id: int,
    *,
    user_profile_id: int,
    user_team: Optional[str],
    result: Optional[str],
    display_name: Optional[str] = None,
    private_match_type: Optional[str] = None,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE matches
        SET
            user_profile_id = ?,
            user_team = ?,
            result = ?,
            display_name = COALESCE(?, display_name),
            private_match_type = COALESCE(?, private_match_type),
            finalized = 1
        WHERE id = ?
        """,
        (user_profile_id, user_team, result, display_name, private_match_type, match_id),
    )
    return cursor.rowcount > 0


def update_match_display(
    connection: sqlite3.Connection,
    match_id: int,
    *,
    display_name: Optional[str] = None,
    match_classification: Optional[str] = None,
    private_match_type: Optional[str] = None,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE matches
        SET
            display_name = COALESCE(?, display_name),
            match_classification = COALESCE(?, match_classification),
            private_match_type = COALESCE(?, private_match_type)
        WHERE id = ?
        """,
        (display_name, match_classification, private_match_type, match_id),
    )
    return cursor.rowcount > 0


def update_match_context(
    connection: sqlite3.Connection,
    match_id: int,
    *,
    display_name: Optional[str] = None,
    match_classification: Optional[str] = None,
    private_match_type: Optional[str] = None,
    blue_round_wins: Optional[int] = None,
    orange_round_wins: Optional[int] = None,
    total_rounds_played: Optional[int] = None,
    round_summary: Optional[Iterable[Dict[str, Any]]] = None,
    points_carry_over: Optional[bool] = None,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE matches
        SET
            display_name = COALESCE(?, display_name),
            match_classification = COALESCE(?, match_classification),
            private_match_type = COALESCE(?, private_match_type),
            blue_round_wins = COALESCE(?, blue_round_wins),
            orange_round_wins = COALESCE(?, orange_round_wins),
            total_rounds_played = COALESCE(?, total_rounds_played),
            round_summary_json = COALESCE(?, round_summary_json),
            points_carry_over = COALESCE(?, points_carry_over)
        WHERE id = ?
        """,
        (
            display_name,
            match_classification,
            private_match_type,
            blue_round_wins,
            orange_round_wins,
            total_rounds_played,
            _json(list(round_summary) if round_summary is not None else None),
            _bool_int(points_carry_over),
            match_id,
        ),
    )
    return cursor.rowcount > 0


def set_private_match_type(connection: sqlite3.Connection, match_id: int, private_match_type: Optional[str]) -> bool:
    cursor = connection.execute(
        """
        UPDATE matches
        SET private_match_type = ?
        WHERE id = ?
        """,
        (private_match_type, match_id),
    )
    return cursor.rowcount > 0


def _json(value: Optional[Dict[str, Any]]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def _json_load(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bool_int(value: Optional[bool]) -> Optional[int]:
    if value is None:
        return None
    return 1 if value else 0
