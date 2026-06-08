"""Canonical player and alias helpers."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from arena_coach.database import connect_database
from arena_coach.repositories import players_repo


class PlayerService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def list_players(self, search: str = "") -> List[Dict[str, Any]]:
        with _connection(self.database_path) as connection:
            rows = players_repo.search_players(connection, search) if search.strip() else players_repo.list_players(connection)
            return [_player_dict(row) for row in rows]

    def get_player(self, player_id: int) -> Optional[Dict[str, Any]]:
        with _connection(self.database_path) as connection:
            row = players_repo.get_player(connection, player_id)
            return _player_dict(row) if row is not None else None

    def create_player(self, canonical_name: str, notes: Optional[str] = None) -> int:
        if not canonical_name.strip():
            raise ValueError("Canonical player name is required.")
        with _connection(self.database_path) as connection:
            with connection:
                return players_repo.create_player(connection, canonical_name.strip(), _clean(notes))

    def update_player(self, player_id: int, canonical_name: Optional[str] = None, notes: Optional[str] = None) -> None:
        with _connection(self.database_path) as connection:
            with connection:
                if not players_repo.update_player(connection, player_id, canonical_name=_clean(canonical_name), notes=_clean(notes)):
                    raise ValueError(f"Player id {player_id} does not exist.")

    def list_aliases(self, player_id: int) -> List[Dict[str, Any]]:
        with _connection(self.database_path) as connection:
            return [_alias_dict(row) for row in players_repo.list_aliases(connection, player_id)]

    def add_alias(
        self,
        player_id: int,
        alias_name: str,
        userid: Optional[str] = None,
        playerid: Optional[str] = None,
    ) -> int:
        if not alias_name.strip():
            raise ValueError("Alias name is required.")
        with _connection(self.database_path) as connection:
            with connection:
                player = players_repo.get_player(connection, player_id)
                if player is None:
                    raise ValueError(f"Player id {player_id} does not exist.")
                conflict = players_repo.find_alias_owned_by_other(connection, alias_name.strip(), player_id)
                if conflict is not None:
                    raise ValueError(
                        f"Alias already belongs to #{conflict['player_id']} {conflict['canonical_name']}."
                    )
                cleaned_userid = _clean(userid)
                alias_id = players_repo.add_alias(connection, player_id, alias_name.strip(), cleaned_userid, None)
                players_repo.add_userid(connection, player_id, cleaned_userid, source="manual_alias", confidence=1.0)
                return alias_id

    def suggestions_for_alias(
        self,
        alias_name: str,
        userid: Optional[str] = None,
        playerid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with _connection(self.database_path) as connection:
            return players_repo.suggest_players_for_alias(connection, alias_name, userid=userid, playerid=playerid)


@contextmanager
def _connection(database_path: Path):
    connection = connect_database(database_path)
    try:
        yield connection
    finally:
        connection.close()


def _player_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "canonical_name": row["canonical_name"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "alias_count": int(row["alias_count"] or 0) if "alias_count" in row.keys() else 0,
    }


def _alias_dict(row: Any) -> Dict[str, Any]:
    return {
        "id": int(row["id"]),
        "player_id": int(row["player_id"]),
        "alias_name": row["alias_name"],
        "userid": row["userid"],
        "playerid": row["playerid"],
        "confidence": float(row["confidence"] or 0),
        "created_at": row["created_at"],
    }


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    return text or None
