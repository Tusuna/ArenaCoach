"""Reusable spatial helpers for advanced inference."""

from __future__ import annotations

import math
from typing import Iterable, Optional

from .spatial_models import OrientationModel, PlayerState, Vector3


def distance_3d(a: Optional[Vector3], b: Optional[Vector3]) -> Optional[float]:
    if a is None or b is None:
        return None
    return math.sqrt(sum((a[index] - b[index]) ** 2 for index in range(3)))


def speed_magnitude(vector: Optional[Vector3]) -> Optional[float]:
    if vector is None:
        return None
    return math.sqrt(sum(component * component for component in vector))


def nearest_player_to_point(players: Iterable[PlayerState], point: Optional[Vector3]) -> Optional[tuple[PlayerState, float]]:
    best: Optional[tuple[PlayerState, float]] = None
    if point is None:
        return None
    for player in players:
        distance = distance_3d(player.best_position, point)
        if distance is None:
            continue
        if best is None or distance < best[1]:
            best = (player, distance)
    return best


def players_within_radius(players: Iterable[PlayerState], point: Optional[Vector3], radius: float) -> list[tuple[PlayerState, float]]:
    rows = []
    if point is None:
        return rows
    for player in players:
        distance = distance_3d(player.best_position, point)
        if distance is None or distance > radius:
            continue
        rows.append((player, distance))
    rows.sort(key=lambda row: row[1])
    return rows


def player_team_at_sequence(player: PlayerState, sequence: Optional[int]) -> Optional[str]:
    _ = sequence
    return player.team


def disc_distance_to_player(disc_position: Optional[Vector3], player: PlayerState) -> Optional[float]:
    return distance_3d(disc_position, player.best_position)


def is_player_near_disc(player: PlayerState, disc_position: Optional[Vector3], radius: float) -> bool:
    distance = disc_distance_to_player(disc_position, player)
    return distance is not None and distance <= radius


def distance_to_line_segment(point: Optional[Vector3], line_start: Optional[Vector3], line_end: Optional[Vector3]) -> Optional[float]:
    if point is None or line_start is None or line_end is None:
        return None
    segment = tuple(line_end[index] - line_start[index] for index in range(3))
    segment_length_sq = sum(component * component for component in segment)
    if segment_length_sq == 0:
        return distance_3d(point, line_start)
    t = sum((point[index] - line_start[index]) * segment[index] for index in range(3)) / segment_length_sq
    t = max(0.0, min(1.0, t))
    closest = tuple(line_start[index] + segment[index] * t for index in range(3))
    return distance_3d(point, closest)


def is_player_between_points(player: PlayerState, point_a: Optional[Vector3], point_b: Optional[Vector3], lane_width: float) -> bool:
    distance = distance_to_line_segment(player.best_position, point_a, point_b)
    return distance is not None and distance <= lane_width


def infer_team_side(orientation: OrientationModel, team: Optional[str]) -> Optional[str]:
    if not orientation.available or not team:
        return None
    team_folded = str(team).casefold()
    if team_folded == "blue":
        return orientation.blue_side
    if team_folded == "orange":
        return orientation.orange_side
    return None


def coordinate_on_axis(point: Optional[Vector3], axis: Optional[str]) -> Optional[float]:
    if point is None or axis not in {"x", "y", "z"}:
        return None
    return point[{"x": 0, "y": 1, "z": 2}[axis]]


def offensive_half(orientation: OrientationModel, team: Optional[str], point: Optional[Vector3], threshold: float = 0.0) -> Optional[bool]:
    side = infer_team_side(orientation, team)
    axis_value = coordinate_on_axis(point, orientation.axis)
    if side is None or axis_value is None:
        return None
    if side == "negative":
        return axis_value > threshold
    if side == "positive":
        return axis_value < threshold
    return None


def defensive_half(orientation: OrientationModel, team: Optional[str], point: Optional[Vector3], threshold: float = 0.0) -> Optional[bool]:
    offensive = offensive_half(orientation, team, point, threshold)
    if offensive is None:
        return None
    return not offensive
