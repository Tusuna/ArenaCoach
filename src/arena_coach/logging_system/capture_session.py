"""Background raw snapshot capture for Arena Coach."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any, Dict, Iterable, Optional

from arena_coach.logging_system.echo_api_client import EchoApiClient, EchoApiError
from arena_coach.models import SessionMetadata


class CaptureSession:
    def __init__(
        self,
        client: EchoApiClient,
        raw_log_dir: Path,
        poll_interval_seconds: float = 0.5,
        max_stored_errors: int = 100,
    ) -> None:
        self.client = client
        self.raw_log_dir = Path(raw_log_dir)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.max_stored_errors = int(max_stored_errors)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._sequence = 0
        self._metadata = SessionMetadata(source=self.client.source_url)
        self.raw_log_path: Optional[Path] = None
        self.metadata_path: Optional[Path] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> SessionMetadata:
        with self._lock:
            if self.is_running:
                return self._metadata

            self.raw_log_dir.mkdir(parents=True, exist_ok=True)
            started_at = _utc_now()
            stamp = _safe_stamp(started_at)
            self.raw_log_path = self.raw_log_dir / f"arena_coach_{stamp}.jsonl"
            self.metadata_path = self.raw_log_dir / f"arena_coach_{stamp}.metadata.json"
            self._metadata = SessionMetadata(
                started_at=started_at,
                source=self.client.source_url,
                raw_log_path=str(self.raw_log_path),
            )
            self._sequence = 0
            self._stop_event.clear()
            self._write_metadata_locked()

            self._thread = threading.Thread(target=self._run, name="ArenaCoachCapture", daemon=True)
            self._thread.start()
            return self._metadata

    def stop(self, timeout: float = 5.0) -> SessionMetadata:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

        with self._lock:
            if self._metadata.stopped_at is None:
                self._metadata.stopped_at = _utc_now()
            self._write_metadata_locked()
            return self._metadata

    def metadata_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self._metadata.to_dict()

    def _run(self) -> None:
        if self.raw_log_path is None:
            self._record_error("Capture session was started without a raw log path", "RuntimeError")
            return

        try:
            with self.raw_log_path.open("a", encoding="utf-8", buffering=1) as raw_file:
                while not self._stop_event.is_set():
                    started = time.perf_counter()
                    try:
                        snapshot = self.client.fetch_snapshot()
                    except EchoApiError as exc:
                        self._record_error(str(exc), type(exc).__name__)
                    else:
                        self._write_snapshot(raw_file, snapshot)

                    elapsed = time.perf_counter() - started
                    sleep_for = max(0.0, self.poll_interval_seconds - elapsed)
                    self._stop_event.wait(sleep_for)
        except OSError as exc:
            self._record_error(f"Could not write raw log: {exc}", type(exc).__name__)

    def _write_snapshot(self, raw_file: Any, snapshot: Dict[str, Any]) -> None:
        captured_at = _utc_now()
        with self._lock:
            self._sequence += 1
            line = {
                "sequence": self._sequence,
                "captured_at": captured_at,
                "source": self.client.source_url,
                "snapshot": snapshot,
            }
            raw_file.write(json.dumps(line, separators=(",", ":"), ensure_ascii=False) + "\n")
            self._metadata.snapshot_count = self._sequence
            self._update_metadata_from_snapshot(snapshot)
            self._write_metadata_locked()

    def _record_error(self, message: str, error_type: str) -> None:
        with self._lock:
            self._metadata.error_count += 1
            if len(self._metadata.errors) < self.max_stored_errors:
                self._metadata.errors.append(
                    {
                        "captured_at": _utc_now(),
                        "sequence": self._sequence,
                        "type": error_type,
                        "message": message,
                    }
                )
            self._write_metadata_locked()

    def _write_metadata_locked(self) -> None:
        if self.metadata_path is None:
            return
        payload = json.dumps(self._metadata.to_dict(), indent=2, ensure_ascii=False) + "\n"
        temp_path = self.metadata_path.with_suffix(self.metadata_path.suffix + ".tmp")
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(self.metadata_path)

    def _update_metadata_from_snapshot(self, snapshot: Dict[str, Any]) -> None:
        session_id = snapshot.get("sessionid")
        if session_id:
            session_id = str(session_id)
            if self._metadata.first_sessionid is None:
                self._metadata.first_sessionid = session_id
            self._metadata.latest_sessionid = session_id

        game_status = snapshot.get("game_status", snapshot.get("game_state"))
        if game_status is not None:
            self._metadata.latest_game_status = str(game_status)

        blue_score = _coerce_int(snapshot.get("blue_points", snapshot.get("blue_score")))
        orange_score = _coerce_int(snapshot.get("orange_points", snapshot.get("orange_score")))
        if blue_score is not None:
            self._metadata.latest_blue_score = blue_score
        if orange_score is not None:
            self._metadata.latest_orange_score = orange_score

        teams = _get_teams(snapshot)
        for index, team in enumerate(teams):
            if not isinstance(team, dict):
                continue
            color = _team_color(index)
            team_name = str(team.get("team") or color.title())
            team_key = f"{index}:{team_name}"
            self._metadata.detected_teams[team_key] = {
                "index": index,
                "team": color,
                "name": team_name,
            }

            players = team.get("players") or []
            if not isinstance(players, list):
                continue
            for player in players:
                if isinstance(player, dict):
                    self._remember_player(player, color, index)

    def _remember_player(self, player: Dict[str, Any], team_color: str, team_index: int) -> None:
        name = player.get("name")
        if not name:
            return

        userid = player.get("userid")
        player_key = str(userid) if userid not in (None, "") else str(name)
        existing = self._metadata.detected_players.setdefault(
            player_key,
            {
                "primary_name": str(name),
                "aliases": [],
                "userid": userid,
                "latest_playerid": player.get("playerid"),
                "number": player.get("number"),
                "level": player.get("level"),
                "observed_teams": [],
            },
        )

        _append_unique(existing["aliases"], str(name))
        _append_unique(existing["observed_teams"], {"team": team_color, "index": team_index})
        existing["primary_name"] = str(name)
        existing["userid"] = userid
        existing["latest_playerid"] = player.get("playerid")
        existing["number"] = player.get("number")
        existing["level"] = player.get("level")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_stamp(value: str) -> str:
    return value.replace("+00:00", "Z").replace(":", "").replace("-", "").replace(".", "_")


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_teams(snapshot: Dict[str, Any]) -> Iterable[Any]:
    teams = snapshot.get("teams")
    if isinstance(teams, list):
        return teams
    processed_teams = snapshot.get("team")
    if isinstance(processed_teams, list):
        return processed_teams
    return []


def _team_color(index: int) -> str:
    if index == 0:
        return "blue"
    if index == 1:
        return "orange"
    return "spectator"


def _append_unique(values: list, value: Any) -> None:
    if value not in values:
        values.append(value)
