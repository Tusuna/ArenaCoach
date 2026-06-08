"""CRUD helpers for canonical players and aliases."""

from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
import sqlite3
from typing import Any, Dict, Optional


def create_player(connection: sqlite3.Connection, canonical_name: str, notes: Optional[str] = None) -> int:
    cursor = connection.execute(
        """
        INSERT INTO players (canonical_name, notes, created_at)
        VALUES (?, ?, ?)
        """,
        (canonical_name, notes, _now()),
    )
    return int(cursor.lastrowid)


def get_player(connection: sqlite3.Connection, player_id: int) -> Optional[sqlite3.Row]:
    return connection.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()


def update_player(
    connection: sqlite3.Connection,
    player_id: int,
    *,
    canonical_name: Optional[str] = None,
    notes: Optional[str] = None,
) -> bool:
    existing = get_player(connection, player_id)
    if existing is None:
        return False
    connection.execute(
        """
        UPDATE players
        SET canonical_name = ?, notes = ?
        WHERE id = ?
        """,
        (
            canonical_name if canonical_name is not None else existing["canonical_name"],
            notes if notes is not None else existing["notes"],
            player_id,
        ),
    )
    return True


def list_players(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT
                p.*,
                COUNT(pa.id) AS alias_count
            FROM players p
            LEFT JOIN player_aliases pa ON pa.player_id = p.id
            GROUP BY p.id
            ORDER BY lower(p.canonical_name), p.id
            """
        )
    )


def search_players(connection: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    pattern = f"%{query.casefold()}%"
    return list(
        connection.execute(
            """
            SELECT
                p.*,
                COUNT(pa.id) AS alias_count
            FROM players p
            LEFT JOIN player_aliases pa ON pa.player_id = p.id
            WHERE lower(p.canonical_name) LIKE ? OR lower(COALESCE(pa.alias_name, '')) LIKE ?
            GROUP BY p.id
            ORDER BY lower(p.canonical_name), p.id
            """,
            (pattern, pattern),
        )
    )


def find_alias(connection: sqlite3.Connection, alias_name: str) -> Optional[sqlite3.Row]:
    return connection.execute(
        """
        SELECT pa.*, p.canonical_name
        FROM player_aliases pa
        JOIN players p ON p.id = pa.player_id
        WHERE lower(pa.alias_name) = lower(?)
        ORDER BY pa.confidence DESC, pa.id
        LIMIT 1
        """,
        (alias_name,),
    ).fetchone()


def find_alias_for_player(connection: sqlite3.Connection, player_id: int, alias_name: str) -> Optional[sqlite3.Row]:
    return connection.execute(
        """
        SELECT *
        FROM player_aliases
        WHERE player_id = ? AND lower(alias_name) = lower(?)
        ORDER BY id
        LIMIT 1
        """,
        (player_id, alias_name),
    ).fetchone()


def find_alias_owned_by_other(
    connection: sqlite3.Connection,
    alias_name: str,
    player_id: int,
) -> Optional[sqlite3.Row]:
    return connection.execute(
        """
        SELECT pa.*, p.canonical_name
        FROM player_aliases pa
        JOIN players p ON p.id = pa.player_id
        WHERE lower(pa.alias_name) = lower(?) AND pa.player_id != ?
        ORDER BY pa.confidence DESC, pa.id
        LIMIT 1
        """,
        (alias_name, player_id),
    ).fetchone()


def find_aliases_by_userid(connection: sqlite3.Connection, userid: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT pa.*, p.canonical_name
            FROM player_aliases pa
            JOIN players p ON p.id = pa.player_id
            WHERE pa.userid = ?
            ORDER BY pa.confidence DESC, pa.id
            """,
            (userid,),
        )
    )


def find_userid(connection: sqlite3.Connection, userid: str) -> Optional[sqlite3.Row]:
    return connection.execute(
        """
        SELECT pu.*, p.canonical_name
        FROM player_userids pu
        JOIN players p ON p.id = pu.player_id
        WHERE pu.userid = ?
        LIMIT 1
        """,
        (userid,),
    ).fetchone()


def list_userids(connection: sqlite3.Connection, player_id: int) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT *
            FROM player_userids
            WHERE player_id = ?
            ORDER BY last_seen_at DESC, id DESC
            """,
            (player_id,),
        )
    )


def add_userid(
    connection: sqlite3.Connection,
    player_id: int,
    userid: Optional[str],
    *,
    source: str = "manual_mapping",
    confidence: float = 1.0,
) -> Optional[int]:
    if userid is None or str(userid).strip() == "":
        return None
    userid = str(userid).strip()
    existing = find_userid(connection, userid)
    now = _now()
    if existing is not None:
        if int(existing["player_id"]) != int(player_id):
            raise ValueError(
                f"User ID {userid} already belongs to #{existing['player_id']} {existing['canonical_name']}."
            )
        connection.execute(
            """
            UPDATE player_userids
            SET last_seen_at = ?, source = ?, confidence = MAX(confidence, ?)
            WHERE id = ?
            """,
            (now, source, confidence, existing["id"]),
        )
        return int(existing["id"])
    cursor = connection.execute(
        """
        INSERT INTO player_userids (player_id, userid, first_seen_at, last_seen_at, source, confidence)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (player_id, userid, now, now, source, confidence),
    )
    return int(cursor.lastrowid)


def find_aliases_by_playerid(connection: sqlite3.Connection, playerid: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT pa.*, p.canonical_name
            FROM player_aliases pa
            JOIN players p ON p.id = pa.player_id
            WHERE pa.playerid = ?
            ORDER BY pa.confidence DESC, pa.id
            """,
            (playerid,),
        )
    )


def list_aliases(connection: sqlite3.Connection, player_id: int) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT *
            FROM player_aliases
            WHERE player_id = ?
            ORDER BY lower(alias_name), id
            """,
            (player_id,),
        )
    )


def add_alias(
    connection: sqlite3.Connection,
    player_id: int,
    alias_name: str,
    userid: Optional[str] = None,
    playerid: Optional[str] = None,
    confidence: float = 1.0,
) -> int:
    existing = find_alias_for_player(connection, player_id, alias_name)
    if existing is not None:
        connection.execute(
            """
            UPDATE player_aliases
            SET
                userid = COALESCE(?, userid),
                playerid = COALESCE(?, playerid),
                confidence = ?
            WHERE id = ?
            """,
            (userid, playerid, confidence, existing["id"]),
        )
        return int(existing["id"])

    cursor = connection.execute(
        """
        INSERT INTO player_aliases (player_id, alias_name, userid, playerid, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(player_id, alias_name) DO UPDATE SET
            userid = COALESCE(excluded.userid, player_aliases.userid),
            playerid = COALESCE(excluded.playerid, player_aliases.playerid),
            confidence = excluded.confidence
        """,
        (player_id, alias_name, userid, playerid, confidence, _now()),
    )
    if cursor.lastrowid:
        return int(cursor.lastrowid)
    row = connection.execute(
        "SELECT id FROM player_aliases WHERE player_id = ? AND alias_name = ?",
        (player_id, alias_name),
    ).fetchone()
    return int(row["id"])


def suggest_players_for_alias(
    connection: sqlite3.Connection,
    alias_name: str,
    userid: Optional[str] = None,
    playerid: Optional[str] = None,
    limit: int = 5,
) -> list[Dict[str, Any]]:
    suggestions: Dict[int, Dict[str, Any]] = {}

    def add(row: sqlite3.Row, reason: str, confidence: float) -> None:
        player_id = int(row["player_id"] if "player_id" in row.keys() else row["id"])
        existing = suggestions.get(player_id)
        if existing is None or confidence > existing["confidence"]:
            suggestions[player_id] = {
                "player_id": player_id,
                "canonical_name": row["canonical_name"],
                "reason": reason,
                "confidence": confidence,
                "alias_name": row["alias_name"] if "alias_name" in row.keys() else None,
                "userid": row["userid"] if "userid" in row.keys() else None,
            }

    exact = connection.execute(
        """
        SELECT pa.*, p.canonical_name
        FROM player_aliases pa
        JOIN players p ON p.id = pa.player_id
        WHERE pa.alias_name = ?
        ORDER BY pa.confidence DESC, pa.id
        """,
        (alias_name,),
    ).fetchall()
    for row in exact:
        add(row, "exact alias match", 1.0)

    case_matches = connection.execute(
        """
        SELECT pa.*, p.canonical_name
        FROM player_aliases pa
        JOIN players p ON p.id = pa.player_id
        WHERE lower(pa.alias_name) = lower(?) AND pa.alias_name != ?
        ORDER BY pa.confidence DESC, pa.id
        """,
        (alias_name, alias_name),
    ).fetchall()
    for row in case_matches:
        add(row, "case-insensitive alias match", 0.98)

    if userid:
        known_userid = find_userid(connection, userid)
        if known_userid is not None:
            add(known_userid, "same userid", 1.0)
        for row in find_aliases_by_userid(connection, userid):
            add(row, "userid match", 0.99)
    folded_alias = alias_name.casefold()
    candidate_rows = connection.execute(
        """
        SELECT p.id AS player_id, p.canonical_name, pa.alias_name
        FROM players p
        LEFT JOIN player_aliases pa ON pa.player_id = p.id
        """
    ).fetchall()
    for row in candidate_rows:
        names = [row["canonical_name"]]
        if row["alias_name"]:
            names.append(row["alias_name"])
        score = max((_similarity(folded_alias, str(name).casefold()) for name in names if name), default=0.0)
        if score >= 0.72:
            add(row, "fuzzy name match", round(score, 2))

    return sorted(
        suggestions.values(),
        key=lambda suggestion: (-suggestion["confidence"], suggestion["canonical_name"].casefold(), suggestion["player_id"]),
    )[:limit]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()
