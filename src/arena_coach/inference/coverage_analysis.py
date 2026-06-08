"""Conservative defensive coverage-gap inference."""

from __future__ import annotations

from typing import Optional

from arena_coach.parsing.normalized_event import NormalizedEvent

from .inference_config import InferenceConfig
from .spatial_math import distance_3d, is_player_between_points
from .spatial_models import AdvancedEvent, OrientationModel, SnapshotFrame


def infer_coverage_events(
    base_events: list[NormalizedEvent],
    frames_by_sequence: dict[int, SnapshotFrame],
    player_ids_by_alias: dict[str, Optional[int]],
    config: InferenceConfig,
    orientation: OrientationModel,
) -> list[AdvancedEvent]:
    if not orientation.available:
        return []
    inferred: list[AdvancedEvent] = []
    for index, event in enumerate(base_events):
        if event.event_type != "goal" or not event.actor_name or event.sequence is None:
            continue
        frame = frames_by_sequence.get(int(event.sequence))
        if frame is None:
            continue
        scorer = _player_named(frame, event.actor_name)
        if scorer is None or scorer.best_position is None:
            continue
        defending_team = _opponent_team(event.team)
        defenders = [player for player in frame.players if player.team == defending_team]
        if not defenders:
            continue

        nearest_distance = None
        for defender in defenders:
            distance = distance_3d(defender.best_position, scorer.best_position)
            if distance is None:
                continue
            nearest_distance = distance if nearest_distance is None else min(nearest_distance, distance)

        if nearest_distance is not None and nearest_distance > config.scorer_uncovered_radius_meters:
            inferred.append(
                AdvancedEvent(
                    event_type="shooter_uncovered",
                    actor_alias=event.actor_name,
                    actor_player_id=player_ids_by_alias.get(str(event.actor_name).casefold()),
                    team=event.team,
                    start_sequence=event.sequence,
                    end_sequence=event.sequence,
                    start_game_clock=event.game_clock,
                    end_game_clock=event.game_clock,
                    confidence="medium",
                    confidence_score=0.66,
                    directness="heuristic",
                    explanation=f"Possible coverage gap: {event.actor_name} appears to have scored without a defender nearby.",
                    evidence={
                        "reason": "nearest defender distance to scorer exceeded coverage radius",
                        "source_events": [event.event_id] if event.event_id is not None else [],
                        "source_sequences": [event.sequence],
                        "distances": {"nearest_defender_to_scorer": round(float(nearest_distance), 3)},
                        "thresholds": {"nearby_defender_radius_meters": config.scorer_uncovered_radius_meters},
                    },
                )
            )

        if not event.assist_name:
            continue
        assister_frame = _latest_frame_before(frames_by_sequence, int(event.sequence))
        assister = _player_named(assister_frame, event.assist_name) if assister_frame is not None else None
        if assister is None or assister.best_position is None:
            continue
        blockers = [
            defender
            for defender in defenders
            if is_player_between_points(defender, assister.best_position, scorer.best_position, config.lane_width_meters)
        ]
        if blockers:
            continue
        inferred.append(
            AdvancedEvent(
                event_type="lane_coverage_failure",
                actor_alias=event.assist_name,
                target_alias=event.actor_name,
                actor_player_id=player_ids_by_alias.get(str(event.assist_name).casefold()),
                target_player_id=player_ids_by_alias.get(str(event.actor_name).casefold()),
                team=event.team,
                start_sequence=event.sequence,
                end_sequence=event.sequence,
                start_game_clock=event.game_clock,
                end_game_clock=event.game_clock,
                confidence="low",
                confidence_score=0.48,
                directness="review_needed",
                explanation=f"Possible open lane: the final pass into {event.actor_name}'s goal does not appear to have a defender in the lane.",
                evidence={
                    "reason": "no defender detected within lane width between assister and scorer",
                    "source_events": [event.event_id] if event.event_id is not None else [],
                    "source_sequences": [event.sequence],
                    "thresholds": {"lane_width_meters": config.lane_width_meters},
                },
            )
        )
    return inferred


def _player_named(frame: Optional[SnapshotFrame], alias: Optional[str]):
    if frame is None or not alias:
        return None
    for player in frame.players:
        if str(player.alias).casefold() == str(alias).casefold():
            return player
    return None


def _opponent_team(team: Optional[str]) -> Optional[str]:
    if str(team or "").casefold() == "blue":
        return "orange"
    if str(team or "").casefold() == "orange":
        return "blue"
    return None


def _latest_frame_before(frames_by_sequence: dict[int, SnapshotFrame], sequence: int) -> Optional[SnapshotFrame]:
    keys = [key for key in frames_by_sequence if key <= sequence]
    if not keys:
        return None
    return frames_by_sequence[max(keys)]
