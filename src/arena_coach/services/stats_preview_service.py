"""GUI-friendly wrapper around the main stats engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from arena_coach.services.stats_service import DatabaseStatsService
from arena_coach.stats.stat_filters import StatsFilter


class StatsPreviewService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self._service = DatabaseStatsService(self.database_path)

    def preview(self, filters: Optional[StatsFilter] = None) -> Dict[str, Any]:
        return self._service.preview(filters or StatsFilter())

    def summary(self, filters: Optional[StatsFilter] = None) -> Dict[str, Any]:
        payload = self.preview(filters or StatsFilter())
        summary = dict(payload.get("summary") or {})
        quality = payload.get("quality") or {}
        summary["total_finalized_matches"] = summary.get("matches_played", 0)
        summary["recent_matches"] = payload.get("recent_matches") or []
        summary["top_players_by_appearances"] = payload.get("top_players_by_appearances") or []
        summary["guest_unmapped_count"] = payload.get("guest_unmapped_count", 0)
        summary["low_active_match_count"] = int((quality.get("counts") or {}).get("Low Quality", 0))
        summary["quality_summary"] = quality
        summary["trends"] = payload.get("trends") or {}
        summary["matchups"] = payload.get("matchups") or {}
        summary["teammates"] = payload.get("teammates") or {}
        summary["playstyle"] = payload.get("playstyle") or summary.get("playstyle") or {}
        return summary

    def quality_for_match(self, match_id: int) -> Dict[str, Any]:
        return self._service.quality_for_match(match_id)
