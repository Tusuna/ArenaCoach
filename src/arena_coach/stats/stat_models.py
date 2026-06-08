"""Internal stats engine models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from arena_coach.match_context import has_meaningful_participation


@dataclass
class MatchParticipant:
    match_id: int
    match_alias: str
    canonical_name: Optional[str]
    player_id: Optional[int]
    userid: Optional[str]
    team: Optional[str]
    is_user: bool
    confirmed: bool
    participant_key: Optional[str] = None
    team_row_key: Optional[str] = None
    stats: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    afk_suspected: bool = False
    afk_confidence: float = 0.0
    afk_reasons: List[str] = field(default_factory=list)
    live_samples: int = 0
    activity_total: float = 0.0
    meaningful_participation: bool = False

    def __post_init__(self) -> None:
        if not self.meaningful_participation:
            self.meaningful_participation = has_meaningful_participation(self.stats, self.metadata)

    @property
    def display_name(self) -> str:
        if self.player_id is not None and self.canonical_name:
            return self.canonical_name
        suffix = "guest" if self.confirmed else "unmapped"
        return f"{self.match_alias} ({suffix})"


@dataclass
class MatchQuality:
    active_non_afk_player_count: int
    suspected_afk_count: int
    mapped_player_count: int
    guest_player_count: int
    has_self: bool
    match_classification: str
    is_low_quality: bool
    quality_reasons: List[str]
    quality_label: str
    competitive_eligible: bool
    team_switch_affected: bool = False


@dataclass
class LoadedMatch:
    id: int
    started_at: Optional[str]
    ended_at: Optional[str]
    created_at: Optional[str]
    display_name: str
    match_classification: str
    match_type: Optional[str]
    map_name: Optional[str]
    blue_score: Optional[int]
    orange_score: Optional[int]
    user_profile_id: Optional[int] = None
    blue_round_wins: int = 0
    orange_round_wins: int = 0
    total_rounds_played: int = 0
    round_summary: List[Dict[str, Any]] = field(default_factory=list)
    points_carry_over: Optional[bool] = None
    user_team: Optional[str] = None
    result: Optional[str] = None
    raw_log_path: Optional[str] = None
    private_match_type: Optional[str] = None
    finalized: bool = False
    participants: List[MatchParticipant] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    quality: Optional[MatchQuality] = None

    def self_participant(self) -> Optional[MatchParticipant]:
        return next((participant for participant in self.participants if participant.is_user), None)

    def self_participants(self) -> List[MatchParticipant]:
        return [participant for participant in self.participants if participant.is_user]


@dataclass
class AggregateSlice:
    matches_played: int
    wins: int
    losses: int
    ties: int
    win_rate: float
    totals: Dict[str, float]
    averages: Dict[str, float]
    shot_efficiency: float
    match_ids: List[int]


@dataclass
class TrendMetric:
    stat_name: str
    last_average: float
    previous_average: float
    delta: float
    direction: str


@dataclass
class MatchupSummary:
    entity_key: str
    player_id: Optional[int]
    display_name: str
    is_guest: bool
    matches_against: int
    wins_against: int
    losses_against: int
    ties_against: int
    win_rate_against: float
    user_totals: Dict[str, float]
    opponent_totals: Dict[str, float]
    differentials: Dict[str, float]
    direct_stuns_against_user: int
    direct_steals_against_user: int
    opponent_context_notes: List[str]


@dataclass
class TeammateSummary:
    entity_key: str
    player_id: Optional[int]
    display_name: str
    is_guest: bool
    matches_together: int
    wins_together: int
    losses_together: int
    ties_together: int
    win_rate_together: float
    user_averages: Dict[str, float]
    teammate_averages: Dict[str, float]
    team_score_average: float
    teammate_afk_count: int
    low_quality_match_count: int
    confidence: str


@dataclass
class PlaystyleResult:
    label: str
    explanation: str
    sample_size: int
    weights: Dict[str, float]
