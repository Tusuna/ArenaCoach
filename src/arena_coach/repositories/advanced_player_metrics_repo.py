"""CRUD helpers for persisted advanced per-player match metrics."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Iterable


def delete_match_metrics(connection: sqlite3.Connection, match_id: int) -> int:
    cursor = connection.execute("DELETE FROM advanced_player_metrics WHERE match_id = ?", (match_id,))
    return int(cursor.rowcount)


def add_metric_rows(connection: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        connection.execute(
            """
            INSERT INTO advanced_player_metrics (
                match_id, player_id, match_alias, userid, team,
                completed_passes, inferred_catches, initiators,
                open_for_pass_samples, lane_blocked_samples, lane_blocks,
                tight_man_coverage_samples, loose_man_coverage_samples, no_man_coverage_samples, goalie_coverage_samples,
                clear_attempts, successful_clears, failed_clears,
                inferred_turnovers, inferred_interceptions, steal_takeaways, stun_takeaways,
                missed_shots, shots_saved_against, blocked_shots, stuffed_shots,
                offensive_transition_count, offensive_transition_total,
                defensive_transition_count, defensive_transition_total,
                goals_2_open_net, goals_2_guarded, goals_3_open_net, goals_3_guarded,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(row["match_id"]),
                row.get("player_id"),
                row["match_alias"],
                row.get("userid"),
                row.get("team"),
                _int(row.get("completed_passes")),
                _int(row.get("inferred_catches")),
                _int(row.get("initiators")),
                _int(row.get("open_for_pass_samples")),
                _int(row.get("lane_blocked_samples")),
                _int(row.get("lane_blocks")),
                _int(row.get("tight_man_coverage_samples")),
                _int(row.get("loose_man_coverage_samples")),
                _int(row.get("no_man_coverage_samples")),
                _int(row.get("goalie_coverage_samples")),
                _int(row.get("clear_attempts")),
                _int(row.get("successful_clears")),
                _int(row.get("failed_clears")),
                _int(row.get("inferred_turnovers")),
                _int(row.get("inferred_interceptions")),
                _int(row.get("steal_takeaways")),
                _int(row.get("stun_takeaways")),
                _int(row.get("missed_shots")),
                _int(row.get("shots_saved_against")),
                _int(row.get("blocked_shots")),
                _int(row.get("stuffed_shots")),
                _int(row.get("offensive_transition_count")),
                _float(row.get("offensive_transition_total")),
                _int(row.get("defensive_transition_count")),
                _float(row.get("defensive_transition_total")),
                _int(row.get("goals_2_open_net")),
                _int(row.get("goals_2_guarded")),
                _int(row.get("goals_3_open_net")),
                _int(row.get("goals_3_guarded")),
                _json(row.get("metadata")),
            ),
        )
        count += 1
    return count


def get_match_metrics(connection: sqlite3.Connection, match_id: int) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT *
            FROM advanced_player_metrics
            WHERE match_id = ?
            ORDER BY
                CASE team
                    WHEN 'blue' THEN 0
                    WHEN 'orange' THEN 1
                    WHEN 'spectator' THEN 2
                    ELSE 3
                END,
                lower(match_alias),
                id
            """,
            (match_id,),
        )
    )


def get_player_metrics(connection: sqlite3.Connection, player_id: int) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT *
            FROM advanced_player_metrics
            WHERE player_id = ?
            ORDER BY match_id DESC, team, lower(match_alias), id
            """,
            (player_id,),
        )
    )


def _json(value: Any) -> str:
    return json.dumps(value or {}, sort_keys=True)


def _int(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _float(value: Any) -> float:
    if value is None:
        return 0.0
    return float(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
