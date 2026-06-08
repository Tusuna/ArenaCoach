"""Turnover-oriented advanced inference."""

from __future__ import annotations

from typing import Optional

from arena_coach.parsing.normalized_event import NormalizedEvent

from .spatial_models import AdvancedEvent, PossessionChain


def infer_turnovers(
    chains: list[PossessionChain],
    base_events: list[NormalizedEvent],
    player_ids_by_alias: dict[str, Optional[int]],
) -> list[AdvancedEvent]:
    inferred: list[AdvancedEvent] = []
    for index, chain in enumerate(chains[:-1]):
        next_chain = chains[index + 1]
        if not chain.team or not next_chain.team or chain.team == next_chain.team:
            continue
        relevant = _events_between(base_events, chain.end_sequence, next_chain.start_sequence)
        if any(event.event_type in {"goal", "save", "shot"} for event in relevant):
            continue
        steal_like = _first(relevant, lambda event: event.event_type in {"steal", "interception"})
        confidence = "high" if chain.actor_alias and next_chain.actor_alias else "medium"
        if steal_like is None and not chain.actor_alias:
            confidence = "low"
        inferred.append(
            AdvancedEvent(
                event_type="turnover",
                actor_alias=chain.actor_alias,
                target_alias=next_chain.actor_alias,
                actor_player_id=player_ids_by_alias.get(str(chain.actor_alias or "").casefold()),
                target_player_id=player_ids_by_alias.get(str(next_chain.actor_alias or "").casefold()),
                team=chain.team,
                start_sequence=chain.end_sequence,
                end_sequence=next_chain.start_sequence,
                start_game_clock=chain.end_game_clock,
                end_game_clock=next_chain.start_game_clock,
                confidence=confidence,
                confidence_score={"high": 0.85, "medium": 0.65, "low": 0.4}[confidence],
                directness="inferred",
                explanation=_explanation(chain, next_chain, steal_like),
                evidence={
                    "reason": "possession changed to the opponent without a shot/goal/save sequence explaining it",
                    "source_events": [event.event_id for event in relevant if event.event_id is not None],
                    "source_sequences": [chain.end_sequence, next_chain.start_sequence],
                    "previous_possessor": chain.actor_alias,
                    "next_possessor": next_chain.actor_alias,
                    "time_window_seconds": round(next_chain.duration_seconds, 3),
                    "steal_or_interception_event": steal_like.event_type if steal_like is not None else None,
                },
            )
        )
    return inferred


def _events_between(events: list[NormalizedEvent], start_sequence: int, end_sequence: int) -> list[NormalizedEvent]:
    return [
        event
        for event in events
        if event.sequence is not None and int(event.sequence) >= int(start_sequence) and int(event.sequence) <= int(end_sequence)
    ]


def _first(events: list[NormalizedEvent], predicate) -> Optional[NormalizedEvent]:
    for event in events:
        if predicate(event):
            return event
    return None


def _explanation(chain: PossessionChain, next_chain: PossessionChain, steal_like: Optional[NormalizedEvent]) -> str:
    if steal_like is not None:
        return (
            f"Likely turnover: {chain.actor_alias or chain.team or 'the team'} lost possession and "
            f"{next_chain.actor_alias or next_chain.team or 'the opponent'} took over with a nearby {steal_like.event_type}."
        )
    return (
        f"Likely turnover: {chain.actor_alias or chain.team or 'the team'} lost possession and "
        f"{next_chain.actor_alias or next_chain.team or 'the opponent'} gained control without a shot or goal sequence."
    )
