"""Clear inference from team possession chains and disc movement."""

from __future__ import annotations

from typing import Optional

from .inference_config import InferenceConfig
from .spatial_math import distance_3d, offensive_half
from .spatial_models import AdvancedEvent, OrientationModel, PossessionChain


def infer_clears(
    chains: list[PossessionChain],
    orientation: OrientationModel,
    player_ids_by_alias: dict[str, Optional[int]],
    config: InferenceConfig,
) -> list[AdvancedEvent]:
    if not orientation.available:
        return []
    inferred: list[AdvancedEvent] = []
    for chain in chains:
        if not chain.team or not chain.frames:
            continue
        start_frame = chain.frames[0]
        end_frame = chain.frames[-1]
        start_point = start_frame.disc.position or _player_position(chain, start_frame)
        end_point = end_frame.disc.position or _player_position(chain, end_frame)
        if start_point is None or end_point is None:
            continue
        start_offense = offensive_half(orientation, chain.team, start_point, config.transition_cross_half_threshold)
        end_offense = offensive_half(orientation, chain.team, end_point, config.transition_cross_half_threshold)
        movement = distance_3d(start_point, end_point)
        if start_offense is None or end_offense is None or movement is None:
            continue
        if start_offense:
            continue
        if movement < config.clear_min_distance_meters:
            continue
        if not end_offense and abs(end_point[2] if orientation.axis == "z" else end_point[0]) <= config.transition_cross_half_threshold:
            continue
        outcome = "successful_clear"
        if chain.next_team and chain.next_team != chain.team:
            outcome = "failed_clear"
        inferred.append(
            AdvancedEvent(
                event_type="clear",
                actor_alias=chain.actor_alias,
                actor_player_id=player_ids_by_alias.get(str(chain.actor_alias or "").casefold()),
                team=chain.team,
                start_sequence=chain.start_sequence,
                end_sequence=chain.end_sequence,
                start_game_clock=chain.start_game_clock,
                end_game_clock=chain.end_game_clock,
                confidence="medium" if end_offense else "low",
                confidence_score=0.72 if end_offense else 0.45,
                directness="heuristic",
                value=round(float(movement), 3),
                explanation=(
                    f"Possible {outcome.replace('_', ' ')}: {chain.actor_alias or chain.team} moved the disc out of the defensive half."
                ),
                evidence={
                    "reason": "disc moved from defensive space into neutral/offensive space",
                    "source_sequences": [chain.start_sequence, chain.end_sequence],
                    "movement_distance_meters": round(float(movement), 3),
                    "outcome": outcome,
                    "thresholds": {"clear_min_distance_meters": config.clear_min_distance_meters},
                },
            )
        )
    return inferred


def _player_position(chain: PossessionChain, frame) -> Optional[tuple[float, float, float]]:
    if not chain.actor_alias:
        return None
    for player in frame.players:
        if str(player.alias).casefold() == str(chain.actor_alias).casefold():
            return player.best_position
    return None
