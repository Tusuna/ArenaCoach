"""Live Echo API capture service used by the GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from arena_coach.config import AppConfig
from arena_coach.logging_system.capture_session import CaptureSession
from arena_coach.logging_system.echo_api_client import EchoApiClient
from arena_coach.models import ConnectionStatus


class CaptureService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._session: Optional[CaptureSession] = None

    @property
    def is_running(self) -> bool:
        return self._session is not None and self._session.is_running

    def update_config(self, config: AppConfig) -> None:
        if self.is_running:
            raise RuntimeError("Stop capture before changing capture settings.")
        self.config = config

    def test_connection(self) -> ConnectionStatus:
        return self._build_client().test_connection()

    def start(self) -> Dict[str, Any]:
        if self.is_running and self._session is not None:
            return self.status()
        self._session = CaptureSession(
            client=self._build_client(),
            raw_log_dir=self.config.raw_log_dir,
            poll_interval_seconds=self.config.poll_interval_seconds,
        )
        self._session.start()
        return self.status()

    def stop(self) -> Dict[str, Any]:
        if self._session is None:
            return self.status()
        self._session.stop()
        return self.status()

    def status(self) -> Dict[str, Any]:
        if self._session is None:
            return {
                "running": False,
                "snapshot_count": 0,
                "latest_game_status": None,
                "latest_blue_score": None,
                "latest_orange_score": None,
                "raw_log_path": None,
                "detected_players": [],
                "error_count": 0,
            }
        data = self._session.metadata_snapshot()
        data["running"] = self._session.is_running
        return data

    def current_raw_log_path(self) -> Optional[Path]:
        status = self.status()
        raw_log_path = status.get("raw_log_path")
        return Path(raw_log_path) if raw_log_path else None

    def _build_client(self) -> EchoApiClient:
        return EchoApiClient(
            host=self.config.echo_api_host,
            port=self.config.echo_api_port,
            timeout=self.config.request_timeout_seconds,
            path=self.config.echo_api_path,
        )
