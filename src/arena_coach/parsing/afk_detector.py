"""Conservative AFK detection from raw Echo Arena snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, Iterable, Optional

from arena_coach.parsing import snapshot_parser as sp
from arena_coach.parsing.raw_log_reader import RawSnapshotRecord


LIVE_STATUSES = {"round_start", "playing", "score", "sudden_death"}
ACTIVITY_STAT_KEYS = (
    "points",
    "saves",
    "goals",
    "stuns",
    "passes",
    "catches",
    "steals",
    "blocks",
    "interceptions",
    "assists",
    "shots_taken",
)


@dataclass
class PlayerAfkTrack:
    key: str
    name: Optional[str] = None
    userid: Optional[str] = None
    playerid: Optional[str] = None
    team: Optional[str] = None
    live_samples: int = 0
    first_sequence: Optional[int] = None
    last_sequence: Optional[int] = None
    velocity_sum: float = 0.0
    body_distance: float = 0.0
    head_distance: float = 0.0
    last_body_position: Optional[list[float]] = None
    last_head_position: Optional[list[float]] = None
    stats: Dict[str, float] = field(default_factory=dict)

    def add_player(self, record: RawSnapshotRecord, player: Dict[str, Any]) -> None:
        self.name = player.get("name") or self.name
        self.userid = player.get("userid") or self.userid
        self.playerid = player.get("playerid") or self.playerid
        self.team = player.get("team") or self.team
        self.live_samples += 1
        self.first_sequence = record.sequence if self.first_sequence is None else self.first_sequence
        self.last_sequence = record.sequence
        self.velocity_sum += _vector_magnitude(player.get("velocity"))
        self.body_distance += self._position_delta(player.get("body"), "last_body_position")
        self.head_distance += self._position_delta(player.get("head"), "last_head_position")
        for key, value in (player.get("stats") or {}).items():
            numeric = _float(value)
            if numeric is not None:
                self.stats[key] = max(self.stats.get(key, 0.0), numeric)

    def _position_delta(self, transform: Any, last_attr: str) -> float:
        position = _position(transform)
        if position is None:
            return 0.0
        previous = getattr(self, last_attr)
        setattr(self, last_attr, position)
        if previous is None:
            return 0.0
        return _distance(previous, position)

    def assessment(
        self,
        minimum_live_samples: int,
        no_stats_possession_threshold: float,
        still_distance_threshold: float,
        still_velocity_threshold: float,
    ) -> Dict[str, Any]:
        activity_total = sum(self.stats.get(key, 0.0) for key in ACTIVITY_STAT_KEYS)
        possession_time = self.stats.get("possession_time", 0.0)
        average_velocity = self.velocity_sum / self.live_samples if self.live_samples else 0.0
        reasons = []
        suspected = False
        confidence = 0.0

        enough_samples = self.live_samples >= minimum_live_samples
        no_stats = activity_total <= 0 and possession_time <= no_stats_possession_threshold
        very_still = self.body_distance <= still_distance_threshold and average_velocity <= still_velocity_threshold

        if self.team == "spectator":
            reasons.append("spectator_not_evaluated")
        elif not enough_samples:
            reasons.append("insufficient_live_samples")
        elif no_stats and very_still:
            suspected = True
            confidence = 0.95
            reasons.extend(["no_stats_or_possession", "minimal_movement"])
        elif no_stats:
            suspected = True
            confidence = 0.75
            reasons.append("no_stats_or_possession")
        elif very_still:
            suspected = True
            confidence = 0.65
            reasons.append("minimal_movement")
        else:
            reasons.append("activity_detected")

        return {
            "suspected": suspected,
            "confidence": confidence,
            "reasons": reasons,
            "name": self.name,
            "live_samples": self.live_samples,
            "first_sequence": self.first_sequence,
            "last_sequence": self.last_sequence,
            "activity_total": activity_total,
            "possession_time": possession_time,
            "body_distance": round(self.body_distance, 3),
            "head_distance": round(self.head_distance, 3),
            "average_velocity": round(average_velocity, 3),
            "team": self.team,
            "userid": self.userid,
            "playerid": self.playerid,
        }


def detect_afk_players(
    records: Iterable[RawSnapshotRecord],
    *,
    minimum_live_samples: int = 120,
    no_stats_possession_threshold: float = 1.0,
    still_distance_threshold: float = 3.0,
    still_velocity_threshold: float = 0.2,
) -> Dict[str, Dict[str, Any]]:
    tracks: Dict[str, PlayerAfkTrack] = {}
    for record in records:
        if sp.game_status(record.snapshot) not in LIVE_STATUSES:
            continue
        for key, player in sp.player_lookup(record.snapshot).items():
            track = tracks.setdefault(key, PlayerAfkTrack(key=key))
            track.add_player(record, player)

    return {
        key: track.assessment(
            minimum_live_samples=minimum_live_samples,
            no_stats_possession_threshold=no_stats_possession_threshold,
            still_distance_threshold=still_distance_threshold,
            still_velocity_threshold=still_velocity_threshold,
        )
        for key, track in tracks.items()
    }


def _position(transform: Any) -> Optional[list[float]]:
    if not isinstance(transform, dict):
        return None
    value = transform.get("position")
    if not isinstance(value, list) or len(value) < 3:
        return None
    try:
        return [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError):
        return None


def _vector_magnitude(value: Any) -> float:
    if not isinstance(value, list) or len(value) < 3:
        return 0.0
    try:
        return math.sqrt(sum(float(component) ** 2 for component in value[:3]))
    except (TypeError, ValueError):
        return 0.0


def _distance(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((right[index] - left[index]) ** 2 for index in range(3)))


def _float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
