"""Transition timing inference."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .inference_config import InferenceConfig
from .spatial_math import defensive_half, offensive_half
from .spatial_models import AdvancedEvent, OrientationModel, PossessionChain, SnapshotFrame


def infer_transition_events(
    chains: list[PossessionChain],
    frames: list[SnapshotFrame],
    orientation: OrientationModel,
    player_ids_by_alias: dict[str, Optional[int]],
    config: InferenceConfig,
) -> list[AdvancedEvent]:
    if not orientation.available:
        return []
    by_sequence = {frame.sequence: index for index, frame in enumerate(frames)}
    inferred: list[AdvancedEvent] = []
    for index in range(1, len(chains)):
        previous_chain = chains[index - 1]
        current_chain = chains[index]
        if not current_chain.team or not current_chain.frames:
            continue
        start_frame = current_chain.frames[0]
        start_index = by_sequence.get(start_frame.sequence)
        if start_index is None:
            continue
        future_frames = _frames_in_window(frames[start_index:], config.transition_window_seconds)
        if not future_frames:
            continue

        for player in start_frame.players:
            if player.team == current_chain.team:
                reached = _first_transition(future_frames, player.alias, current_chain.team, orientation, True, config)
                if reached is not None:
                    inferred.append(
                        _transition_event(
                            "offensive_transition_time",
                            player.alias,
                            player_ids_by_alias,
                            current_chain.team,
                            start_frame,
                            reached,
                            "Possible offensive push timing",
                            "player reached the offensive half after team possession changed",
                        )
                    )
            elif previous_chain.team and player.team == previous_chain.team:
                reached = _first_transition(future_frames, player.alias, player.team, orientation, False, config)
                if reached is not None:
                    inferred.append(
                        _transition_event(
                            "defensive_transition_time",
                            player.alias,
                            player_ids_by_alias,
                            player.team,
                            start_frame,
                            reached,
                            "Possible defensive recovery timing",
                            "player reached the defensive half after losing possession",
                        )
                    )
    return inferred


def _transition_event(
    event_type: str,
    alias: str,
    player_ids_by_alias: dict[str, Optional[int]],
    team: str,
    start_frame: SnapshotFrame,
    reached_frame: SnapshotFrame,
    explanation_prefix: str,
    reason: str,
) -> AdvancedEvent:
    duration = _seconds_between_frames(start_frame, reached_frame) or 0.0
    return AdvancedEvent(
        event_type=event_type,
        actor_alias=alias,
        actor_player_id=player_ids_by_alias.get(str(alias).casefold()),
        team=team,
        start_sequence=start_frame.sequence,
        end_sequence=reached_frame.sequence,
        start_game_clock=start_frame.game_clock,
        end_game_clock=reached_frame.game_clock,
        confidence="medium",
        confidence_score=0.64,
        directness="heuristic",
        value=round(duration, 3),
        explanation=f"{explanation_prefix}: {alias} crossed into the expected half about {duration:.2f}s after the possession change.",
        evidence={
            "reason": reason,
            "source_sequences": [start_frame.sequence, reached_frame.sequence],
            "time_window_seconds": round(duration, 3),
        },
    )


def _first_transition(
    frames: list[SnapshotFrame],
    alias: str,
    team: str,
    orientation: OrientationModel,
    want_offense: bool,
    config: InferenceConfig,
) -> Optional[SnapshotFrame]:
    for frame in frames:
        player = _player_named(frame, alias)
        if player is None or player.best_position is None:
            continue
        state = offensive_half(orientation, team, player.best_position, config.transition_cross_half_threshold)
        if state is None:
            continue
        if want_offense and state:
            return frame
        if not want_offense and defensive_half(orientation, team, player.best_position, config.transition_cross_half_threshold):
            return frame
    return None


def _player_named(frame: SnapshotFrame, alias: str):
    for player in frame.players:
        if str(player.alias).casefold() == str(alias).casefold():
            return player
    return None


def _frames_in_window(frames: list[SnapshotFrame], seconds: float) -> list[SnapshotFrame]:
    if not frames:
        return []
    start = frames[0]
    rows: list[SnapshotFrame] = []
    for frame in frames:
        delta = _seconds_between_frames(start, frame)
        if delta is None:
            continue
        if delta > seconds:
            break
        rows.append(frame)
    return rows


def _seconds_between_frames(left: SnapshotFrame, right: SnapshotFrame) -> Optional[float]:
    if not left.captured_at or not right.captured_at:
        return None
    try:
        return max(0.0, (datetime.fromisoformat(right.captured_at) - datetime.fromisoformat(left.captured_at)).total_seconds())
    except ValueError:
        return None
