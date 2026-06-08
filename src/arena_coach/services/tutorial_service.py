"""First-run tutorial state."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from arena_coach.database import connect_database


TUTORIAL_SEEN_KEY = "gui_tutorial_seen"


class TutorialService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def has_seen_tutorial(self) -> bool:
        with _connection(self.database_path) as connection:
            row = connection.execute(
                "SELECT value FROM app_metadata WHERE key = ?",
                (TUTORIAL_SEEN_KEY,),
            ).fetchone()
        return bool(row and str(row["value"]).strip().casefold() in {"1", "true", "yes"})

    def mark_seen(self) -> None:
        with _connection(self.database_path) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO app_metadata (key, value, updated_at)
                    VALUES (?, 'true', CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (TUTORIAL_SEEN_KEY,),
                )


@contextmanager
def _connection(database_path: Path):
    connection = connect_database(database_path)
    try:
        yield connection
    finally:
        connection.close()
