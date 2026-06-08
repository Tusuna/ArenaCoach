"""Trend calculations for recent self performance."""

from __future__ import annotations

from typing import Dict, Iterable, List

from arena_coach.stats.stat_models import MatchParticipant, TrendMetric


TREND_STATS = (
    "points",
    "goals",
    "assists",
    "saves",
    "stuns",
    "steals",
    "shots",
    "shot_efficiency",
    "passes",
    "catches",
    "interceptions",
    "blocks",
)


def calculate_trends(self_rows: Iterable[MatchParticipant], sample_size: int = 5) -> List[TrendMetric]:
    rows = list(self_rows)
    recent = rows[:sample_size]
    previous = rows[sample_size : sample_size * 2]
    metrics: List[TrendMetric] = []
    for stat_name in TREND_STATS:
        last_average = _average(recent, stat_name)
        previous_average = _average(previous, stat_name)
        delta = round(last_average - previous_average, 2)
        direction = "flat"
        if delta > 0.05:
            direction = "up"
        elif delta < -0.05:
            direction = "down"
        metrics.append(
            TrendMetric(
                stat_name=stat_name,
                last_average=round(last_average, 2),
                previous_average=round(previous_average, 2),
                delta=delta,
                direction=direction,
            )
        )
    return metrics


def trend_to_dict(trend: TrendMetric) -> Dict[str, object]:
    return {
        "stat_name": trend.stat_name,
        "last_average": trend.last_average,
        "previous_average": trend.previous_average,
        "delta": trend.delta,
        "direction": trend.direction,
    }


def _average(rows: List[MatchParticipant], stat_name: str) -> float:
    if not rows:
        return 0.0
    values = [_metric_value(row, stat_name) for row in rows]
    return sum(values) / len(values)


def _metric_value(row: MatchParticipant, stat_name: str) -> float:
    if stat_name == "shot_efficiency":
        shots = float(row.stats.get("shots") or 0)
        goals = float(row.stats.get("goals") or 0)
        return goals / shots if shots > 0 else 0.0
    return float(row.stats.get(stat_name) or 0)
