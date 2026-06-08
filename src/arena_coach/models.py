"""Shared data models for Arena Coach."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ConnectionStatus:
    ok: bool
    source: str
    status: str
    error: Optional[str] = None
    latency_ms: Optional[float] = None
    snapshot_keys: List[str] = field(default_factory=list)


@dataclass
class SessionMetadata:
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    source: Optional[str] = None
    raw_log_path: Optional[str] = None
    snapshot_count: int = 0
    errors: List[Dict[str, Any]] = field(default_factory=list)
    error_count: int = 0
    first_sessionid: Optional[str] = None
    latest_sessionid: Optional[str] = None
    latest_game_status: Optional[str] = None
    detected_players: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    detected_teams: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    latest_blue_score: Optional[int] = None
    latest_orange_score: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "source": self.source,
            "raw_log_path": self.raw_log_path,
            "snapshot_count": self.snapshot_count,
            "errors": self.errors,
            "error_count": self.error_count,
            "first_sessionid": self.first_sessionid,
            "latest_sessionid": self.latest_sessionid,
            "latest_game_status": self.latest_game_status,
            "detected_players": sorted(
                self.detected_players.values(),
                key=lambda player: (str(player.get("primary_name", "")), str(player.get("userid", ""))),
            ),
            "detected_teams": sorted(
                self.detected_teams.values(),
                key=lambda team: int(team.get("index", 99)),
            ),
            "latest_blue_score": self.latest_blue_score,
            "latest_orange_score": self.latest_orange_score,
        }


@dataclass
class ImportLogResult:
    match_id: int
    raw_log_path: str
    detected_players: List[Dict[str, Any]]
    detected_teams: List[Dict[str, Any]]
    blue_score: Optional[int]
    orange_score: Optional[int]
    event_counts: Dict[str, int]
    blue_round_wins: Optional[int] = None
    orange_round_wins: Optional[int] = None
    total_rounds_played: Optional[int] = None
    points_carry_over: Optional[bool] = None
    finalized: bool = False
    events_saved: int = 0
    match_players_saved: int = 0
    match_player_stats_saved: int = 0
