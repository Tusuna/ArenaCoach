"""Observer-inspired per-player advanced metric aggregation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from arena_coach.parsing.normalized_event import NormalizedEvent

from .inference_config import InferenceConfig
from .spatial_math import distance_3d, infer_team_side
from .spatial_models import OrientationModel, PlayerState, PossessionChain, SnapshotFrame


LIVE_SAMPLE_STATUSES = {"round_start", "playing", "score", "sudden_death"}
ACTIVE_ACTIVITY_STAT_KEYS = (
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
    "possession_time",
)
INACTIVE_STREAK_THRESHOLD_SECONDS = 4.0
ACTIVE_MOVEMENT_METERS = 0.75
ACTIVE_VELOCITY_MPS = 0.35


@dataclass
class PlayerMetricAccumulator:
    match_id: int
    player_id: Optional[int]
    match_alias: str
    userid: Optional[str]
    team: str
    completed_passes: int = 0
    inferred_catches: int = 0
    initiators: int = 0
    open_for_pass_samples: int = 0
    lane_blocked_samples: int = 0
    lane_blocks: int = 0
    tight_man_coverage_samples: int = 0
    loose_man_coverage_samples: int = 0
    no_man_coverage_samples: int = 0
    goalie_coverage_samples: int = 0
    clear_attempts: int = 0
    successful_clears: int = 0
    failed_clears: int = 0
    inferred_turnovers: int = 0
    inferred_interceptions: int = 0
    steal_takeaways: int = 0
    stun_takeaways: int = 0
    missed_shots: int = 0
    shots_saved_against: int = 0
    blocked_shots: int = 0
    stuffed_shots: int = 0
    offensive_transition_count: int = 0
    offensive_transition_total: float = 0.0
    defensive_transition_count: int = 0
    defensive_transition_total: float = 0.0
    goals_2_open_net: int = 0
    goals_2_guarded: int = 0
    goals_3_open_net: int = 0
    goals_3_guarded: int = 0
    active_clock_seconds: float = 0.0
    active_rounds_estimated: float = 0.0
    round_length_seconds_estimated: float = 600.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "player_id": self.player_id,
            "match_alias": self.match_alias,
            "userid": self.userid,
            "team": self.team,
            "completed_passes": self.completed_passes,
            "inferred_catches": self.inferred_catches,
            "initiators": self.initiators,
            "open_for_pass_samples": self.open_for_pass_samples,
            "lane_blocked_samples": self.lane_blocked_samples,
            "lane_blocks": self.lane_blocks,
            "tight_man_coverage_samples": self.tight_man_coverage_samples,
            "loose_man_coverage_samples": self.loose_man_coverage_samples,
            "no_man_coverage_samples": self.no_man_coverage_samples,
            "goalie_coverage_samples": self.goalie_coverage_samples,
            "clear_attempts": self.clear_attempts,
            "successful_clears": self.successful_clears,
            "failed_clears": self.failed_clears,
            "inferred_turnovers": self.inferred_turnovers,
            "inferred_interceptions": self.inferred_interceptions,
            "steal_takeaways": self.steal_takeaways,
            "stun_takeaways": self.stun_takeaways,
            "missed_shots": self.missed_shots,
            "shots_saved_against": self.shots_saved_against,
            "blocked_shots": self.blocked_shots,
            "stuffed_shots": self.stuffed_shots,
            "offensive_transition_count": self.offensive_transition_count,
            "offensive_transition_total": round(self.offensive_transition_total, 6),
            "defensive_transition_count": self.defensive_transition_count,
            "defensive_transition_total": round(self.defensive_transition_total, 6),
            "goals_2_open_net": self.goals_2_open_net,
            "goals_2_guarded": self.goals_2_guarded,
            "goals_3_open_net": self.goals_3_open_net,
            "goals_3_guarded": self.goals_3_guarded,
            "metadata": self._metadata_payload(),
        }

    def _metadata_payload(self) -> dict[str, Any]:
        open_total = self.open_for_pass_samples + self.lane_blocked_samples
        shot_denominator = self.missed_shots + self.shots_saved_against
        return {
            **self.metadata,
            "average_time_to_offense": (
                round(self.offensive_transition_total / self.offensive_transition_count, 3)
                if self.offensive_transition_count
                else None
            ),
            "average_time_to_defense": (
                round(self.defensive_transition_total / self.defensive_transition_count, 3)
                if self.defensive_transition_count
                else None
            ),
            "open_for_pass_rate": round(self.open_for_pass_samples / open_total, 3) if open_total else None,
            "shooting_percentage": (
                round(
                    (
                        float(
                            self.goals_2_open_net
                            + self.goals_2_guarded
                            + self.goals_3_open_net
                            + self.goals_3_guarded
                        )
                        / float(shot_denominator)
                    )
                    * 100.0,
                    3,
                )
                if shot_denominator
                else None
            ),
            "active_seconds_observed": round(self.active_clock_seconds, 3),
            "active_rounds_estimated": round(self.active_rounds_estimated, 3),
            "round_length_seconds_estimated": round(self.round_length_seconds_estimated, 3),
            "inactive_seconds_observed": round(float(self.metadata.get("inactive_seconds_observed") or 0.0), 3),
            "movement_distance_observed": round(float(self.metadata.get("movement_distance_observed") or 0.0), 3),
            "active_signal_samples": int(self.metadata.get("active_signal_samples") or 0),
            "inactive_streak_threshold_seconds": INACTIVE_STREAK_THRESHOLD_SECONDS,
        }


def build_player_metrics(
    match_id: int,
    match_stat_rows: list[Any],
    frames: list[SnapshotFrame],
    chains: list[PossessionChain],
    base_events: list[NormalizedEvent],
    advanced_rows: list[Any],
    orientation: OrientationModel,
    config: InferenceConfig,
    *,
    match_classification: Optional[str] = None,
) -> list[dict[str, Any]]:
    accumulators = _initial_accumulators(match_id, match_stat_rows)
    frames_by_sequence = {int(frame.sequence): frame for frame in frames}

    _apply_pass_reconstruction(accumulators, match_id, chains, base_events, orientation, config)
    _apply_frame_coverage_samples(accumulators, match_id, frames, orientation, config)
    _apply_goal_context(accumulators, match_id, base_events, frames_by_sequence, orientation, config)
    _apply_advanced_events(accumulators, match_id, advanced_rows, base_events, config)
    _apply_active_round_estimates(accumulators, match_id, frames, match_classification)

    return [accumulator.to_record() for accumulator in accumulators.values()]


def _apply_active_round_estimates(
    accumulators: dict[str, PlayerMetricAccumulator],
    match_id: int,
    frames: list[SnapshotFrame],
    match_classification: Optional[str],
) -> None:
    round_length_seconds = _estimated_round_length_seconds(frames, match_classification)
    trackers: dict[str, dict[str, Any]] = {}
    for frame in frames:
        if frame.game_status not in LIVE_SAMPLE_STATUSES:
            continue
        current_time = _parse_captured_at(frame.captured_at)
        for player in frame.players:
            team = str(player.team or "").casefold()
            if team not in {"blue", "orange"}:
                continue
            accumulator = _ensure_accumulator(
                accumulators,
                player.alias,
                player.team,
                player.actor_player_id,
                player.userid,
                match_id,
            )
            key = _metric_key(accumulator.match_alias, accumulator.team)
            tracker = trackers.setdefault(
                key,
                {
                    "active_seconds": 0.0,
                    "inactive_seconds": 0.0,
                    "sample_count": 0,
                    "last_clock": None,
                    "last_time": None,
                    "last_position": None,
                    "last_velocity": 0.0,
                    "last_stats": {},
                    "last_possession": False,
                    "inactive_streak": 0.0,
                    "movement_distance": 0.0,
                    "active_signal_samples": 0,
                },
            )
            tracker["sample_count"] += 1
            clock = _normalized_clock_value(frame.game_clock, round_length_seconds)
            previous_clock = tracker["last_clock"]
            delta = _player_sample_delta_seconds(
                previous_clock,
                clock,
                tracker["last_time"],
                current_time,
                round_length_seconds,
            )
            movement_distance = _movement_distance(tracker["last_position"], player.best_position)
            tracker["movement_distance"] += movement_distance
            current_velocity = _vector_speed(player.velocity)
            stat_changed = _stats_changed(tracker["last_stats"], player.stats)
            possession_signal = bool(
                player.possession
                or tracker["last_possession"]
                or _holding_disc(player.holding_left)
                or _holding_disc(player.holding_right)
            )
            activity_signal = bool(
                stat_changed
                or possession_signal
                or movement_distance >= ACTIVE_MOVEMENT_METERS
                or current_velocity >= ACTIVE_VELOCITY_MPS
            )
            if delta > 0.0:
                if activity_signal:
                    tracker["active_signal_samples"] += 1
                    inactive_streak = float(tracker["inactive_streak"] or 0.0)
                    if 0.0 < inactive_streak <= INACTIVE_STREAK_THRESHOLD_SECONDS:
                        tracker["active_seconds"] += inactive_streak
                    elif inactive_streak > INACTIVE_STREAK_THRESHOLD_SECONDS:
                        tracker["inactive_seconds"] += inactive_streak
                    tracker["inactive_streak"] = 0.0
                    tracker["active_seconds"] += delta
                else:
                    tracker["inactive_streak"] += delta
            tracker["last_clock"] = clock if clock is not None else tracker["last_clock"]
            tracker["last_time"] = current_time
            tracker["last_position"] = player.best_position
            tracker["last_velocity"] = current_velocity
            tracker["last_stats"] = dict(player.stats or {})
            tracker["last_possession"] = bool(player.possession)

    for key, accumulator in accumulators.items():
        tracker = trackers.get(key)
        active_seconds = 0.0
        inactive_seconds = 0.0
        sample_count = 0
        if tracker is not None:
            active_seconds = float(tracker.get("active_seconds") or 0.0)
            inactive_seconds = float(tracker.get("inactive_seconds") or 0.0)
            sample_count = int(tracker.get("sample_count") or 0)
            trailing_inactive = float(tracker.get("inactive_streak") or 0.0)
            if 0.0 < trailing_inactive <= INACTIVE_STREAK_THRESHOLD_SECONDS:
                active_seconds += trailing_inactive
            else:
                inactive_seconds += trailing_inactive
            if active_seconds <= 0.0 and sample_count > 1 and float(tracker.get("movement_distance") or 0.0) > 0.0:
                active_seconds = min((sample_count - 1) * 0.5, round_length_seconds)
        accumulator.active_clock_seconds = round(active_seconds, 6)
        accumulator.round_length_seconds_estimated = round_length_seconds
        accumulator.active_rounds_estimated = (
            min(float(active_seconds) / float(round_length_seconds), 1.0e9)
            if round_length_seconds > 0.0 and active_seconds > 0.0
            else 0.0
        )
        accumulator.metadata["inactive_seconds_observed"] = round(inactive_seconds, 6)
        accumulator.metadata["movement_distance_observed"] = round(float(tracker.get("movement_distance") or 0.0), 6) if tracker is not None else 0.0
        accumulator.metadata["active_signal_samples"] = int(tracker.get("active_signal_samples") or 0) if tracker is not None else 0


def _estimated_round_length_seconds(
    frames: list[SnapshotFrame],
    match_classification: Optional[str],
) -> float:
    classification = str(match_classification or "").strip().casefold()
    default_round_length = 300.0 if classification == "public" else 600.0
    observed_max = 0.0
    for frame in frames:
        if frame.game_status not in LIVE_SAMPLE_STATUSES:
            continue
        clock = _optional_float(frame.game_clock)
        if clock is None or clock <= 0.0:
            continue
        observed_max = max(observed_max, float(clock))
    if observed_max >= default_round_length * 1.1:
        return observed_max
    return default_round_length


def _normalized_clock_value(value: Optional[float], round_length_seconds: float) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0.0:
        return None
    if round_length_seconds > 0.0:
        numeric = min(numeric, round_length_seconds)
    return numeric


def _parse_captured_at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _player_sample_delta_seconds(
    previous_clock: Optional[float],
    current_clock: Optional[float],
    previous_time: Optional[datetime],
    current_time: Optional[datetime],
    round_length_seconds: float,
) -> float:
    if previous_clock is not None and current_clock is not None:
        delta = float(previous_clock) - float(current_clock)
        if 0.0 <= delta <= round_length_seconds:
            return delta
    if previous_time is not None and current_time is not None:
        elapsed = (current_time - previous_time).total_seconds()
        if 0.0 <= elapsed <= 10.0:
            return float(elapsed)
    return 0.0


def _movement_distance(previous_position: Optional[tuple[float, float, float]], current_position: Optional[tuple[float, float, float]]) -> float:
    distance = distance_3d(previous_position, current_position)
    return float(distance or 0.0)


def _vector_speed(vector: Optional[tuple[float, float, float]]) -> float:
    if vector is None:
        return 0.0
    x_value, y_value, z_value = vector
    return ((float(x_value) ** 2) + (float(y_value) ** 2) + (float(z_value) ** 2)) ** 0.5


def _holding_disc(value: Optional[str]) -> bool:
    return str(value or "").strip().casefold() == "disc"


def _stats_changed(previous_stats: dict[str, float], current_stats: dict[str, float]) -> bool:
    for key in ACTIVE_ACTIVITY_STAT_KEYS:
        if float(current_stats.get(key) or 0.0) > float(previous_stats.get(key) or 0.0):
            return True
    return False


def _initial_accumulators(match_id: int, rows: list[Any]) -> dict[str, PlayerMetricAccumulator]:
    accumulators: dict[str, PlayerMetricAccumulator] = {}
    for row in rows:
        alias = str(row["match_alias"] or "")
        team = str(row["team"] or "unknown")
        key = _metric_key(alias, team)
        accumulators[key] = PlayerMetricAccumulator(
            match_id=match_id,
            player_id=_optional_int(row["player_id"]),
            match_alias=alias,
            userid=_optional_str(row["userid"]),
            team=team,
            metadata={
                "passes_to_open_receiver": 0,
                "passes_to_covered_receiver": 0,
                "catches_open": 0,
                "catches_covered": 0,
                "self_goals": 0,
                "lane_coverage_failures": 0,
                "shooter_uncovered": 0,
                "dunk_like_open_2s": 0,
                "dunk_like_guarded_2s": 0,
            },
        )
    return accumulators


def _apply_pass_reconstruction(
    accumulators: dict[str, PlayerMetricAccumulator],
    match_id: int,
    chains: list[PossessionChain],
    base_events: list[NormalizedEvent],
    orientation: OrientationModel,
    config: InferenceConfig,
) -> None:
    for index, chain in enumerate(chains[:-1]):
        next_chain = chains[index + 1]
        if not chain.team or chain.team != next_chain.team:
            continue
        if not chain.actor_alias or not next_chain.actor_alias:
            continue
        if chain.actor_alias.casefold() == next_chain.actor_alias.casefold():
            continue
        relevant = _events_between(base_events, chain.end_sequence, next_chain.start_sequence)
        if any(event.event_type in {"goal", "save", "shot"} for event in relevant):
            continue
        passer = _ensure_accumulator(
            accumulators,
            chain.actor_alias,
            chain.team,
            chain.actor_player_id,
            chain.actor_userid,
            match_id,
        )
        receiver = _ensure_accumulator(
            accumulators,
            next_chain.actor_alias,
            next_chain.team,
            next_chain.actor_player_id,
            next_chain.actor_userid,
            match_id,
        )
        passer.completed_passes += 1
        receiver.inferred_catches += 1

        frame = chain.frames[-1] if chain.frames else None
        if frame is None:
            continue
        snapshot = _coverage_snapshot(frame, chain.team, chain.actor_alias, orientation, config)
        receiver_status = snapshot.receiver_states.get(next_chain.actor_alias.casefold())
        if receiver_status == "open":
            passer.metadata["passes_to_open_receiver"] = int(passer.metadata.get("passes_to_open_receiver") or 0) + 1
            receiver.metadata["catches_open"] = int(receiver.metadata.get("catches_open") or 0) + 1
        elif receiver_status in {"covered", "lane_blocked"}:
            passer.metadata["passes_to_covered_receiver"] = int(passer.metadata.get("passes_to_covered_receiver") or 0) + 1
            receiver.metadata["catches_covered"] = int(receiver.metadata.get("catches_covered") or 0) + 1


def _apply_frame_coverage_samples(
    accumulators: dict[str, PlayerMetricAccumulator],
    match_id: int,
    frames: list[SnapshotFrame],
    orientation: OrientationModel,
    config: InferenceConfig,
) -> None:
    for frame in frames:
        if frame.game_status not in LIVE_SAMPLE_STATUSES:
            continue
        possessor = _frame_possessor(frame)
        if possessor is None:
            continue
        snapshot = _coverage_snapshot(frame, possessor.team, possessor.alias, orientation, config)
        for alias_folded, status in snapshot.receiver_states.items():
            receiver = snapshot.offense_by_alias.get(alias_folded)
            if receiver is None:
                continue
            accumulator = _ensure_accumulator(
                accumulators,
                receiver.alias,
                receiver.team,
                receiver.actor_player_id,
                receiver.userid,
                match_id,
            )
            if status == "open":
                accumulator.open_for_pass_samples += 1
            elif status == "lane_blocked":
                accumulator.lane_blocked_samples += 1
        for alias_folded, role in snapshot.defender_roles.items():
            defender = snapshot.defense_by_alias.get(alias_folded)
            if defender is None:
                continue
            accumulator = _ensure_accumulator(
                accumulators,
                defender.alias,
                defender.team,
                defender.actor_player_id,
                defender.userid,
                match_id,
            )
            if role == "goalie":
                accumulator.goalie_coverage_samples += 1
            elif role == "tight":
                accumulator.tight_man_coverage_samples += 1
            elif role == "loose":
                accumulator.loose_man_coverage_samples += 1
            elif role == "lane_block":
                accumulator.lane_blocks += 1
            elif role == "no_man":
                accumulator.no_man_coverage_samples += 1


def _apply_goal_context(
    accumulators: dict[str, PlayerMetricAccumulator],
    match_id: int,
    base_events: list[NormalizedEvent],
    frames_by_sequence: dict[int, SnapshotFrame],
    orientation: OrientationModel,
    config: InferenceConfig,
) -> None:
    for event in base_events:
        if event.event_type != "goal" or not event.actor_name or not event.team:
            continue
        accumulator = _ensure_accumulator(accumulators, event.actor_name, event.team, None, event.actor_userid, match_id)
        goal_type = str((event.metadata or {}).get("goal_type") or "").casefold()
        if "self goal" in goal_type:
            accumulator.metadata["self_goals"] = int(accumulator.metadata.get("self_goals") or 0) + 1
            continue
        frame = frames_by_sequence.get(int(event.sequence)) if event.sequence is not None else None
        guarded = _goalie_present(frame, _opponent_team(event.team), orientation, config)
        points = int(event.value or 0)
        if points >= 3:
            if guarded:
                accumulator.goals_3_guarded += 1
            else:
                accumulator.goals_3_open_net += 1
        elif points >= 2:
            if guarded:
                accumulator.goals_2_guarded += 1
            else:
                accumulator.goals_2_open_net += 1
            if _is_dunk_like_goal(goal_type):
                metadata_key = "dunk_like_guarded_2s" if guarded else "dunk_like_open_2s"
                accumulator.metadata[metadata_key] = int(accumulator.metadata.get(metadata_key) or 0) + 1


def _apply_advanced_events(
    accumulators: dict[str, PlayerMetricAccumulator],
    match_id: int,
    advanced_rows: list[Any],
    base_events: list[NormalizedEvent],
    config: InferenceConfig,
) -> None:
    stun_index = _stun_index(base_events)
    seen_turnover_events: set[tuple[Any, ...]] = set()
    for row in advanced_rows:
        event_type = str(_value(row, "event_type") or "")
        team = str(_value(row, "team") or "")
        actor_alias = _optional_str(_value(row, "actor_alias"))
        target_alias = _optional_str(_value(row, "target_alias"))
        actor_player_id = _optional_int(_value(row, "actor_player_id"))
        target_player_id = _optional_int(_value(row, "target_player_id"))
        actor = (
            _ensure_accumulator(accumulators, actor_alias, team, actor_player_id, None, match_id)
            if actor_alias and team
            else None
        )
        target_team = _opponent_team(team)
        target = (
            _ensure_accumulator(accumulators, target_alias, target_team, target_player_id, None, match_id)
            if target_alias and target_team
            else None
        )
        if event_type == "initiator" and actor is not None:
            actor.initiators += 1
        elif event_type == "clear" and actor is not None:
            actor.clear_attempts += 1
            outcome = str((_evidence(row).get("outcome") or "")).casefold()
            if outcome == "successful_clear":
                actor.successful_clears += 1
            elif outcome == "failed_clear":
                actor.failed_clears += 1
        elif event_type in {"turnover", "intercepted_pass"}:
            turnover_key = _turnover_event_key(row)
            if turnover_key in seen_turnover_events:
                continue
            seen_turnover_events.add(turnover_key)
            if actor is not None:
                actor.inferred_turnovers += 1
            if target is not None:
                target.inferred_interceptions += 1
                if _has_recent_stun_against_actor(stun_index, target_alias, actor_alias, row, config):
                    target.stun_takeaways += 1
        elif event_type == "missed_shot" and actor is not None:
            actor.missed_shots += 1
        elif event_type == "shot_saved" and actor is not None:
            actor.shots_saved_against += 1
        elif event_type == "blocked_shot" and actor is not None:
            actor.blocked_shots += 1
        elif event_type == "stuffed_shot" and actor is not None:
            actor.stuffed_shots += 1
        elif event_type == "offensive_transition_time" and actor is not None and _value(row, "value") is not None:
            actor.offensive_transition_count += 1
            actor.offensive_transition_total += float(_value(row, "value") or 0.0)
        elif event_type == "defensive_transition_time" and actor is not None and _value(row, "value") is not None:
            actor.defensive_transition_count += 1
            actor.defensive_transition_total += float(_value(row, "value") or 0.0)
        elif event_type == "lane_coverage_failure" and actor is not None:
            actor.metadata["lane_coverage_failures"] = int(actor.metadata.get("lane_coverage_failures") or 0) + 1
        elif event_type == "shooter_uncovered" and actor is not None:
            actor.metadata["shooter_uncovered"] = int(actor.metadata.get("shooter_uncovered") or 0) + 1

    for event in base_events:
        if event.event_type == "steal" and event.actor_name and event.team:
            actor = _ensure_accumulator(accumulators, event.actor_name, event.team, None, event.actor_userid, match_id)
            actor.steal_takeaways += 1


def _events_between(events: list[NormalizedEvent], start_sequence: int, end_sequence: int) -> list[NormalizedEvent]:
    return [
        event
        for event in events
        if event.sequence is not None and int(event.sequence) >= int(start_sequence) and int(event.sequence) <= int(end_sequence)
    ]


def _stun_index(events: list[NormalizedEvent]) -> list[NormalizedEvent]:
    return [event for event in events if event.event_type == "stun" and event.actor_name]


def _turnover_event_key(row: Any) -> tuple[Any, ...]:
    start_sequence = _optional_int(_value(row, "start_sequence"))
    end_sequence = _optional_int(_value(row, "end_sequence"))
    return (
        _optional_int(_value(row, "match_id")),
        start_sequence,
        end_sequence,
        str(_value(row, "actor_alias") or "").casefold(),
        str(_value(row, "target_alias") or "").casefold(),
        str(_value(row, "team") or "").casefold(),
    )


def _has_recent_stun_against_actor(
    stuns: list[NormalizedEvent],
    stunner_alias: Optional[str],
    victim_alias: Optional[str],
    advanced_row: Any,
    config: InferenceConfig,
) -> bool:
    if not stunner_alias:
        return False
    end_sequence = _optional_int(_value(advanced_row, "end_sequence"))
    target_clock = _optional_float(_value(advanced_row, "end_game_clock"))
    for event in stuns:
        if str(event.actor_name or "").casefold() != str(stunner_alias).casefold():
            continue
        if victim_alias and event.target_name and str(event.target_name).casefold() != str(victim_alias).casefold():
            continue
        if end_sequence is not None and event.sequence is not None and abs(int(end_sequence) - int(event.sequence)) <= 6:
            return True
        if target_clock is not None and event.game_clock is not None and abs(float(target_clock) - float(event.game_clock)) <= config.stun_takeaway_window_seconds:
            return True
    return False


def _goalie_present(
    frame: Optional[SnapshotFrame],
    defending_team: Optional[str],
    orientation: OrientationModel,
    config: InferenceConfig,
) -> bool:
    if frame is None or not defending_team:
        return False
    return _goalie_alias(frame, defending_team, orientation, config) is not None


def _goalie_alias(
    frame: SnapshotFrame,
    defending_team: str,
    orientation: OrientationModel,
    config: InferenceConfig,
) -> Optional[str]:
    if not orientation.available:
        return None
    defenders = [player for player in frame.players if player.team == defending_team and player.best_position is not None]
    if not defenders:
        return None
    goal_point = _goal_point(orientation, defending_team, config)
    if goal_point is None:
        return None
    nearest = None
    nearest_distance = None
    for defender in defenders:
        distance = distance_3d(defender.best_position, goal_point)
        if distance is None:
            continue
        if nearest_distance is None or distance < nearest_distance:
            nearest = defender
            nearest_distance = distance
    if nearest is None or nearest_distance is None:
        return None
    if nearest_distance > config.goalie_coverage_meters or nearest.stunned:
        return None
    return nearest.alias


@dataclass
class _Possessor:
    alias: str
    team: str


@dataclass
class _CoverageSnapshot:
    offense_by_alias: dict[str, PlayerState]
    defense_by_alias: dict[str, PlayerState]
    receiver_states: dict[str, str]
    defender_roles: dict[str, str]


def _coverage_snapshot(
    frame: SnapshotFrame,
    offense_team: str,
    possessor_alias: str,
    orientation: OrientationModel,
    config: InferenceConfig,
) -> _CoverageSnapshot:
    offense_players = [
        player
        for player in frame.players
        if player.team == offense_team and player.best_position is not None and player.alias.casefold() != possessor_alias.casefold()
    ]
    defense_team = _opponent_team(offense_team)
    defense_players = [
        player
        for player in frame.players
        if player.team == defense_team and player.best_position is not None
    ]
    offense_by_alias = {player.alias.casefold(): player for player in offense_players}
    defense_by_alias = {player.alias.casefold(): player for player in defense_players}
    possessor = next(
        (
            player
            for player in frame.players
            if player.team == offense_team and player.best_position is not None and player.alias.casefold() == possessor_alias.casefold()
        ),
        None,
    )
    if possessor is None:
        return _CoverageSnapshot(offense_by_alias, defense_by_alias, {}, {})

    goalie_alias = _goalie_alias(frame, defense_team, orientation, config) if defense_team else None
    goalie_folded = goalie_alias.casefold() if goalie_alias else None
    defenders_for_assignment = [
        player for player in defense_players if goalie_folded is None or player.alias.casefold() != goalie_folded
    ]
    assignments = _assign_man_coverage(defenders_for_assignment, offense_players, config)

    receiver_states: dict[str, str] = {}
    defender_roles: dict[str, str] = {}
    covered_receivers = {receiver.alias.casefold() for _, receiver, _ in assignments}
    for defender, _, distance in assignments:
        defender_roles[defender.alias.casefold()] = "tight" if distance <= config.tight_coverage_meters else "loose"
    if goalie_folded is not None:
        defender_roles[goalie_folded] = "goalie"

    lane_blockers = Counter()
    for receiver in offense_players:
        receiver_key = receiver.alias.casefold()
        distance = distance_3d(possessor.best_position, receiver.best_position)
        if distance is None:
            continue
        if distance < config.open_pass_min_distance_meters or distance > config.open_pass_max_distance_meters:
            continue
        if receiver_key in covered_receivers:
            receiver_states[receiver_key] = "covered"
            continue
        blocker = _best_lane_blocker(possessor.best_position, receiver.best_position, defense_players, config)
        if blocker is not None:
            receiver_states[receiver_key] = "lane_blocked"
            lane_blockers[blocker.alias.casefold()] += 1
        else:
            receiver_states[receiver_key] = "open"
    for alias_folded in lane_blockers:
        defender_roles.setdefault(alias_folded, "lane_block")
    for defender in defense_players:
        defender_roles.setdefault(defender.alias.casefold(), "no_man")
    return _CoverageSnapshot(offense_by_alias, defense_by_alias, receiver_states, defender_roles)


def _assign_man_coverage(
    defenders: list[PlayerState],
    offense_players: list[PlayerState],
    config: InferenceConfig,
) -> list[tuple[PlayerState, PlayerState, float]]:
    candidates: list[tuple[float, PlayerState, PlayerState]] = []
    for defender in defenders:
        for receiver in offense_players:
            distance = distance_3d(defender.best_position, receiver.best_position)
            if distance is None or distance > config.light_coverage_meters:
                continue
            candidates.append((distance, defender, receiver))
    candidates.sort(key=lambda row: row[0])
    assigned_defenders: set[str] = set()
    assigned_receivers: set[str] = set()
    assignments: list[tuple[PlayerState, PlayerState, float]] = []
    for distance, defender, receiver in candidates:
        defender_key = defender.alias.casefold()
        receiver_key = receiver.alias.casefold()
        if defender_key in assigned_defenders or receiver_key in assigned_receivers:
            continue
        assigned_defenders.add(defender_key)
        assigned_receivers.add(receiver_key)
        assignments.append((defender, receiver, distance))
    return assignments


def _best_lane_blocker(
    disc_point: tuple[float, float, float],
    receiver_point: tuple[float, float, float],
    defenders: list[PlayerState],
    config: InferenceConfig,
) -> Optional[PlayerState]:
    best: Optional[tuple[PlayerState, float]] = None
    for defender in defenders:
        intercept = _closest_point_on_segment(disc_point, receiver_point, defender.best_position)
        if intercept is None:
            continue
        distance_to_line = distance_3d(defender.best_position, intercept)
        distance_from_disc = distance_3d(disc_point, intercept)
        total_pass_distance = distance_3d(disc_point, receiver_point)
        if distance_to_line is None or distance_from_disc is None or total_pass_distance is None:
            continue
        if distance_from_disc > total_pass_distance:
            continue
        time_to_line = distance_from_disc / max(config.expected_disc_speed_mps, 0.001)
        defender_reach = config.defender_wing_span_meters + (config.defender_speed_mps * time_to_line)
        if distance_to_line > defender_reach:
            continue
        distance_to_receiver = distance_3d(intercept, receiver_point)
        if distance_to_receiver is None:
            continue
        if best is None or distance_to_receiver < best[1]:
            best = (defender, distance_to_receiver)
    return None if best is None else best[0]


def _closest_point_on_segment(
    line_start: Optional[tuple[float, float, float]],
    line_end: Optional[tuple[float, float, float]],
    point: Optional[tuple[float, float, float]],
) -> Optional[tuple[float, float, float]]:
    if line_start is None or line_end is None or point is None:
        return None
    segment = tuple(line_end[index] - line_start[index] for index in range(3))
    segment_length_sq = sum(component * component for component in segment)
    if segment_length_sq == 0:
        return line_start
    t = sum((point[index] - line_start[index]) * segment[index] for index in range(3)) / segment_length_sq
    t = max(0.0, min(1.0, t))
    return tuple(line_start[index] + segment[index] * t for index in range(3))


def _goal_point(
    orientation: OrientationModel,
    team: str,
    config: InferenceConfig,
) -> Optional[tuple[float, float, float]]:
    axis = str(orientation.axis or "")
    side = infer_team_side(orientation, team)
    if axis not in {"x", "z"} or side not in {"positive", "negative"}:
        return None
    values = [0.0, 0.0, 0.0]
    axis_index = 0 if axis == "x" else 2
    values[axis_index] = config.goal_axis_distance_meters if side == "positive" else -config.goal_axis_distance_meters
    return values[0], values[1], values[2]


def _frame_possessor(frame: SnapshotFrame) -> Optional[_Possessor]:
    for player in frame.players:
        if player.possession:
            return _Possessor(alias=player.alias, team=player.team)
    return None


def _ensure_accumulator(
    accumulators: dict[str, PlayerMetricAccumulator],
    alias: Optional[str],
    team: Optional[str],
    player_id: Optional[int],
    userid: Optional[str],
    match_id: int,
) -> PlayerMetricAccumulator:
    alias_value = str(alias or "")
    team_value = str(team or "unknown")
    key = _metric_key(alias_value, team_value)
    accumulator = accumulators.get(key)
    if accumulator is None:
        accumulator = PlayerMetricAccumulator(
            match_id=match_id,
            player_id=player_id,
            match_alias=alias_value,
            userid=userid,
            team=team_value,
            metadata={
                "passes_to_open_receiver": 0,
                "passes_to_covered_receiver": 0,
                "catches_open": 0,
                "catches_covered": 0,
                "self_goals": 0,
                "lane_coverage_failures": 0,
                "shooter_uncovered": 0,
                "dunk_like_open_2s": 0,
                "dunk_like_guarded_2s": 0,
            },
        )
        accumulators[key] = accumulator
    else:
        if accumulator.player_id is None and player_id is not None:
            accumulator.player_id = player_id
        if not accumulator.userid and userid:
            accumulator.userid = userid
    return accumulator


def _metric_key(alias: str, team: str) -> str:
    return f"{str(team).casefold()}::{str(alias).casefold()}"


def _opponent_team(team: Optional[str]) -> str:
    folded = str(team or "").casefold()
    if folded == "blue":
        return "orange"
    if folded == "orange":
        return "blue"
    return "unknown"


def _value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except Exception:
        return getattr(row, key, None)


def _evidence(row: Any) -> dict[str, Any]:
    value = _value(row, "evidence_json")
    if isinstance(row, dict) and isinstance(row.get("evidence"), dict):
        return row["evidence"]
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        import json

        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _is_dunk_like_goal(goal_type: str) -> bool:
    normalized = str(goal_type or "").strip().casefold()
    return "slam dunk" in normalized or normalized == "headbutt"
