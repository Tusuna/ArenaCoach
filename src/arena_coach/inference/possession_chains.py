"""Possession chain helpers for advanced inference."""

from __future__ import annotations

from typing import Iterable, Optional

from arena_coach.parsing.normalized_event import NormalizedEvent

from .spatial_models import PossessionChain, SnapshotFrame


def build_possession_chains(frames: Iterable[SnapshotFrame], base_events: Iterable[NormalizedEvent]) -> list[PossessionChain]:
    frame_list = list(frames)
    if not frame_list:
        return []

    chains: list[PossessionChain] = []
    current: Optional[PossessionChain] = None

    for frame in frame_list:
        team, alias, player_id, userid = _frame_possessor(frame)
        identity = (team, alias or userid or "")
        current_identity = None if current is None else (current.team, current.actor_alias or current.actor_userid or "")
        if current is None or identity != current_identity:
            if current is not None:
                current.end_sequence = frame.sequence
                current.end_game_clock = frame.game_clock
                current.end_captured_at = frame.captured_at
                chains.append(current)
            current = PossessionChain(
                team=team,
                actor_alias=alias,
                actor_player_id=player_id,
                actor_userid=userid,
                start_sequence=frame.sequence,
                end_sequence=frame.sequence,
                start_game_clock=frame.game_clock,
                end_game_clock=frame.game_clock,
                start_captured_at=frame.captured_at,
                end_captured_at=frame.captured_at,
                frames=[frame],
            )
        else:
            current.end_sequence = frame.sequence
            current.end_game_clock = frame.game_clock
            current.end_captured_at = frame.captured_at
            current.frames.append(frame)

    if current is not None:
        chains.append(current)

    event_list = list(base_events)
    for index, chain in enumerate(chains):
        previous_chain = chains[index - 1] if index > 0 else None
        next_chain = chains[index + 1] if index + 1 < len(chains) else None
        chain.previous_actor_alias = previous_chain.actor_alias if previous_chain else None
        chain.next_actor_alias = next_chain.actor_alias if next_chain else None
        chain.next_team = next_chain.team if next_chain else None
        search_end = next_chain.start_sequence if next_chain is not None else chain.end_sequence
        chain.terminal_event = _terminal_event(event_list, chain.start_sequence, search_end)
    return chains


def _frame_possessor(frame: SnapshotFrame) -> tuple[Optional[str], Optional[str], Optional[int], Optional[str]]:
    possessing_players = [player for player in frame.players if player.possession]
    if possessing_players:
        player = possessing_players[0]
        return player.team, player.alias, player.actor_player_id, player.userid
    if frame.blue_team_possession and not frame.orange_team_possession:
        return "blue", None, None, None
    if frame.orange_team_possession and not frame.blue_team_possession:
        return "orange", None, None, None
    return None, None, None, None


def _terminal_event(events: list[NormalizedEvent], start_sequence: int, end_sequence: int) -> Optional[str]:
    relevant = [
        event.event_type
        for event in events
        if event.sequence is not None and start_sequence <= int(event.sequence) <= end_sequence
    ]
    for event_type in ("goal", "save", "steal", "interception", "shot", "score_update", "pass", "catch"):
        if event_type in relevant:
            return event_type
    return "unknown"
