"""Local configuration for Arena Coach."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


CONFIG_FILENAME = "arena_coach_config.json"


class ConfigError(RuntimeError):
    """Raised when the local Arena Coach config cannot be loaded."""


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    config_path: Path
    echo_api_host: str
    echo_api_port: int
    echo_api_path: str
    poll_interval_seconds: float
    request_timeout_seconds: float
    raw_log_dir: Path
    database_path: Path
    use_guided_match_review: bool = True

    @property
    def echo_api_url(self) -> str:
        return f"http://{self.echo_api_host}:{self.echo_api_port}{self.echo_api_path}"

    @property
    def exports_dir(self) -> Path:
        return self.project_root / "exports"

    @property
    def imports_dir(self) -> Path:
        return self.project_root / "imports"

    @property
    def backups_dir(self) -> Path:
        return self.project_root / "backups"


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config_data(project_root: Optional[Path] = None) -> Dict[str, Any]:
    root = project_root or get_project_root()
    return {
        "echo_api_host": "127.0.0.1",
        "echo_api_port": 6721,
        "echo_api_path": "/session",
        "poll_interval_seconds": 0.5,
        "request_timeout_seconds": 1.0,
        "raw_log_dir": str(root / "logs" / "raw"),
        "database_path": str(root / "data" / "arena_coach.db"),
        "use_guided_match_review": True,
    }


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    default_project_root = get_project_root()
    resolved_config_path = Path(config_path).resolve() if config_path else default_project_root / CONFIG_FILENAME
    project_root = resolved_config_path.parent if config_path else default_project_root
    defaults = default_config_data(project_root)

    if resolved_config_path.exists():
        try:
            loaded = json.loads(resolved_config_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Config file is not valid JSON: {resolved_config_path}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError(f"Config file must contain a JSON object: {resolved_config_path}")
        data = {**defaults, **loaded}
    else:
        data = defaults
        resolved_config_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    raw_log_dir = _resolve_path(data["raw_log_dir"], project_root, Path("logs") / "raw")
    database_path = _resolve_path(data["database_path"], project_root, Path("data") / "arena_coach.db")

    config = AppConfig(
        project_root=project_root,
        config_path=resolved_config_path,
        echo_api_host=str(data["echo_api_host"]),
        echo_api_port=int(data["echo_api_port"]),
        echo_api_path=_normalize_api_path(data["echo_api_path"]),
        poll_interval_seconds=float(data["poll_interval_seconds"]),
        request_timeout_seconds=float(data["request_timeout_seconds"]),
        raw_log_dir=raw_log_dir,
        database_path=database_path,
        use_guided_match_review=_bool(data.get("use_guided_match_review"), True),
    )

    ensure_runtime_directories(config)
    _write_config(config)
    return config


def save_config_values(config_path: Optional[Path], values: Dict[str, Any]) -> AppConfig:
    default_project_root = get_project_root()
    resolved_config_path = Path(config_path).resolve() if config_path else default_project_root / CONFIG_FILENAME
    project_root = resolved_config_path.parent if config_path else default_project_root
    defaults = default_config_data(project_root)
    data = {**defaults, **values}
    normalized = {
        "echo_api_host": str(data["echo_api_host"]),
        "echo_api_port": int(data["echo_api_port"]),
        "echo_api_path": _normalize_api_path(data["echo_api_path"]),
        "poll_interval_seconds": float(data["poll_interval_seconds"]),
        "request_timeout_seconds": float(data["request_timeout_seconds"]),
        "raw_log_dir": _serialize_path(
            _resolve_path(data["raw_log_dir"], project_root, Path("logs") / "raw"),
            project_root,
        ),
        "database_path": _serialize_path(
            _resolve_path(data["database_path"], project_root, Path("data") / "arena_coach.db"),
            project_root,
        ),
        "use_guided_match_review": _bool(data.get("use_guided_match_review"), True),
    }
    resolved_config_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_config_path.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
    return load_config(resolved_config_path)


def ensure_runtime_directories(config: AppConfig) -> None:
    (config.project_root / "data").mkdir(parents=True, exist_ok=True)
    (config.project_root / "logs").mkdir(parents=True, exist_ok=True)
    config.exports_dir.mkdir(parents=True, exist_ok=True)
    config.imports_dir.mkdir(parents=True, exist_ok=True)
    config.backups_dir.mkdir(parents=True, exist_ok=True)
    config.raw_log_dir.mkdir(parents=True, exist_ok=True)
    config.database_path.parent.mkdir(parents=True, exist_ok=True)


def _resolve_path(value: Any, project_root: Path, default_relative: Path) -> Path:
    default_path = (project_root / default_relative).resolve()
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        return (project_root / path).resolve()

    path = path.resolve(strict=False)
    if _is_relative_to(path, project_root):
        return path
    if path.exists():
        return path

    ancestor = _closest_existing_ancestor(path)
    if ancestor is None:
        return default_path
    if not os.access(ancestor, os.W_OK):
        return default_path
    return path


def _write_config(config: AppConfig) -> None:
    normalized = {
        "echo_api_host": config.echo_api_host,
        "echo_api_port": config.echo_api_port,
        "echo_api_path": config.echo_api_path,
        "poll_interval_seconds": config.poll_interval_seconds,
        "request_timeout_seconds": config.request_timeout_seconds,
        "raw_log_dir": _serialize_path(config.raw_log_dir, config.project_root),
        "database_path": _serialize_path(config.database_path, config.project_root),
        "use_guided_match_review": config.use_guided_match_review,
    }
    config.config_path.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")


def _normalize_api_path(value: Any) -> str:
    path = str(value or "/").strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"true", "1", "yes", "on"}


def _serialize_path(path: Path, project_root: Path) -> str:
    resolved = Path(path).resolve(strict=False)
    if _is_relative_to(resolved, project_root):
        return str(resolved.relative_to(project_root))
    return str(resolved)


def _closest_existing_ancestor(path: Path) -> Optional[Path]:
    current = Path(path)
    while True:
        if current.exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
