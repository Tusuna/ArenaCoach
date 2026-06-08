"""Settings validation and persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from arena_coach.config import AppConfig, save_config_values


class SettingsService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def current_values(self) -> Dict[str, Any]:
        return {
            "echo_api_host": self.config.echo_api_host,
            "echo_api_port": self.config.echo_api_port,
            "echo_api_path": self.config.echo_api_path,
            "poll_interval_seconds": self.config.poll_interval_seconds,
            "request_timeout_seconds": self.config.request_timeout_seconds,
            "raw_log_dir": str(self.config.raw_log_dir),
            "database_path": str(self.config.database_path),
            "use_guided_match_review": self.config.use_guided_match_review,
        }

    def validate(self, values: Dict[str, Any]) -> Dict[str, Any]:
        host = str(values.get("echo_api_host") or "").strip()
        if not host:
            raise ValueError("Echo API host is required.")
        port = int(values.get("echo_api_port"))
        if port <= 0 or port > 65535:
            raise ValueError("Echo API port must be between 1 and 65535.")
        poll_interval = float(values.get("poll_interval_seconds"))
        if poll_interval <= 0:
            raise ValueError("Poll interval must be greater than zero.")
        timeout = float(values.get("request_timeout_seconds"))
        if timeout <= 0:
            raise ValueError("Request timeout must be greater than zero.")
        raw_log_dir = Path(str(values.get("raw_log_dir") or "")).expanduser()
        if not str(raw_log_dir):
            raise ValueError("Raw log directory is required.")
        database_path = Path(str(values.get("database_path") or "")).expanduser()
        if not str(database_path):
            raise ValueError("Database path is required.")
        path = str(values.get("echo_api_path") or "/").strip()
        if not path.startswith("/"):
            path = f"/{path}"
        return {
            "echo_api_host": host,
            "echo_api_port": port,
            "echo_api_path": path,
            "poll_interval_seconds": poll_interval,
            "request_timeout_seconds": timeout,
            "raw_log_dir": str(raw_log_dir),
            "database_path": str(database_path),
            "use_guided_match_review": _bool(values.get("use_guided_match_review"), True),
        }

    def save(self, values: Dict[str, Any], config_path: Optional[Path] = None) -> AppConfig:
        normalized = self.validate(values)
        self.config = save_config_values(config_path or self.config.config_path, normalized)
        return self.config


SETTINGS_HELP = {
    "general": "Do not change these unless you know what you are doing.",
    "use_guided_match_review": "Recommended. Walks through each detected player one at a time.",
    "echo_api_host": "Usually 127.0.0.1. The computer where Echo VR API is running.",
    "echo_api_port": "Usually 6721.",
    "echo_api_path": "Usually /session.",
    "poll_interval_seconds": "How often Arena Coach asks Echo for live data. Lower is more frequent.",
    "request_timeout_seconds": "How long Arena Coach waits before treating Echo as unavailable.",
    "raw_log_dir": "Where captured match logs are saved.",
    "database_path": "Where Arena Coach stores profiles, matches, players, and stats.",
}


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"true", "1", "yes", "on"}
