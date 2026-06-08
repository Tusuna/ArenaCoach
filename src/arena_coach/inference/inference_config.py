"""Configurable thresholds for advanced inference."""

from __future__ import annotations

from dataclasses import dataclass


CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class InferenceConfig:
    possession_change_window_seconds: float = 3.0
    shot_save_window_seconds: float = 3.0
    shot_miss_window_seconds: float = 3.0
    pass_completion_window_seconds: float = 3.0
    immediate_pressure_window_seconds: float = 3.0
    covered_radius_meters: float = 6.0
    lane_width_meters: float = 4.5
    clear_min_distance_meters: float = 15.0
    transition_cross_half_threshold: float = 1.0
    nearby_defender_radius_meters: float = 7.5
    goal_area_radius_meters: float = 12.0
    min_confidence_to_display: str = "medium"
    orientation_min_team_separation: float = 10.0
    scorer_uncovered_radius_meters: float = 9.0
    transition_window_seconds: float = 8.0
    open_pass_min_distance_meters: float = 5.0
    open_pass_max_distance_meters: float = 40.0
    light_coverage_meters: float = 4.0
    tight_coverage_meters: float = 2.0
    goalie_coverage_meters: float = 3.5
    goal_axis_distance_meters: float = 36.0
    expected_disc_speed_mps: float = 16.0
    defender_wing_span_meters: float = 1.0
    defender_speed_mps: float = 5.0
    stun_takeaway_window_seconds: float = 2.0


DEFAULT_CONFIG = InferenceConfig()


def confidence_value(label: str) -> int:
    return CONFIDENCE_ORDER.get(str(label or "low").casefold(), 0)


def meets_min_confidence(label: str, minimum: str) -> bool:
    return confidence_value(label) >= confidence_value(minimum)
