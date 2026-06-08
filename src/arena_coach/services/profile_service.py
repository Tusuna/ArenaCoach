"""Profile workflow helpers for GUI and tests."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from arena_coach.database import connect_database
from arena_coach.repositories import profiles_repo


class ProfileService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def list_profiles(self) -> List[Dict[str, Any]]:
        with _connection(self.database_path) as connection:
            active_id = profiles_repo.get_active_profile_id(connection)
            return [_profile_dict(row, active_id) for row in profiles_repo.list_profiles(connection)]

    def get_active_profile(self) -> Optional[Dict[str, Any]]:
        with _connection(self.database_path) as connection:
            row = profiles_repo.get_active_profile(connection)
            if row is None:
                return None
            return _profile_dict(row, int(row["id"]))

    def create_profile(self, display_name: str, echo_name: Optional[str] = None) -> int:
        if not display_name.strip():
            raise ValueError("Display name is required.")
        with _connection(self.database_path) as connection:
            with connection:
                return profiles_repo.create_profile(connection, display_name.strip(), _clean(echo_name))

    def set_active_profile(self, profile_id: int) -> None:
        with _connection(self.database_path) as connection:
            with connection:
                if not profiles_repo.set_active_profile(connection, profile_id):
                    raise ValueError(f"Profile id {profile_id} does not exist.")

    def update_active_profile(
        self,
        display_name: Optional[str] = None,
        echo_name: Optional[str] = None,
    ) -> None:
        with _connection(self.database_path) as connection:
            with connection:
                active = profiles_repo.get_active_profile(connection)
                if active is None:
                    raise ValueError("No active profile.")
                profiles_repo.update_profile(
                    connection,
                    int(active["id"]),
                    display_name=_clean(display_name),
                    primary_echo_name=_clean(echo_name),
                )


@contextmanager
def _connection(database_path: Path):
    connection = connect_database(database_path)
    try:
        yield connection
    finally:
        connection.close()


def _profile_dict(row: Any, active_id: Optional[int]) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "display_name": row["display_name"],
        "primary_echo_name": row["primary_echo_name"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "active": active_id == int(row["id"]),
    }


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return text or None
