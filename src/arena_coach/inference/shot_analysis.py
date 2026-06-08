"""Shot-related advanced inference."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from arena_coach.parsing.normalized_event import NormalizedEvent

from .inference_config import InferenceConfig
from .spatial_math import disc_distance_to_player, distance_3d
from .spatial_models import AdvancedEvent, SnapshotFrame


def infer_shot_events(
    base_events: list[NormalizedEvent],
    frames_by_sequence: dict[int, SnapshotFrame],
    player_ids_by_alias: dict[str, Optional[int]],
    config: InferenceConfig,
) -> list[AdvancedEvent]:
    inferred: list[AdvancedEvent] = []
    for index, event in enumerate(base_events):
        if event.event_type != "shot" or not event.actor_name:
            continue
        window = _window_after(base_events, index, config.shot_save_window_seconds)
        goal_event = _first(window, lambda row: row.event_type == "goal" and _same_name(row.actor_name, event.actor_name))
        if goal_event is not None:
            continue
        save_event = _first(window, lambda row: row.event_type in {"save", "block"} and not _same_team(row.team, event.team))
        if save_event is not None:
            if save_event.event_type == "block":
                inferred.append(
                    _advanced_event(
                        "blocked_shot",
                        event,
                        player_ids_by_alias,
                        target_event=save_event,
                        confidence="high",
                        confidence_score=0.9,
                        directness="inferred",
                        explanation=f"Blocked shot: {save_event.actor_name or 'An opponent'} registered a block against {event.actor_name}'s shot.",
                        evidence=_evidence(
                            reason="shot delta followed by block delta",
                            source_events=[event.event_id, save_event.event_id],
                            source_sequences=[event.sequence, save_event.sequence],
                            time_window_seconds=_seconds_between(event, save_event),
                            thresholds={"shot_save_window_seconds": config.shot_save_window_seconds},
                        ),
                    )
                )
                stuffed = _stuffed_shot(event, save_event, frames_by_sequence, player_ids_by_alias)
                if stuffed is not None:
                    inferred.append(stuffed)
            else:
                inferred.append(
                    _advanced_event(
                        "shot_saved",
                        event,
                        player_ids_by_alias,
                        target_event=save_event,
                        confidence="high",
                        confidence_score=0.92,
                        directness="inferred",
                        explanation=(
                            f"Likely saved shot: {event.actor_name} took a shot and "
                            f"{save_event.actor_name or 'an opponent'} recorded a save shortly after."
                        ),
                        evidence=_evidence(
                            reason="shot delta followed by save delta",
                            source_events=[event.event_id, save_event.event_id],
                            source_sequences=[event.sequence, save_event.sequence],
                            time_window_seconds=_seconds_between(event, save_event),
                            thresholds={"shot_save_window_seconds": config.shot_save_window_seconds},
                        ),
                    )
                )
                stuffed = _stuffed_shot(event, save_event, frames_by_sequence, player_ids_by_alias)
                if stuffed is not None:
                    inferred.append(stuffed)
            continue

        miss_window = _window_after(base_events, index, config.shot_miss_window_seconds)
        turnover_like = _first(miss_window, lambda row: row.event_type in {"possession_change", "steal", "interception"})
        inferred.append(
            _advanced_event(
                "missed_shot",
                event,
                player_ids_by_alias,
                confidence="medium" if turnover_like is not None else "low",
                confidence_score=0.7 if turnover_like is not None else 0.45,
                directness="heuristic",
                explanation=f"Likely missed shot: {event.actor_name}'s shot was not followed by a goal or credited save/block in the next few seconds.",
                evidence=_evidence(
                    reason="shot delta with no goal/save/block window",
                    source_events=[event.event_id] + ([turnover_like.event_id] if turnover_like is not None else []),
                    source_sequences=[event.sequence] + ([turnover_like.sequence] if turnover_like is not None else []),
                    time_window_seconds=config.shot_miss_window_seconds,
                    thresholds={"shot_miss_window_seconds": config.shot_miss_window_seconds},
                ),
            )
        )
    return inferred


def _stuffed_shot(
    shot_event: NormalizedEvent,
    save_event: NormalizedEvent,
    frames_by_sequence: dict[int, SnapshotFrame],
    player_ids_by_alias: dict[str, Optional[int]],
) -> Optional[AdvancedEvent]:
    if shot_event.sequence is None or save_event.sequence is None:
        return None
    shot_frame = frames_by_sequence.get(int(shot_event.sequence))
    save_frame = frames_by_sequence.get(int(save_event.sequence))
    if shot_frame is None or save_frame is None:
        return None
    shooter = _player_named(shot_frame, shot_event.actor_name)
    defender = _player_named(save_frame, save_event.actor_name)
    if shooter is None or defender is None:
        return None
    defender_to_shooter = distance_3d(defender.best_position, shooter.best_position)
    defender_to_disc = disc_distance_to_player(save_frame.disc.position, defender)
    if defender_to_shooter is None or defender_to_disc is None:
        return None
    if defender_to_shooter > 4.0 or defender_to_disc > 2.5:
        return None
    return _advanced_event(
        "stuffed_shot",
        shot_event,
        player_ids_by_alias,
        target_event=save_event,
        confidence="medium",
        confidence_score=0.76,
        directness="heuristic",
        explanation=f"Possible stuffed shot: {save_event.actor_name or 'an opponent'} was very close to {shot_event.actor_name} and the disc when the save happened.",
        evidence=_evidence(
            reason="save happened near shooter and disc",
            source_events=[shot_event.event_id, save_event.event_id],
            source_sequences=[shot_event.sequence, save_event.sequence],
            distances={
                "defender_to_shooter": round(defender_to_shooter, 3),
                "defender_to_disc": round(defender_to_disc, 3),
            },
        ),
    )


def _advanced_event(
    event_type: str,
    base_event: NormalizedEvent,
    player_ids_by_alias: dict[str, Optional[int]],
    *,
    target_event: Optional[NormalizedEvent] = None,
    confidence: str,
    confidence_score: float,
    directness: str,
    explanation: str,
    evidence: dict[str, object],
) -> AdvancedEvent:
    target_alias = target_event.actor_name if target_event else None
    return AdvancedEvent(
        event_type=event_type,
        actor_alias=base_event.actor_name,
        target_alias=target_alias,
        actor_player_id=player_ids_by_alias.get(str(base_event.actor_name or "").casefold()),
        target_player_id=player_ids_by_alias.get(str(target_alias or "").casefold()) if target_alias else None,
        team=base_event.team,
        start_sequence=base_event.sequence,
        end_sequence=target_event.sequence if target_event and target_event.sequence is not None else base_event.sequence,
        start_game_clock=base_event.game_clock,
        end_game_clock=target_event.game_clock if target_event else base_event.game_clock,
        confidence=confidence,
        confidence_score=confidence_score,
        directness=directness,
        explanation=explanation,
        evidence=evidence,
    )


def _window_after(events: list[NormalizedEvent], index: int, window_seconds: float) -> list[NormalizedEvent]:
    start = events[index]
    rows = []
    for candidate in events[index + 1 :]:
        seconds = _seconds_between(start, candidate)
        if seconds is None:
            continue
        if seconds > window_seconds:
            break
        rows.append(candidate)
    return rows


def _first(events: list[NormalizedEvent], predicate) -> Optional[NormalizedEvent]:
    for event in events:
        if predicate(event):
            return event
    return None


def _same_name(left: Optional[str], right: Optional[str]) -> bool:
    return bool(left and right and str(left).casefold() == str(right).casefold())


def _same_team(left: Optional[str], right: Optional[str]) -> bool:
    return bool(left and right and str(left).casefold() == str(right).casefold())


def _seconds_between(left: NormalizedEvent, right: NormalizedEvent) -> Optional[float]:
    if not left.captured_at or not right.captured_at:
        return None
    try:
        return max(0.0, (datetime.fromisoformat(right.captured_at) - datetime.fromisoformat(left.captured_at)).total_seconds())
    except ValueError:
        return None


def _player_named(frame: SnapshotFrame, alias: Optional[str]):
    if not alias:
        return None
    for player in frame.players:
        if str(player.alias).casefold() == str(alias).casefold():
            return player
    return None


def _evidence(
    *,
    reason: str,
    source_events: list[Optional[int]] | None = None,
    source_sequences: list[Optional[int]] | None = None,
    time_window_seconds: Optional[float] = None,
    thresholds: Optional[dict[str, float]] = None,
    distances: Optional[dict[str, float]] = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "reason": reason,
        "source_events": [event_id for event_id in source_events or [] if event_id is not None],
        "source_sequences": [sequence for sequence in source_sequences or [] if sequence is not None],
    }
    if distances:
        payload["distances"] = distances
    if time_window_seconds is not None:
        payload["time_window_seconds"] = round(float(time_window_seconds), 3)
    if thresholds:
        payload["thresholds"] = thresholds
    return payload
