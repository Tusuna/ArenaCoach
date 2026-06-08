"""Normalized event model for parsed Arena Coach logs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


SUPPORTED_EVENT_TYPES = {
    "match_start",
    "match_end",
    "score_update",
    "goal",
    "assist",
    "save",
    "stun",
    "steal",
    "pass",
    "catch",
    "shot",
    "interception",
    "block",
    "possession_change",
    "player_join",
    "player_leave",
    "unknown",
}


@dataclass
class NormalizedEvent:
    event_type: str
    sequence: Optional[int] = None
    captured_at: Optional[str] = None
    game_clock: Optional[float] = None
    game_clock_display: Optional[str] = None
    event_id: Optional[int] = None
    match_id: Optional[int] = None
    actor_name: Optional[str] = None
    target_name: Optional[str] = None
    assist_name: Optional[str] = None
    actor_userid: Optional[str] = None
    target_userid: Optional[str] = None
    assist_userid: Optional[str] = None
    actor_playerid: Optional[str] = None
    target_playerid: Optional[str] = None
    assist_playerid: Optional[str] = None
    team: Optional[str] = None
    value: Optional[float] = None
    raw_text: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.event_type not in SUPPORTED_EVENT_TYPES:
            self.metadata.setdefault("original_event_type", self.event_type)
            self.event_type = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "match_id": self.match_id,
            "sequence": self.sequence,
            "captured_at": self.captured_at,
            "game_clock": self.game_clock,
            "game_clock_display": self.game_clock_display,
            "event_type": self.event_type,
            "actor_name": self.actor_name,
            "target_name": self.target_name,
            "assist_name": self.assist_name,
            "actor_userid": self.actor_userid,
            "target_userid": self.target_userid,
            "assist_userid": self.assist_userid,
            "actor_playerid": self.actor_playerid,
            "target_playerid": self.target_playerid,
            "assist_playerid": self.assist_playerid,
            "team": self.team,
            "value": self.value,
            "raw_text": self.raw_text,
            "metadata": self.metadata,
        }
