"""CRUD helpers for user profiles."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import Optional


ACTIVE_PROFILE_KEY = "active_profile_id"


def create_profile(
    connection: sqlite3.Connection,
    display_name: str,
    primary_echo_name: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO user_profiles (display_name, primary_echo_name, notes, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (display_name, primary_echo_name, notes, _now()),
    )
    return int(cursor.lastrowid)


def get_profile(connection: sqlite3.Connection, profile_id: int) -> Optional[sqlite3.Row]:
    return connection.execute("SELECT * FROM user_profiles WHERE id = ?", (profile_id,)).fetchone()


def list_profiles(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(connection.execute("SELECT * FROM user_profiles ORDER BY created_at, id"))


def update_profile(
    connection: sqlite3.Connection,
    profile_id: int,
    *,
    display_name: Optional[str] = None,
    primary_echo_name: Optional[str] = None,
    notes: Optional[str] = None,
) -> bool:
    existing = get_profile(connection, profile_id)
    if existing is None:
        return False

    connection.execute(
        """
        UPDATE user_profiles
        SET display_name = ?, primary_echo_name = ?, notes = ?
        WHERE id = ?
        """,
        (
            display_name if display_name is not None else existing["display_name"],
            primary_echo_name if primary_echo_name is not None else existing["primary_echo_name"],
            notes if notes is not None else existing["notes"],
            profile_id,
        ),
    )
    return True


def update_primary_echo_name(connection: sqlite3.Connection, profile_id: int, primary_echo_name: str) -> None:
    update_profile(connection, profile_id, primary_echo_name=primary_echo_name)


def set_active_profile(connection: sqlite3.Connection, profile_id: int) -> bool:
    if get_profile(connection, profile_id) is None:
        return False

    connection.execute(
        """
        INSERT INTO app_metadata (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (ACTIVE_PROFILE_KEY, str(profile_id)),
    )
    return True


def get_active_profile_id(connection: sqlite3.Connection) -> Optional[int]:
    row = connection.execute(
        "SELECT value FROM app_metadata WHERE key = ?",
        (ACTIVE_PROFILE_KEY,),
    ).fetchone()
    if row is None:
        return None
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return None


def get_active_profile(connection: sqlite3.Connection) -> Optional[sqlite3.Row]:
    profile_id = get_active_profile_id(connection)
    if profile_id is None:
        return None
    return get_profile(connection, profile_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
