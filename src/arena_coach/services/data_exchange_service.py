"""Export, import, and backup helpers for tester-friendly data sharing."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any, Iterable, Optional
from zipfile import ZIP_DEFLATED, ZipFile

from arena_coach import __app_name__, __version__
from arena_coach.config import AppConfig
from arena_coach.database import create_database_backup, connect_database
from arena_coach.repositories import profiles_repo


EXPORT_VERSION = "1.0"


class DataExchangeService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def exports_dir(self) -> Path:
        return self.config.exports_dir

    @property
    def imports_dir(self) -> Path:
        return self.config.imports_dir

    @property
    def backups_dir(self) -> Path:
        return self.config.backups_dir

    def export_data(
        self,
        *,
        include_raw_logs: bool = False,
        include_debug_logs: bool = False,
        include_unfinalized_matches: bool = True,
        include_advanced_events: bool = True,
    ) -> dict[str, Any]:
        self._ensure_directories()
        timestamp = _timestamp_slug()
        active_profile = self._active_profile()
        export_owner = _export_owner_slug(active_profile)
        export_path = self.exports_dir / f"ArenaCoach_Export_{export_owner}_{timestamp}.zip"

        with tempfile.TemporaryDirectory(prefix="arena_coach_export_") as temp_dir:
            temp_root = Path(temp_dir)
            db_copy_path = temp_root / "arena_coach.db"
            self._build_export_database(
                db_copy_path,
                include_unfinalized_matches=include_unfinalized_matches,
                include_advanced_events=include_advanced_events,
            )
            manifest = self._build_manifest(
                profile_display_name=(active_profile or {}).get("display_name"),
                created_at=_now_iso(),
                raw_logs_included=include_raw_logs,
                debug_logs_included=include_debug_logs,
                include_unfinalized_matches=include_unfinalized_matches,
                include_advanced_events=include_advanced_events,
            )
            readme_text = self._export_readme_text(manifest)

            raw_log_paths = self._raw_logs_for_export(db_copy_path) if include_raw_logs else []
            debug_log_paths = self._debug_logs_for_export() if include_debug_logs else []

            with ZipFile(export_path, "w", compression=ZIP_DEFLATED) as archive:
                archive.write(db_copy_path, arcname="arena_coach.db")
                archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
                archive.writestr("README_EXPORT.txt", readme_text)
                for path in raw_log_paths:
                    if path.exists() and path.is_file():
                        archive.write(path, arcname=f"raw_logs/{path.name}")
                for path in debug_log_paths:
                    if path.exists() and path.is_file():
                        archive.write(path, arcname=f"debug_logs/{path.name}")

        return {
            "export_path": export_path,
            "manifest": manifest,
            "raw_logs_included": include_raw_logs,
            "debug_logs_included": include_debug_logs,
            "raw_log_files": [str(path) for path in raw_log_paths],
            "debug_log_files": [str(path) for path in debug_log_paths],
        }

    def import_data(self, zip_path: Path) -> dict[str, Any]:
        self._ensure_directories()
        archive_path = Path(zip_path).resolve()
        if not archive_path.exists():
            raise FileNotFoundError(f"Export zip was not found: {archive_path}")
        with ZipFile(archive_path, "r") as archive:
            names = set(archive.namelist())
            if "manifest.json" not in names:
                raise ValueError("Export zip is missing manifest.json")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("Export manifest is not a JSON object.")
            if "export_version" not in manifest:
                raise ValueError("Export manifest is missing export_version.")
            target_dir = self.imports_dir / f"{archive_path.stem}_{_timestamp_slug()}"
            target_dir.mkdir(parents=True, exist_ok=False)
            _safe_extract_archive(archive, target_dir)

        return {
            "import_dir": target_dir,
            "manifest": manifest,
            "database_path": target_dir / "arena_coach.db",
        }

    def list_imports(self) -> list[dict[str, Any]]:
        self._ensure_directories()
        rows: list[dict[str, Any]] = []
        for directory in sorted(self.imports_dir.iterdir(), key=lambda path: path.name.lower(), reverse=True):
            if not directory.is_dir():
                continue
            manifest_path = directory / "manifest.json"
            manifest: dict[str, Any] = {}
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    manifest = {}
            rows.append(
                {
                    "name": directory.name,
                    "path": directory,
                    "database_path": directory / "arena_coach.db",
                    "manifest": manifest,
                    "created_at": manifest.get("created_at") if isinstance(manifest, dict) else None,
                    "profile_display_name": manifest.get("profile_display_name") if isinstance(manifest, dict) else None,
                }
            )
        return rows

    def backup_database(self, *, reason: str = "manual") -> dict[str, Any]:
        self._ensure_directories()
        timestamp = _timestamp_slug()
        backup_reason = _safe_name(reason) or "manual"
        backup_path = self.backups_dir / f"ArenaCoach_Backup_{backup_reason}_{timestamp}.db"
        create_database_backup(self.config.database_path, backup_path)
        return {
            "backup_path": backup_path,
            "reason": reason,
            "created_at": _now_iso(),
            "size_bytes": backup_path.stat().st_size if backup_path.exists() else 0,
        }

    def openable_paths(self) -> dict[str, Path]:
        self._ensure_directories()
        return {
            "exports": self.exports_dir,
            "imports": self.imports_dir,
            "backups": self.backups_dir,
        }

    def _ensure_directories(self) -> None:
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.imports_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    def _active_profile(self) -> Optional[dict[str, Any]]:
        with _connection(self.config.database_path) as connection:
            active = profiles_repo.get_active_profile(connection)
        if active is None:
            return None
        return {
            "id": int(active["id"]),
            "display_name": active["display_name"],
            "primary_echo_name": active["primary_echo_name"],
        }

    def _build_export_database(
        self,
        target_path: Path,
        *,
        include_unfinalized_matches: bool,
        include_advanced_events: bool,
    ) -> None:
        create_database_backup(self.config.database_path, target_path)
        connection = sqlite3.connect(target_path)
        try:
            with connection:
                if not include_unfinalized_matches:
                    match_ids = [
                        int(row[0])
                        for row in connection.execute("SELECT id FROM matches WHERE finalized = 0").fetchall()
                    ]
                    self._delete_match_rows(connection, match_ids)
                if not include_advanced_events:
                    connection.execute("DELETE FROM advanced_events")
                    connection.execute("DELETE FROM advanced_player_metrics")
        finally:
            connection.close()

    def _delete_match_rows(self, connection: sqlite3.Connection, match_ids: Iterable[int]) -> None:
        ids = [int(match_id) for match_id in match_ids]
        if not ids:
            return
        placeholders = ", ".join("?" for _ in ids)
        for table in ("advanced_events", "advanced_player_metrics", "events", "match_player_stats", "match_players"):
            connection.execute(f"DELETE FROM {table} WHERE match_id IN ({placeholders})", ids)
        connection.execute(f"DELETE FROM matches WHERE id IN ({placeholders})", ids)

    def _build_manifest(
        self,
        *,
        profile_display_name: Optional[str],
        created_at: str,
        raw_logs_included: bool,
        debug_logs_included: bool,
        include_unfinalized_matches: bool,
        include_advanced_events: bool,
    ) -> dict[str, Any]:
        with _connection(self.config.database_path) as connection:
            schema_version = connection.execute(
                "SELECT value FROM app_metadata WHERE key = 'schema_version'"
            ).fetchone()
        return {
            "export_version": EXPORT_VERSION,
            "app_name": __app_name__,
            "app_version": __version__,
            "schema_version": str(schema_version["value"]) if schema_version is not None else "unknown",
            "created_at": created_at,
            "profile_display_name": profile_display_name or "unknown",
            "database_included": True,
            "raw_logs_included": bool(raw_logs_included),
            "debug_logs_included": bool(debug_logs_included),
            "include_unfinalized_matches": bool(include_unfinalized_matches),
            "include_advanced_events": bool(include_advanced_events),
            "notes": "Tester export for Arena Coach developer review",
        }

    def _raw_logs_for_export(self, database_copy_path: Path) -> list[Path]:
        connection = sqlite3.connect(database_copy_path)
        try:
            rows = connection.execute("SELECT raw_log_path FROM matches WHERE raw_log_path IS NOT NULL AND raw_log_path != ''").fetchall()
        finally:
            connection.close()
        paths: list[Path] = []
        seen: set[str] = set()
        for row in rows:
            value = str(row[0])
            if value in seen:
                continue
            seen.add(value)
            path = Path(value)
            if path.exists() and path.is_file():
                paths.append(path)
            metadata_path = path.with_suffix(path.suffix + ".metadata.json")
            if metadata_path.exists() and metadata_path.is_file() and str(metadata_path) not in seen:
                seen.add(str(metadata_path))
                paths.append(metadata_path)
        return paths

    def _debug_logs_for_export(self) -> list[Path]:
        logs_root = self.config.project_root / "logs"
        if not logs_root.exists():
            return []
        rows: list[Path] = []
        for path in logs_root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.casefold() == ".jsonl":
                continue
            if path.name.endswith(".metadata.json"):
                rows.append(path)
                continue
            if path.suffix.casefold() in {".log", ".txt"}:
                rows.append(path)
        return rows

    def _export_readme_text(self, manifest: dict[str, Any]) -> str:
        return (
            f"{__app_name__} tester export\n\n"
            f"Created: {manifest['created_at']}\n"
            f"App version: {manifest['app_version']}\n"
            f"Profile: {manifest['profile_display_name']}\n\n"
            "This zip contains a database copy for developer review.\n"
            "Raw logs and debug logs are included only if selected during export.\n"
            "Do not merge this database into another install automatically.\n"
        )


@contextmanager
def _connection(database_path: Path):
    connection = connect_database(database_path)
    try:
        yield connection
    finally:
        connection.close()


def _timestamp_slug() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d_%I-%M-%S%p")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _safe_name(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in str(value))
    trimmed = cleaned.strip("_")
    return trimmed or "tester"


def _export_owner_slug(profile: Optional[dict[str, Any]]) -> str:
    if not profile:
        return "unknown_user"
    display_name = _safe_name(profile.get("display_name") or "")
    echo_name = _safe_name(profile.get("primary_echo_name") or "")
    parts = [part for part in (display_name, echo_name) if part]
    if not parts:
        return "unknown_user"
    return "_".join(dict.fromkeys(parts))


def _safe_extract_archive(archive: ZipFile, target_dir: Path) -> None:
    for member in archive.infolist():
        member_path = (target_dir / member.filename).resolve()
        if not str(member_path).startswith(str(target_dir.resolve())):
            raise ValueError(f"Unsafe path inside export zip: {member.filename}")
    archive.extractall(target_dir)
