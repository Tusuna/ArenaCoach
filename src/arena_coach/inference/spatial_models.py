"""Shared spatial and inferred-event models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


Vector3 = tuple[float, float, float]


@dataclass
class DiscState:
    position: Optional[Vector3] = None
    velocity: Optional[Vector3] = None
    forward: Optional[Vector3] = None
    left: Optional[Vector3] = None
    up: Optional[Vector3] = None
    bounce_count: Optional[int] = None


@dataclass
class PlayerState:
    alias: str
    userid: Optional[str]
    playerid: Optional[str]
    team: str
    head_position: Optional[Vector3] = None
    body_position: Optional[Vector3] = None
    left_hand_position: Optional[Vector3] = None
    right_hand_position: Optional[Vector3] = None
    velocity: Optional[Vector3] = None
    holding_left: Optional[str] = None
    holding_right: Optional[str] = None
    possession: bool = False
    stunned: bool = False
    blocking: bool = False
    actor_player_id: Optional[int] = None
    stats: dict[str, float] = field(default_factory=dict)

    @property
    def best_position(self) -> Optional[Vector3]:
        return self.body_position or self.head_position or self.right_hand_position or self.left_hand_position


@dataclass
class SnapshotFrame:
    sequence: int
    captured_at: Optional[str]
    game_clock: Optional[float]
    game_clock_display: Optional[str]
    game_status: str
    blue_score: Optional[int]
    orange_score: Optional[int]
    disc: DiscState
    players: list[PlayerState] = field(default_factory=list)
    top_level_possession: Any = None
    blue_team_possession: bool = False
    orange_team_possession: bool = False


@dataclass
class OrientationModel:
    axis: Optional[str] = None
    blue_side: Optional[str] = None
    orange_side: Optional[str] = None
    confidence: str = "low"
    confidence_score: float = 0.0
    explanation: str = ""

    @property
    def available(self) -> bool:
        return bool(self.axis and self.blue_side and self.orange_side)


@dataclass
class PossessionChain:
    team: Optional[str]
    actor_alias: Optional[str]
    actor_player_id: Optional[int]
    actor_userid: Optional[str]
    start_sequence: int
    end_sequence: int
    start_game_clock: Optional[float]
    end_game_clock: Optional[float]
    start_captured_at: Optional[str]
    end_captured_at: Optional[str]
    previous_actor_alias: Optional[str] = None
    next_actor_alias: Optional[str] = None
    next_team: Optional[str] = None
    terminal_event: Optional[str] = None
    frames: list[SnapshotFrame] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if self.start_captured_at and self.end_captured_at:
            try:
                from datetime import datetime

                start = datetime.fromisoformat(self.start_captured_at)
                end = datetime.fromisoformat(self.end_captured_at)
                return max(0.0, (end - start).total_seconds())
            except ValueError:
                return 0.0
        return 0.0


@dataclass
class AdvancedEvent:
    event_type: str
    actor_alias: Optional[str] = None
    target_alias: Optional[str] = None
    assist_alias: Optional[str] = None
    actor_player_id: Optional[int] = None
    target_player_id: Optional[int] = None
    assist_player_id: Optional[int] = None
    team: Optional[str] = None
    start_sequence: Optional[int] = None
    end_sequence: Optional[int] = None
    start_game_clock: Optional[float] = None
    end_game_clock: Optional[float] = None
    confidence: str = "medium"
    confidence_score: float = 0.5
    directness: str = "inferred"
    value: Optional[float] = None
    explanation: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    event_id: Optional[int] = None
    match_id: Optional[int] = None
