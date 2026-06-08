"""Raw log parse and import helpers."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from arena_coach.log_importer import import_raw_log
from arena_coach.parsing.event_deriver import derive_events
from arena_coach.parsing.raw_log_reader import read_raw_log


class ImportService:
    def __init__(self, database_path: Path, raw_log_dir: Path) -> None:
        self.database_path = Path(database_path)
        self.raw_log_dir = Path(raw_log_dir)

    def recent_raw_logs(self, limit: int = 25) -> List[Path]:
        if not self.raw_log_dir.exists():
            return []
        return sorted(
            self.raw_log_dir.glob("*.jsonl"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:limit]

    def latest_raw_log(self) -> Optional[Path]:
        logs = self.recent_raw_logs(limit=1)
        return logs[0] if logs else None

    def parse_log(self, raw_log_path: Path) -> Dict[str, Any]:
        read_result = read_raw_log(raw_log_path)
        derived = derive_events(read_result.records)
        return {
            "raw_log_path": str(Path(raw_log_path).resolve()),
            "valid_snapshots": read_result.summary.valid_snapshots,
            "invalid_lines": read_result.summary.invalid_lines,
            "first_captured_at": read_result.summary.first_captured_at,
            "last_captured_at": read_result.summary.last_captured_at,
            "detected_sessionid": derived.detected_sessionid,
            "detected_map": derived.detected_map_name,
            "detected_players": derived.detected_player_list(),
            "detected_teams": derived.detected_team_list(),
            "blue_score": derived.latest_blue_score,
            "orange_score": derived.latest_orange_score,
            "event_count": len(derived.events),
            "event_counts": dict(sorted(Counter(event.event_type for event in derived.events).items())),
        }

    def import_log(self, raw_log_path: Path) -> Dict[str, Any]:
        result = import_raw_log(raw_log_path, self.database_path)
        return {
            "match_id": result.match_id,
            "raw_log_path": result.raw_log_path,
            "detected_players": result.detected_players,
            "detected_teams": result.detected_teams,
            "blue_score": result.blue_score,
            "orange_score": result.orange_score,
            "event_counts": result.event_counts,
            "events_saved": result.events_saved,
            "match_players_saved": result.match_players_saved,
            "match_player_stats_saved": result.match_player_stats_saved,
            "finalized": result.finalized,
        }
