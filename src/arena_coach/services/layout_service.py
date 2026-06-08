"""Persist per-tab GUI card layout state in the local Arena Coach database."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
from typing import Any, Iterable, Optional

from arena_coach.database import connect_database
from arena_coach.repositories import profiles_repo


CARD_ORDER_PREFIX = "gui_card_order_"
PROFILE_CARD_ORDER_PREFIX = "gui_card_order_profile_"
CARD_SIZE_PREFIX = "gui_card_sizes_"
PROFILE_CARD_SIZE_PREFIX = "gui_card_sizes_profile_"


class LayoutService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def active_profile_id(self) -> Optional[int]:
        with _connection(self.database_path) as connection:
            return profiles_repo.get_active_profile_id(connection)

    def load_card_order(
        self,
        tab_id: str,
        default_order: Iterable[str],
        profile_id: Optional[int] = None,
    ) -> list[str]:
        expected = list(default_order)
        expected_set = set(expected)
        for key in _candidate_keys(CARD_ORDER_PREFIX, PROFILE_CARD_ORDER_PREFIX, tab_id, profile_id):
            with _connection(self.database_path) as connection:
                raw = _get_value(connection, key)
            order = _parse_order(raw)
            if order is None:
                continue
            if set(order) == expected_set and len(order) == len(expected):
                return order
        return expected

    def save_card_order(self, tab_id: str, order: Iterable[str], profile_id: Optional[int] = None) -> None:
        key = _profile_key(CARD_ORDER_PREFIX, PROFILE_CARD_ORDER_PREFIX, tab_id, profile_id)
        payload = json.dumps(list(order))
        with _connection(self.database_path) as connection:
            with connection:
                _set_value(connection, key, payload)

    def load_card_sizes(self, tab_id: str, profile_id: Optional[int] = None) -> dict[str, int]:
        for key in _candidate_keys(CARD_SIZE_PREFIX, PROFILE_CARD_SIZE_PREFIX, tab_id, profile_id):
            with _connection(self.database_path) as connection:
                raw = _get_value(connection, key)
            sizes = _parse_sizes(raw)
            if sizes is not None:
                return sizes
        return {}

    def save_card_sizes(self, tab_id: str, sizes: dict[str, int], profile_id: Optional[int] = None) -> None:
        key = _profile_key(CARD_SIZE_PREFIX, PROFILE_CARD_SIZE_PREFIX, tab_id, profile_id)
        payload = json.dumps(sizes)
        with _connection(self.database_path) as connection:
            with connection:
                _set_value(connection, key, payload)

    def reset_card_order(self, tab_id: str, profile_id: Optional[int] = None) -> None:
        keys = [
            _profile_key(CARD_ORDER_PREFIX, PROFILE_CARD_ORDER_PREFIX, tab_id, profile_id),
            _profile_key(CARD_SIZE_PREFIX, PROFILE_CARD_SIZE_PREFIX, tab_id, profile_id),
        ]
        with _connection(self.database_path) as connection:
            with connection:
                for key in keys:
                    connection.execute("DELETE FROM app_metadata WHERE key = ?", (key,))

    def reset_all_card_orders(self, profile_id: Optional[int] = None) -> None:
        if profile_id is None:
            patterns = [f"{CARD_ORDER_PREFIX}%", f"{CARD_SIZE_PREFIX}%"]
        else:
            patterns = [
                f"{PROFILE_CARD_ORDER_PREFIX}{profile_id}_%",
                f"{PROFILE_CARD_SIZE_PREFIX}{profile_id}_%",
            ]
        with _connection(self.database_path) as connection:
            with connection:
                for pattern in patterns:
                    connection.execute("DELETE FROM app_metadata WHERE key LIKE ?", (pattern,))


@contextmanager
def _connection(database_path: Path):
    connection = connect_database(database_path)
    try:
        yield connection
    finally:
        connection.close()


def _profile_key(global_prefix: str, profile_prefix: str, tab_id: str, profile_id: Optional[int]) -> str:
    if profile_id is None:
        return f"{global_prefix}{tab_id}"
    return f"{profile_prefix}{profile_id}_{tab_id}"


def _candidate_keys(global_prefix: str, profile_prefix: str, tab_id: str, profile_id: Optional[int]) -> list[str]:
    keys = []
    if profile_id is not None:
        keys.append(_profile_key(global_prefix, profile_prefix, tab_id, profile_id))
    keys.append(_profile_key(global_prefix, profile_prefix, tab_id, None))
    return keys


def _set_value(connection: Any, key: str, value: str) -> None:
    connection.execute(
        """
        INSERT INTO app_metadata (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (key, value),
    )


def _get_value(connection: Any, key: str) -> Optional[str]:
    row = connection.execute("SELECT value FROM app_metadata WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return str(row["value"])


def _parse_order(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    normalized = [str(item) for item in parsed if str(item).strip()]
    return normalized or None


def _parse_sizes(value: Optional[str]) -> Optional[dict[str, int]]:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    normalized: dict[str, int] = {}
    for key, raw_size in parsed.items():
        try:
            size = int(raw_size)
        except (TypeError, ValueError):
            continue
        if size >= 80:
            normalized[str(key)] = size
    return normalized
