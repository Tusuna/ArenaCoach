"""Pass-chain and assist-sequence advanced inference."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from arena_coach.parsing.normalized_event import NormalizedEvent

from .inference_config import InferenceConfig
from .spatial_math import players_within_radius
from .spatial_models import AdvancedEvent, SnapshotFrame


def infer_pass_events(
    base_events: list[NormalizedEvent],
    frames_by_sequence: dict[int, SnapshotFrame],
    player_ids_by_alias: dict[str, Optional[int]],
    config: InferenceConfig,
) -> list[AdvancedEvent]:
    inferred: list[AdvancedEvent] = []
    for index, event in enumerate(base_events):
        if event.event_type != "pass" or not event.actor_name:
            continue
        window = _window_after(base_events, index, config.pass_completion_window_seconds)
        catch_event = _first(window, lambda row: row.event_type == "catch")
        opponent_gain = _first(window, lambda row: row.event_type in {"interception", "steal"} or (row.event_type == "possession_change" and not _same_team(row.team, event.team)))
        intended_receiver = _intended_receiver(event, window, frames_by_sequence)

        if opponent_gain is not None:
            confidence = "high" if opponent_gain.event_type == "interception" else "medium"
            inferred.append(
                _advanced_event(
                    "intercepted_pass",
                    event,
                    player_ids_by_alias,
                    target_alias=opponent_gain.actor_name,
                    confidence=confidence,
                    confidence_score=0.9 if confidence == "high" else 0.7,
                    directness="inferred",
                    explanation=f"Likely intercepted pass: {event.actor_name}'s pass was followed by opponent control from {opponent_gain.actor_name or 'an opponent'}.",
                    evidence=_evidence(
                        reason="pass followed by opponent possession",
                        source_events=[event.event_id, opponent_gain.event_id],
                        source_sequences=[event.sequence, opponent_gain.sequence],
                        intended_receiver=intended_receiver,
                        time_window_seconds=_seconds_between(event, opponent_gain),
                        thresholds={"pass_completion_window_seconds": config.pass_completion_window_seconds},
                    ),
                )
            )

        if catch_event is None or not _same_team(catch_event.team, event.team):
            inferred.append(
                _advanced_event(
                    "missed_pass",
                    event,
                    player_ids_by_alias,
                    target_alias=intended_receiver,
                    confidence="medium" if intended_receiver else "low",
                    confidence_score=0.74 if intended_receiver else 0.45,
                    directness="heuristic",
                    explanation=f"Likely missed pass: {event.actor_name}'s pass was not completed to a teammate in the next few seconds.",
                    evidence=_evidence(
                        reason="pass without same-team catch in window",
                        source_events=[event.event_id] + ([catch_event.event_id] if catch_event else []),
                        source_sequences=[event.sequence] + ([catch_event.sequence] if catch_event else []),
                        intended_receiver=intended_receiver,
                        time_window_seconds=config.pass_completion_window_seconds,
                        thresholds={"pass_completion_window_seconds": config.pass_completion_window_seconds},
                    ),
                )
            )
            if intended_receiver:
                inferred.append(
                    _advanced_event(
                        "missed_catch",
                        event,
                        player_ids_by_alias,
                        target_alias=intended_receiver,
                        confidence="medium",
                        confidence_score=0.68,
                        directness="heuristic",
                        explanation=f"Possible missed catch: {intended_receiver} appears to have been the nearest teammate to {event.actor_name}'s pass, but no catch was recorded.",
                        evidence=_evidence(
                            reason="likely receiver near pass end point without catch",
                            source_events=[event.event_id],
                            source_sequences=[event.sequence],
                            intended_receiver=intended_receiver,
                            time_window_seconds=config.pass_completion_window_seconds,
                            thresholds={"pass_completion_window_seconds": config.pass_completion_window_seconds},
                        ),
                    )
                )
        elif catch_event.actor_name:
            covered = _covered_teammate_event(
                event,
                catch_event,
                window,
                frames_by_sequence,
                player_ids_by_alias,
                config,
            )
            if covered is not None:
                inferred.append(covered)

    inferred.extend(infer_initiators(base_events, player_ids_by_alias, config))
    return inferred


def infer_initiators(
    base_events: list[NormalizedEvent],
    player_ids_by_alias: dict[str, Optional[int]],
    config: InferenceConfig,
) -> list[AdvancedEvent]:
    _ = config
    inferred: list[AdvancedEvent] = []
    for index, event in enumerate(base_events):
        if event.event_type != "goal" or not event.assist_name or not event.actor_name:
            continue
        previous_window = _window_before(base_events, index, 12.0)
        assister_catch_index = None
        for previous_index, candidate in enumerate(previous_window):
            if candidate.event_type == "catch" and _same_name(candidate.actor_name, event.assist_name):
                assister_catch_index = previous_index
        if assister_catch_index is None:
            continue
        prior_rows = previous_window[:assister_catch_index]
        prior_pass = None
        for candidate in reversed(prior_rows):
            if candidate.event_type != "pass" or not _same_team(candidate.team, event.team):
                continue
            if _same_name(candidate.actor_name, event.assist_name):
                continue
            prior_pass = candidate
            break
        if prior_pass is None or not prior_pass.actor_name:
            continue
        inferred.append(
            _advanced_event(
                "initiator",
                prior_pass,
                player_ids_by_alias,
                target_alias=event.actor_name,
                assist_alias=event.assist_name,
                confidence="high",
                confidence_score=0.88,
                directness="inferred",
                explanation=f"Likely initiator: {prior_pass.actor_name} fed the possession that led into {event.assist_name}'s assist and {event.actor_name}'s goal.",
                evidence=_evidence(
                    reason="pass to assister before assisted goal",
                    source_events=[prior_pass.event_id, event.event_id],
                    source_sequences=[prior_pass.sequence, event.sequence],
                    intended_receiver=event.assist_name,
                    time_window_seconds=_seconds_between(prior_pass, event),
                ),
            )
        )
    return inferred


def _covered_teammate_event(
    pass_event: NormalizedEvent,
    catch_event: NormalizedEvent,
    window: list[NormalizedEvent],
    frames_by_sequence: dict[int, SnapshotFrame],
    player_ids_by_alias: dict[str, Optional[int]],
    config: InferenceConfig,
) -> Optional[AdvancedEvent]:
    if catch_event.sequence is None:
        return None
    catch_frame = frames_by_sequence.get(int(catch_event.sequence))
    if catch_frame is None or not catch_event.actor_name:
        return None
    receiver = _player_named(catch_frame, catch_event.actor_name)
    if receiver is None or receiver.best_position is None:
        return None
    defenders = [
        row
        for row in players_within_radius(
            [player for player in catch_frame.players if not _same_team(player.team, catch_event.team)],
            receiver.best_position,
            config.covered_radius_meters,
        )
    ]
    if not defenders:
        return None
    immediate_problem = _first(
        window,
        lambda row: row.sequence is not None
        and catch_event.sequence is not None
        and row.sequence >= catch_event.sequence
        and _seconds_between(catch_event, row) is not None
        and _seconds_between(catch_event, row) <= config.immediate_pressure_window_seconds
        and (
            row.event_type in {"stun", "steal", "interception"}
            or (row.event_type == "possession_change" and not _same_team(row.team, catch_event.team))
        ),
    )
    if immediate_problem is None:
        return None
    nearest_defender, nearest_distance = defenders[0]
    return _advanced_event(
        "pass_to_covered_teammate",
        pass_event,
        player_ids_by_alias,
        target_alias=catch_event.actor_name,
        confidence="medium",
        confidence_score=0.73,
        directness="heuristic",
        explanation=f"Possible pass to covered teammate: {catch_event.actor_name} caught the pass under pressure and the possession immediately became risky.",
        evidence=_evidence(
            reason="receiver caught under defender pressure and immediately lost advantage",
            source_events=[pass_event.event_id, catch_event.event_id, immediate_problem.event_id],
            source_sequences=[pass_event.sequence, catch_event.sequence, immediate_problem.sequence],
            intended_receiver=catch_event.actor_name,
            time_window_seconds=_seconds_between(catch_event, immediate_problem),
            distances={"nearest_defender_to_receiver": round(nearest_distance, 3)},
            thresholds={
                "covered_radius_meters": config.covered_radius_meters,
                "immediate_pressure_window_seconds": config.immediate_pressure_window_seconds,
            },
        ),
    )


def _intended_receiver(pass_event: NormalizedEvent, window: list[NormalizedEvent], frames_by_sequence: dict[int, SnapshotFrame]) -> Optional[str]:
    if pass_event.sequence is None:
        return None
    frame = frames_by_sequence.get(int(pass_event.sequence))
    if frame is None:
        return None
    later_frame = None
    for row in window:
        if row.sequence is None:
            continue
        later_frame = frames_by_sequence.get(int(row.sequence))
        if later_frame is not None:
            break
    if later_frame is None:
        later_frame = frame
    point = later_frame.disc.position or frame.disc.position
    if point is None:
        return None
    teammates = [
        player
        for player in later_frame.players
        if _same_team(player.team, pass_event.team) and not _same_name(player.alias, pass_event.actor_name)
    ]
    if not teammates:
        return None
    candidates = players_within_radius(teammates, point, 8.0)
    return candidates[0][0].alias if candidates else None


def _advanced_event(
    event_type: str,
    base_event: NormalizedEvent,
    player_ids_by_alias: dict[str, Optional[int]],
    *,
    target_alias: Optional[str],
    confidence: str,
    confidence_score: float,
    directness: str,
    explanation: str,
    evidence: dict[str, object],
    assist_alias: Optional[str] = None,
) -> AdvancedEvent:
    return AdvancedEvent(
        event_type=event_type,
        actor_alias=base_event.actor_name,
        target_alias=target_alias,
        assist_alias=assist_alias,
        actor_player_id=player_ids_by_alias.get(str(base_event.actor_name or "").casefold()),
        target_player_id=player_ids_by_alias.get(str(target_alias or "").casefold()) if target_alias else None,
        assist_player_id=player_ids_by_alias.get(str(assist_alias or "").casefold()) if assist_alias else None,
        team=base_event.team,
        start_sequence=base_event.sequence,
        end_sequence=base_event.sequence,
        start_game_clock=base_event.game_clock,
        end_game_clock=base_event.game_clock,
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


def _window_before(events: list[NormalizedEvent], index: int, window_seconds: float) -> list[NormalizedEvent]:
    end = events[index]
    rows = []
    for candidate in reversed(events[:index]):
        seconds = _seconds_between(candidate, end)
        if seconds is None:
            continue
        if seconds > window_seconds:
            break
        rows.insert(0, candidate)
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
    intended_receiver: Optional[str] = None,
    time_window_seconds: Optional[float] = None,
    thresholds: Optional[dict[str, float]] = None,
    distances: Optional[dict[str, float]] = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "reason": reason,
        "source_events": [event_id for event_id in source_events or [] if event_id is not None],
        "source_sequences": [sequence for sequence in source_sequences or [] if sequence is not None],
    }
    if intended_receiver:
        payload["intended_receiver"] = intended_receiver
    if time_window_seconds is not None:
        payload["time_window_seconds"] = round(float(time_window_seconds), 3)
    if thresholds:
        payload["thresholds"] = thresholds
    if distances:
        payload["distances"] = distances
    return payload
