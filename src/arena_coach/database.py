"""SQLite bootstrap and connection helpers for Arena Coach."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = "6"


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS app_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_profiles (
        id INTEGER PRIMARY KEY,
        display_name TEXT NOT NULL,
        primary_echo_name TEXT,
        notes TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY,
        canonical_name TEXT NOT NULL,
        notes TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_aliases (
        id INTEGER PRIMARY KEY,
        player_id INTEGER NOT NULL,
        alias_name TEXT NOT NULL,
        userid TEXT,
        playerid TEXT,
        confidence REAL DEFAULT 1.0,
        created_at TEXT NOT NULL,
        UNIQUE(player_id, alias_name),
        FOREIGN KEY(player_id) REFERENCES players(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS player_userids (
        id INTEGER PRIMARY KEY,
        player_id INTEGER NOT NULL,
        userid TEXT NOT NULL UNIQUE,
        first_seen_at TEXT,
        last_seen_at TEXT,
        source TEXT,
        confidence REAL DEFAULT 1.0,
        FOREIGN KEY(player_id) REFERENCES players(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY,
        user_profile_id INTEGER,
        display_name TEXT,
        started_at TEXT,
        ended_at TEXT,
        sessionid TEXT,
        sessionip TEXT,
        match_type TEXT,
        match_classification TEXT,
        private_match_type TEXT,
        map_name TEXT,
        blue_score INTEGER,
        orange_score INTEGER,
        blue_round_wins INTEGER DEFAULT 0,
        orange_round_wins INTEGER DEFAULT 0,
        total_rounds_played INTEGER DEFAULT 0,
        round_summary_json TEXT,
        points_carry_over INTEGER DEFAULT NULL,
        user_team TEXT,
        result TEXT,
        raw_log_path TEXT,
        finalized INTEGER DEFAULT 0,
        metadata_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_profile_id) REFERENCES user_profiles(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS match_players (
        id INTEGER PRIMARY KEY,
        match_id INTEGER NOT NULL,
        player_id INTEGER,
        match_alias TEXT NOT NULL,
        userid TEXT,
        playerid TEXT,
        team TEXT,
        is_user INTEGER DEFAULT 0,
        confirmed INTEGER DEFAULT 0,
        metadata_json TEXT,
        FOREIGN KEY(match_id) REFERENCES matches(id),
        FOREIGN KEY(player_id) REFERENCES players(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY,
        match_id INTEGER NOT NULL,
        sequence INTEGER,
        captured_at TEXT,
        game_clock REAL,
        game_clock_display TEXT,
        event_type TEXT NOT NULL,
        actor_player_id INTEGER,
        target_player_id INTEGER,
        assist_player_id INTEGER,
        actor_alias TEXT,
        target_alias TEXT,
        assist_alias TEXT,
        actor_userid TEXT,
        target_userid TEXT,
        assist_userid TEXT,
        actor_playerid TEXT,
        target_playerid TEXT,
        assist_playerid TEXT,
        team TEXT,
        value REAL,
        raw_text TEXT,
        metadata_json TEXT,
        FOREIGN KEY(match_id) REFERENCES matches(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS match_player_stats (
        id INTEGER PRIMARY KEY,
        match_id INTEGER NOT NULL,
        player_id INTEGER,
        match_alias TEXT NOT NULL,
        userid TEXT,
        playerid TEXT,
        team TEXT,
        points INTEGER DEFAULT 0,
        goals INTEGER DEFAULT 0,
        assists INTEGER DEFAULT 0,
        saves INTEGER DEFAULT 0,
        stuns INTEGER DEFAULT 0,
        steals INTEGER DEFAULT 0,
        shots INTEGER DEFAULT 0,
        passes INTEGER DEFAULT 0,
        catches INTEGER DEFAULT 0,
        turnovers INTEGER DEFAULT 0,
        interceptions INTEGER DEFAULT 0,
        blocks INTEGER DEFAULT 0,
        possession_time REAL DEFAULT 0,
        metadata_json TEXT,
        FOREIGN KEY(match_id) REFERENCES matches(id),
        FOREIGN KEY(player_id) REFERENCES players(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS advanced_events (
        id INTEGER PRIMARY KEY,
        match_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        actor_player_id INTEGER,
        target_player_id INTEGER,
        assist_player_id INTEGER,
        actor_alias TEXT,
        target_alias TEXT,
        assist_alias TEXT,
        team TEXT,
        start_sequence INTEGER,
        end_sequence INTEGER,
        start_game_clock REAL,
        end_game_clock REAL,
        confidence TEXT,
        confidence_score REAL,
        directness TEXT,
        value REAL,
        explanation TEXT,
        evidence_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(match_id) REFERENCES matches(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS advanced_player_metrics (
        id INTEGER PRIMARY KEY,
        match_id INTEGER NOT NULL,
        player_id INTEGER,
        match_alias TEXT NOT NULL,
        userid TEXT,
        team TEXT,
        completed_passes INTEGER DEFAULT 0,
        inferred_catches INTEGER DEFAULT 0,
        initiators INTEGER DEFAULT 0,
        open_for_pass_samples INTEGER DEFAULT 0,
        lane_blocked_samples INTEGER DEFAULT 0,
        lane_blocks INTEGER DEFAULT 0,
        tight_man_coverage_samples INTEGER DEFAULT 0,
        loose_man_coverage_samples INTEGER DEFAULT 0,
        no_man_coverage_samples INTEGER DEFAULT 0,
        goalie_coverage_samples INTEGER DEFAULT 0,
        clear_attempts INTEGER DEFAULT 0,
        successful_clears INTEGER DEFAULT 0,
        failed_clears INTEGER DEFAULT 0,
        inferred_turnovers INTEGER DEFAULT 0,
        inferred_interceptions INTEGER DEFAULT 0,
        steal_takeaways INTEGER DEFAULT 0,
        stun_takeaways INTEGER DEFAULT 0,
        missed_shots INTEGER DEFAULT 0,
        shots_saved_against INTEGER DEFAULT 0,
        blocked_shots INTEGER DEFAULT 0,
        stuffed_shots INTEGER DEFAULT 0,
        offensive_transition_count INTEGER DEFAULT 0,
        offensive_transition_total REAL DEFAULT 0,
        defensive_transition_count INTEGER DEFAULT 0,
        defensive_transition_total REAL DEFAULT 0,
        goals_2_open_net INTEGER DEFAULT 0,
        goals_2_guarded INTEGER DEFAULT 0,
        goals_3_open_net INTEGER DEFAULT 0,
        goals_3_guarded INTEGER DEFAULT 0,
        metadata_json TEXT,
        FOREIGN KEY(match_id) REFERENCES matches(id),
        FOREIGN KEY(player_id) REFERENCES players(id),
        UNIQUE(match_id, match_alias, team)
    )
    """,
]


INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_player_aliases_alias_name ON player_aliases(alias_name)",
    "CREATE INDEX IF NOT EXISTS idx_player_userids_userid ON player_userids(userid)",
    "CREATE INDEX IF NOT EXISTS idx_player_userids_player_id ON player_userids(player_id)",
    "CREATE INDEX IF NOT EXISTS idx_matches_started_at ON matches(started_at)",
    "CREATE INDEX IF NOT EXISTS idx_events_match_id ON events(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type)",
    "CREATE INDEX IF NOT EXISTS idx_events_actor_alias ON events(actor_alias)",
    "CREATE INDEX IF NOT EXISTS idx_events_target_alias ON events(target_alias)",
    "CREATE INDEX IF NOT EXISTS idx_match_players_match_id ON match_players(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_match_player_stats_match_id ON match_player_stats(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_advanced_events_match_id ON advanced_events(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_advanced_events_event_type ON advanced_events(event_type)",
    "CREATE INDEX IF NOT EXISTS idx_advanced_events_actor_player_id ON advanced_events(actor_player_id)",
    "CREATE INDEX IF NOT EXISTS idx_advanced_player_metrics_match_id ON advanced_player_metrics(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_advanced_player_metrics_player_id ON advanced_player_metrics(player_id)",
]


def initialize_database(database_path: Path) -> Path:
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    _backup_before_schema_update(database_path)

    connection = connect_database(database_path)
    try:
        with connection:
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
            _migrate_existing_schema(connection)
            for statement in INDEX_STATEMENTS:
                connection.execute(statement)
            _backfill_player_userids(connection)
            connection.execute(
                """
                INSERT INTO app_metadata (key, value)
                VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (SCHEMA_VERSION,),
            )
            connection.execute(
                """
                INSERT INTO app_metadata (key, value)
                VALUES ('app_name', 'Arena Coach')
                ON CONFLICT(key) DO NOTHING
                """
            )
    finally:
        connection.close()

    return database_path


def create_database_backup(database_path: Path, backup_path: Path) -> Path:
    source_path = Path(database_path)
    target_path = Path(backup_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not source_path.exists():
        raise FileNotFoundError(f"Database does not exist: {source_path}")

    source = sqlite3.connect(source_path)
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return target_path


def _migrate_existing_schema(connection: sqlite3.Connection) -> None:
    _add_column_if_missing(connection, "matches", "display_name", "TEXT")
    _add_column_if_missing(connection, "matches", "match_classification", "TEXT")
    _add_column_if_missing(connection, "matches", "private_match_type", "TEXT")
    _add_column_if_missing(connection, "matches", "blue_round_wins", "INTEGER DEFAULT 0")
    _add_column_if_missing(connection, "matches", "orange_round_wins", "INTEGER DEFAULT 0")
    _add_column_if_missing(connection, "matches", "total_rounds_played", "INTEGER DEFAULT 0")
    _add_column_if_missing(connection, "matches", "round_summary_json", "TEXT")
    _add_column_if_missing(connection, "matches", "points_carry_over", "INTEGER DEFAULT NULL")


def _backup_before_schema_update(database_path: Path) -> None:
    path = Path(database_path)
    if not path.exists() or path.stat().st_size <= 0:
        return
    if not _schema_backup_needed(path):
        return
    backups_dir = path.parent.parent / "backups"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    backup_path = backups_dir / f"arena_coach_backup_pre_migration_{timestamp}.db"
    create_database_backup(path, backup_path)


def _schema_backup_needed(database_path: Path) -> bool:
    try:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        try:
            tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if "app_metadata" not in tables:
                return True
            row = connection.execute(
                "SELECT value FROM app_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                return True
            return str(row["value"]) != SCHEMA_VERSION
        finally:
            connection.close()
    except sqlite3.Error:
        return True


def _add_column_if_missing(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _backfill_player_userids(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO player_userids (
            player_id, userid, first_seen_at, last_seen_at, source, confidence
        )
        SELECT player_id, userid, created_at, created_at, 'alias_backfill', confidence
        FROM player_aliases
        WHERE userid IS NOT NULL AND userid != ''
        """
    )


def connect_database(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(database_path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection
