"""GUI-friendly wrapper for advanced inference and advanced event queries."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime
import json
from pathlib import Path
import statistics
from typing import Any, Iterable, Optional

from arena_coach.database import connect_database
from arena_coach.inference import AdvancedInferenceService
from arena_coach.repositories import players_repo, profiles_repo


class AdvancedAnalysisService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self._service = AdvancedInferenceService(self.database_path)

    def infer_match(self, match_id: int, *, force: bool = False) -> dict[str, Any]:
        result = self._service.infer_match(match_id, force=force)
        return {
            "match_id": result.match_id,
            "raw_log_path": result.raw_log_path,
            "advanced_events_saved": result.advanced_events_saved,
            "deleted_existing_events": result.deleted_existing_events,
            "advanced_player_metrics_saved": result.advanced_player_metrics_saved,
            "deleted_existing_metrics": result.deleted_existing_metrics,
            "event_counts": result.event_counts,
            "orientation": result.orientation,
        }

    def infer_latest(self, *, force: bool = False) -> dict[str, Any]:
        result = self._service.infer_latest(force=force)
        return {
            "match_id": result.match_id,
            "raw_log_path": result.raw_log_path,
            "advanced_events_saved": result.advanced_events_saved,
            "deleted_existing_events": result.deleted_existing_events,
            "advanced_player_metrics_saved": result.advanced_player_metrics_saved,
            "deleted_existing_metrics": result.deleted_existing_metrics,
            "event_counts": result.event_counts,
            "orientation": result.orientation,
        }

    def infer_all_finalized(self, *, force: bool = False) -> dict[str, Any]:
        return self._service.infer_all_finalized(force=force)

    def summary(
        self,
        match_id: int,
        *,
        min_confidence: str = "medium",
        confidence_levels: Optional[list[str]] = None,
        event_type: Optional[str] = None,
        event_types: Optional[Iterable[str]] = None,
        player_id: Optional[int] = None,
        include_low_confidence: bool = False,
    ) -> dict[str, Any]:
        selected_event_types = _normalized_event_types(event_types, fallback=event_type)
        delegate_event_type = selected_event_types[0] if len(selected_event_types) == 1 else None
        payload = self._service.summary(
            match_id,
            min_confidence=min_confidence,
            confidence_levels=confidence_levels,
            event_type=delegate_event_type,
            player_id=player_id,
            include_low_confidence=include_low_confidence,
        )
        if len(selected_event_types) > 1:
            payload = _filter_advanced_summary_payload(payload, set(selected_event_types))
        return payload

    def timeline(
        self,
        match_id: int,
        *,
        min_confidence: str = "medium",
        confidence_levels: Optional[list[str]] = None,
        event_type: Optional[str] = None,
        event_types: Optional[Iterable[str]] = None,
        player_id: Optional[int] = None,
        include_low_confidence: bool = False,
    ) -> list[dict[str, Any]]:
        return self._service.timeline(
            match_id,
            min_confidence=min_confidence,
            confidence_levels=confidence_levels,
            event_type=event_type if not event_types else None,
            player_id=player_id,
            include_low_confidence=include_low_confidence,
        ) if not event_types else self.summary(
            match_id,
            min_confidence=min_confidence,
            confidence_levels=confidence_levels,
            event_types=event_types,
            player_id=player_id,
            include_low_confidence=include_low_confidence,
        )["timeline"]

    def player(
        self,
        player_id: int,
        *,
        min_confidence: str = "medium",
        confidence_levels: Optional[list[str]] = None,
        event_type: Optional[str] = None,
        include_low_confidence: bool = False,
    ) -> dict[str, Any]:
        return self._service.player(
            player_id,
            min_confidence=min_confidence,
            confidence_levels=confidence_levels,
            event_type=event_type,
            include_low_confidence=include_low_confidence,
        )

    def player_metric_summary(self, player_id: int, filters: Optional["StatsFilter"] = None) -> dict[str, Any]:
        from arena_coach.services.stats_service import DatabaseStatsService
        from arena_coach.stats.stat_filters import StatsFilter

        active_filters = filters or StatsFilter()
        engine = DatabaseStatsService(self.database_path)._engine()
        filtered_matches = engine._apply_match_filters(engine.matches, active_filters)
        allowed_match_ids = [int(match.id) for match in filtered_matches]

        with _connection(self.database_path) as connection:
            player = players_repo.get_player(connection, player_id)
            active_profile = profiles_repo.get_active_profile(connection)
            baseline_source_matches = engine.matches
            if active_profile is not None:
                baseline_source_matches = [
                    match
                    for match in engine.matches
                    if int(match.user_profile_id or -1) == int(active_profile["id"])
                ]
            baseline_match_ids = _competitive_baseline_match_ids(baseline_source_matches, active_filters)
            if player is None:
                raise ValueError(f"Player id {player_id} does not exist.")
            if not allowed_match_ids:
                return {
                    "player_id": player_id,
                    "display_name": player["canonical_name"],
                    "match_ids": [],
                    "match_count": 0,
                    "metric_rounds_considered": 0,
                    "category_breakdown": {},
                    "warnings": ["No matches matched the current filters."],
                    "competitive_baseline_sample_size": 0,
                    "competitive_baseline_match_ids": [],
                }

            placeholders = ",".join("?" for _ in allowed_match_ids)
            metric_rows = list(
                connection.execute(
                    f"""
                    SELECT
                        apm.*,
                        m.display_name,
                        m.started_at,
                        m.result,
                        m.total_rounds_played
                    FROM advanced_player_metrics apm
                    JOIN matches m ON m.id = apm.match_id
                    WHERE apm.player_id = ? AND apm.match_id IN ({placeholders})
                    ORDER BY COALESCE(m.started_at, m.created_at) DESC, apm.match_id DESC, apm.id
                    """,
                    (player_id, *allowed_match_ids),
                )
            )
            stat_rows = list(
                connection.execute(
                    f"""
                    SELECT
                        mps.*,
                        m.total_rounds_played
                    FROM match_player_stats mps
                    JOIN matches m ON m.id = mps.match_id
                    WHERE mps.player_id = ? AND mps.match_id IN ({placeholders})
                    ORDER BY COALESCE(m.started_at, m.created_at) DESC, mps.match_id DESC, mps.id
                    """,
                    (player_id, *allowed_match_ids),
                )
            )
            baseline_rows: list[Any] = []
            if baseline_match_ids:
                baseline_placeholders = ",".join("?" for _ in baseline_match_ids)
                if active_profile is not None:
                    baseline_rows = list(
                        connection.execute(
                            f"""
                            SELECT
                                apm.*,
                                m.match_classification,
                                m.private_match_type,
                                m.total_rounds_played,
                                mps.points,
                                mps.goals,
                                mps.assists,
                                mps.saves,
                                mps.stuns,
                                mps.steals,
                                mps.shots,
                                mps.passes,
                                mps.catches,
                                mps.turnovers,
                                mps.interceptions,
                                mps.blocks,
                                mps.possession_time,
                                mps.metadata_json AS stat_metadata_json
                            FROM advanced_player_metrics apm
                            JOIN matches m ON m.id = apm.match_id
                            LEFT JOIN match_player_stats mps
                                ON mps.match_id = apm.match_id
                                AND COALESCE(mps.player_id, -1) = COALESCE(apm.player_id, -1)
                                AND lower(COALESCE(mps.match_alias, '')) = lower(COALESCE(apm.match_alias, ''))
                                AND lower(COALESCE(mps.team, '')) = lower(COALESCE(apm.team, ''))
                            WHERE
                                m.user_profile_id = ?
                                AND apm.match_id IN ({baseline_placeholders})
                                AND lower(COALESCE(apm.team, '')) IN ('blue', 'orange')
                            ORDER BY apm.match_id, apm.team, apm.match_alias, apm.id
                            """,
                            (int(active_profile["id"]), *baseline_match_ids),
                        )
                    )
                else:
                    baseline_rows = list(
                        connection.execute(
                            f"""
                            SELECT
                                apm.*,
                                m.match_classification,
                                m.private_match_type,
                                m.total_rounds_played,
                                mps.points,
                                mps.goals,
                                mps.assists,
                                mps.saves,
                                mps.stuns,
                                mps.steals,
                                mps.shots,
                                mps.passes,
                                mps.catches,
                                mps.turnovers,
                                mps.interceptions,
                                mps.blocks,
                                mps.possession_time,
                                mps.metadata_json AS stat_metadata_json
                            FROM advanced_player_metrics apm
                            JOIN matches m ON m.id = apm.match_id
                            LEFT JOIN match_player_stats mps
                                ON mps.match_id = apm.match_id
                                AND COALESCE(mps.player_id, -1) = COALESCE(apm.player_id, -1)
                                AND lower(COALESCE(mps.match_alias, '')) = lower(COALESCE(apm.match_alias, ''))
                                AND lower(COALESCE(mps.team, '')) = lower(COALESCE(apm.team, ''))
                            WHERE
                                apm.match_id IN ({baseline_placeholders})
                                AND lower(COALESCE(apm.team, '')) IN ('blue', 'orange')
                            ORDER BY apm.match_id, apm.team, apm.match_alias, apm.id
                            """,
                            tuple(baseline_match_ids),
                        )
                    )

        if not active_filters.include_afk_players:
            baseline_rows = [row for row in baseline_rows if not _row_afk_suspected(row)]

        aggregate = _aggregate_local_metric_rows(metric_rows, stat_rows)
        baselines = _competitive_category_baselines(baseline_rows)
        category_breakdown = _build_category_breakdown(aggregate, baselines=baselines) if metric_rows else {}
        warnings: list[str] = []
        if not metric_rows:
            warnings.append("No advanced player metrics are available for this player in the current filters yet.")

        return {
            "player_id": player_id,
            "display_name": player["canonical_name"],
            "match_ids": sorted({int(row["match_id"]) for row in metric_rows or stat_rows}, reverse=True),
            "match_count": len({int(row["match_id"]) for row in metric_rows or stat_rows}),
            "metric_rounds_considered": aggregate["rounds"],
            "category_breakdown": category_breakdown,
            "warnings": warnings,
            "competitive_baseline_sample_size": len(baseline_rows),
            "competitive_baseline_match_ids": sorted({int(row["match_id"]) for row in baseline_rows}),
        }

    def local_user_summary(
        self,
        *,
        confidence_levels: Optional[Iterable[str]] = None,
        filters: Optional["StatsFilter"] = None,
    ) -> dict[str, Any]:
        from arena_coach.services.stats_service import DatabaseStatsService
        from arena_coach.stats.stat_filters import StatsFilter

        selected_levels = _normalized_confidence_levels(confidence_levels)
        active_filters = filters or StatsFilter()
        engine = DatabaseStatsService(self.database_path)._engine()
        with _connection(self.database_path) as connection:
            active = profiles_repo.get_active_profile(connection)
            if active is None:
                return {
                    "active_profile": None,
                    "confidence_levels": selected_levels,
                    "warnings": ["No active profile. Create or select one first."],
                    "event_counts": {},
                    "confidence_counts": {},
                    "recent_matches": [],
                    "transitions": {},
                    "total_finalized_matches": 0,
                    "matches_with_advanced_data": 0,
                }

            profile_matches = [
                match
                for match in engine.matches
                if int(match.user_profile_id or -1) == int(active["id"])
            ]
            filtered_profile_matches = engine._apply_match_filters(profile_matches, active_filters)
            allowed_match_ids = [int(match.id) for match in filtered_profile_matches]
            baseline_match_ids = _competitive_baseline_match_ids(profile_matches, active_filters)

            player_rows = connection.execute(
                """
                SELECT DISTINCT
                    mp.player_id,
                    COALESCE(p.canonical_name, mp.match_alias) AS display_name
                FROM matches m
                JOIN match_players mp ON mp.match_id = m.id
                LEFT JOIN players p ON p.id = mp.player_id
                WHERE
                    m.finalized = 1
                    AND m.user_profile_id = ?
                    AND mp.is_user = 1
                    AND mp.player_id IS NOT NULL
                ORDER BY lower(COALESCE(p.canonical_name, mp.match_alias)), mp.player_id
                """,
                (int(active["id"]),),
            ).fetchall()

            total_finalized_matches = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM matches
                    WHERE finalized = 1 AND user_profile_id = ?
                    """,
                    (int(active["id"]),),
                ).fetchone()[0]
            )

            player_ids = [int(row["player_id"]) for row in player_rows if row["player_id"] is not None]
            player_names = [str(row["display_name"]) for row in player_rows]
            if not player_ids:
                return {
                    "active_profile": {
                        "id": int(active["id"]),
                        "display_name": active["display_name"],
                        "primary_echo_name": active["primary_echo_name"],
                    },
                    "confidence_levels": selected_levels,
                    "warnings": ["No finalized self-mapped canonical player was found for the active profile yet."],
                    "event_counts": {},
                    "confidence_counts": {},
                    "recent_matches": [],
                    "transitions": {},
                    "total_finalized_matches": total_finalized_matches,
                    "matches_with_advanced_data": 0,
                    "canonical_player_names": [],
                }
            if not allowed_match_ids:
                return {
                    "active_profile": {
                        "id": int(active["id"]),
                        "display_name": active["display_name"],
                        "primary_echo_name": active["primary_echo_name"],
                    },
                    "confidence_levels": selected_levels,
                    "warnings": ["No finalized self matches matched the current filters."],
                    "canonical_player_names": player_names,
                    "event_counts": {},
                    "display_event_totals": {},
                    "event_averages_per_round": {},
                    "confidence_counts": {},
                    "recent_matches": [],
                    "transitions": {},
                    "total_finalized_matches": total_finalized_matches,
                    "matches_with_advanced_data": 0,
                    "metric_rounds_considered": 0,
                    "total_rounds_considered": 0,
                    "total_advanced_events": 0,
                    "competitive_baseline_sample_size": 0,
                    "competitive_baseline_match_ids": [],
                    "category_breakdown": {},
                }

            placeholders = ",".join("?" for _ in player_ids)
            allowed_placeholders = ",".join("?" for _ in allowed_match_ids)
            rows = list(
                connection.execute(
                    f"""
                    SELECT
                        ae.*,
                        m.display_name,
                        m.started_at,
                        m.match_classification,
                        m.private_match_type,
                        m.result,
                        m.total_rounds_played
                    FROM advanced_events ae
                    JOIN matches m ON m.id = ae.match_id
                    WHERE
                        m.finalized = 1
                        AND m.user_profile_id = ?
                        AND m.id IN ({allowed_placeholders})
                        AND (
                            ae.actor_player_id IN ({placeholders})
                            OR ae.target_player_id IN ({placeholders})
                            OR ae.assist_player_id IN ({placeholders})
                        )
                    ORDER BY COALESCE(m.started_at, m.created_at) DESC, ae.match_id DESC, COALESCE(ae.start_sequence, 0), ae.id
                    """,
                    (int(active["id"]), *allowed_match_ids, *player_ids, *player_ids, *player_ids),
                )
            )
            metric_rows = list(
                connection.execute(
                    f"""
                    SELECT
                        apm.*,
                        m.display_name,
                        m.started_at,
                        m.result,
                        m.total_rounds_played
                    FROM advanced_player_metrics apm
                    JOIN matches m ON m.id = apm.match_id
                    WHERE
                        m.finalized = 1
                        AND m.user_profile_id = ?
                        AND m.id IN ({allowed_placeholders})
                        AND apm.player_id IN ({placeholders})
                    ORDER BY COALESCE(m.started_at, m.created_at) DESC, apm.match_id DESC, apm.id
                    """,
                    (int(active["id"]), *allowed_match_ids, *player_ids),
                )
            )
            stat_rows = list(
                connection.execute(
                    f"""
                    SELECT
                        mps.*,
                        m.total_rounds_played
                    FROM match_player_stats mps
                    JOIN matches m ON m.id = mps.match_id
                    WHERE
                        m.finalized = 1
                        AND m.user_profile_id = ?
                        AND m.id IN ({allowed_placeholders})
                        AND mps.player_id IN ({placeholders})
                    ORDER BY COALESCE(m.started_at, m.created_at) DESC, mps.match_id DESC, mps.id
                    """,
                    (int(active["id"]), *allowed_match_ids, *player_ids),
                )
            )
            competitive_sample_rows: list[Any] = []
            if baseline_match_ids:
                baseline_placeholders = ",".join("?" for _ in baseline_match_ids)
                competitive_sample_rows = list(
                    connection.execute(
                        f"""
                        SELECT
                            apm.*,
                            m.user_profile_id,
                            m.match_classification,
                            m.private_match_type,
                            m.total_rounds_played,
                            mps.points,
                            mps.goals,
                            mps.assists,
                            mps.saves,
                            mps.stuns,
                            mps.steals,
                            mps.shots,
                            mps.passes,
                            mps.catches,
                            mps.turnovers,
                            mps.interceptions,
                            mps.blocks,
                            mps.possession_time,
                            mps.metadata_json AS stat_metadata_json
                        FROM advanced_player_metrics apm
                        JOIN matches m ON m.id = apm.match_id
                        LEFT JOIN match_player_stats mps
                            ON mps.match_id = apm.match_id
                            AND COALESCE(mps.player_id, -1) = COALESCE(apm.player_id, -1)
                            AND lower(COALESCE(mps.match_alias, '')) = lower(COALESCE(apm.match_alias, ''))
                            AND lower(COALESCE(mps.team, '')) = lower(COALESCE(apm.team, ''))
                        WHERE
                            m.finalized = 1
                            AND m.user_profile_id = ?
                            AND apm.match_id IN ({baseline_placeholders})
                            AND lower(COALESCE(apm.team, '')) IN ('blue', 'orange')
                        ORDER BY apm.match_id, apm.team, apm.match_alias, apm.id
                        """,
                        (int(active["id"]), *baseline_match_ids),
                    )
                )

        selected_set = set(selected_levels)
        filtered_rows = [row for row in rows if str(row["confidence"] or "low").casefold() in selected_set]
        event_counts = dict(sorted(Counter(str(row["event_type"]) for row in filtered_rows).items()))
        confidence_counts = dict(sorted(Counter(str(row["confidence"] or "low") for row in filtered_rows).items()))
        transitions = _transition_summary(filtered_rows)
        display_totals = _perspective_event_totals(filtered_rows, set(player_ids))
        recent_matches = _recent_match_breakdown(filtered_rows, set(player_ids))
        total_rounds_considered = _total_rounds_considered(filtered_rows)
        metric_aggregate = _aggregate_local_metric_rows(metric_rows, stat_rows)
        event_rounds_considered = metric_aggregate["rounds"] if metric_aggregate["rounds"] > 0 else float(total_rounds_considered)
        event_averages = {
            event_type: round(count / event_rounds_considered, 3) if event_rounds_considered > 0 else 0.0
            for event_type, count in display_totals.items()
        }
        competitive_sample_rows = [row for row in competitive_sample_rows if not _row_afk_suspected(row)]
        competitive_baselines = _competitive_category_baselines(competitive_sample_rows)
        category_breakdown = _build_category_breakdown(metric_aggregate, baselines=competitive_baselines)

        warnings: list[str] = []
        if len(player_ids) > 1:
            warnings.append(
                "The active profile is mapped to multiple canonical self players across finalized matches. The summary combines them."
            )
        if not metric_rows:
            warnings.append(
                "Observer-style player metrics are not populated yet for these matches. Run advanced inference to fill them in."
            )

        return {
            "active_profile": {
                "id": int(active["id"]),
                "display_name": active["display_name"],
                "primary_echo_name": active["primary_echo_name"],
            },
            "confidence_levels": selected_levels,
            "warnings": warnings,
            "canonical_player_names": player_names,
            "event_counts": event_counts,
            "display_event_totals": display_totals,
            "event_averages_per_round": event_averages,
            "confidence_counts": confidence_counts,
            "recent_matches": recent_matches,
            "transitions": transitions,
            "total_finalized_matches": total_finalized_matches,
            "matches_with_advanced_data": len({int(row["match_id"]) for row in filtered_rows}),
            "total_advanced_events": len(filtered_rows),
            "total_rounds_considered": event_rounds_considered,
            "metric_rounds_considered": metric_aggregate["rounds"],
            "category_breakdown": category_breakdown,
            "metric_summary_note": "Category cards use estimated personal active rounds from stored per-match player metrics. Confidence toggles still affect event views only.",
            "competitive_baseline_sample_size": len(competitive_sample_rows),
            "competitive_baseline_match_ids": sorted({int(row["match_id"]) for row in competitive_sample_rows}),
        }


@contextmanager
def _connection(database_path: Path):
    connection = connect_database(database_path)
    try:
        yield connection
    finally:
        connection.close()


def _normalized_confidence_levels(levels: Optional[Iterable[str]]) -> list[str]:
    allowed = {"high", "medium", "low"}
    normalized = [str(level).casefold() for level in (levels or ("high", "medium")) if str(level).casefold() in allowed]
    if not normalized:
        normalized = ["high", "medium"]
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(set(normalized), key=lambda level: order[level])


def _normalized_event_types(event_types: Optional[Iterable[str]], fallback: Optional[str] = None) -> list[str]:
    if event_types is None:
        if not fallback:
            return []
        return [str(fallback).casefold()]
    normalized = [str(event_type).casefold() for event_type in event_types if str(event_type).strip()]
    if not normalized:
        return []
    if "all" in normalized:
        return []
    return list(dict.fromkeys(normalized))


def _filter_advanced_summary_payload(payload: dict[str, Any], selected_event_types: set[str]) -> dict[str, Any]:
    filtered_timeline = [
        row for row in payload.get("timeline") or []
        if str(row.get("event_type") or "").casefold() in selected_event_types
    ]
    counts = dict(sorted(Counter(str(row.get("event_type") or "") for row in filtered_timeline).items()))
    return {
        **payload,
        "counts": counts,
        "timeline": filtered_timeline,
        "player_breakdown": _filtered_player_breakdown(filtered_timeline, payload.get("player_breakdown") or []),
    }


def _filtered_player_breakdown(
    timeline_rows: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    static_by_alias = {str(row.get("alias") or ""): row for row in existing_rows if str(row.get("alias") or "").strip()}
    counts_by_alias: dict[str, Counter[str]] = {}
    for row in timeline_rows:
        event_type = str(row.get("event_type") or "")
        actor_alias = row.get("actor_alias")
        target_alias = row.get("target_alias")
        assist_alias = row.get("assist_alias")
        if event_type in {"turnover", "intercepted_pass"}:
            if actor_alias:
                counts_by_alias.setdefault(str(actor_alias), Counter())["turnover"] += 1
            if target_alias:
                counts_by_alias.setdefault(str(target_alias), Counter())["interception"] += 1
            continue
        for alias in (actor_alias, target_alias, assist_alias):
            if alias:
                counts_by_alias.setdefault(str(alias), Counter()).update([event_type])

    filtered_rows: list[dict[str, Any]] = []
    for alias in sorted(counts_by_alias, key=str.casefold):
        base = static_by_alias.get(alias, {})
        filtered_rows.append(
            {
                "alias": alias,
                "player_id": base.get("player_id"),
                "canonical_name": base.get("canonical_name"),
                "team": base.get("team"),
                "counts": dict(sorted(counts_by_alias[alias].items())),
                "stats": base.get("stats") or {"points": 0, "goals": 0, "assists": 0, "saves": 0, "stuns": 0},
            }
        )
    return filtered_rows


def _transition_summary(rows: list[Any]) -> dict[str, Any]:
    offense_values = [float(row["value"]) for row in rows if row["event_type"] == "offensive_transition_time" and row["value"] is not None]
    defense_values = [float(row["value"]) for row in rows if row["event_type"] == "defensive_transition_time" and row["value"] is not None]
    return {
        "average_time_to_offense": round(sum(offense_values) / len(offense_values), 3) if offense_values else None,
        "average_time_to_defense": round(sum(defense_values) / len(defense_values), 3) if defense_values else None,
        "offense_samples": len(offense_values),
        "defense_samples": len(defense_values),
    }


def _recent_match_breakdown(rows: list[Any], player_ids: set[int]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        match_id = int(row["match_id"])
        entry = grouped.setdefault(
            match_id,
            {
                "match_id": match_id,
                "display_name": row["display_name"],
                "started_at": row["started_at"],
                "match_classification": row["match_classification"],
                "private_match_type": row["private_match_type"],
                "result": row["result"],
                "total_rounds_played": row["total_rounds_played"],
                "raw_counts": Counter(),
                "offense_values": [],
                "defense_values": [],
                "rows": [],
            },
        )
        event_type = str(row["event_type"])
        entry["raw_counts"][event_type] += 1
        entry["rows"].append(row)
        if event_type == "offensive_transition_time" and row["value"] is not None:
            entry["offense_values"].append(float(row["value"]))
        if event_type == "defensive_transition_time" and row["value"] is not None:
            entry["defense_values"].append(float(row["value"]))

    ordered = sorted(
        grouped.values(),
        key=lambda item: (str(item["started_at"] or ""), int(item["match_id"])),
        reverse=True,
    )
    rows_out: list[dict[str, Any]] = []
    for entry in ordered[:10]:
        counts = _perspective_event_totals(entry["rows"], player_ids)
        rows_out.append(
            {
                "match_id": entry["match_id"],
                "display_name": entry["display_name"],
                "result": entry["result"],
                "match_classification": entry["match_classification"],
                "private_match_type": entry["private_match_type"],
                "counts": dict(sorted(counts.items())),
                "average_time_to_offense": (
                    round(sum(entry["offense_values"]) / len(entry["offense_values"]), 3)
                    if entry["offense_values"]
                    else None
                ),
                "average_time_to_defense": (
                    round(sum(entry["defense_values"]) / len(entry["defense_values"]), 3)
                    if entry["defense_values"]
                    else None
                ),
            }
        )
    return rows_out


def _total_rounds_considered(rows: list[Any]) -> float:
    rounds_by_match: dict[int, int] = {}
    for row in rows:
        match_id = int(row["match_id"])
        if match_id in rounds_by_match:
            continue
        rounds_raw = row["total_rounds_played"]
        try:
            rounds = int(rounds_raw) if rounds_raw is not None else 0
        except (TypeError, ValueError):
            rounds = 0
        rounds_by_match[match_id] = rounds if rounds > 0 else 1
    return float(sum(rounds_by_match.values()))


def _aggregate_local_metric_rows(metric_rows: list[Any], stat_rows: list[Any]) -> dict[str, Any]:
    metric_match_ids = {int(row["match_id"]) for row in metric_rows}
    relevant_stat_rows = [
        row for row in stat_rows
        if not metric_match_ids or int(row["match_id"]) in metric_match_ids
    ]
    rounds_by_match = _personal_rounds_by_match(metric_rows, relevant_stat_rows)
    totals = Counter()
    metadata_totals = Counter()
    for row in metric_rows:
        _merge_metric_row_into_totals(row, totals, metadata_totals)

    stat_totals = Counter()
    for row in relevant_stat_rows:
        _merge_stat_row_into_totals(row, stat_totals)

    rounds = float(sum(rounds_by_match.values()))
    goals_2_open = _safe_number(totals["goals_2_open_net"])
    goals_2_guarded = _safe_number(totals["goals_2_guarded"])
    goals_3_open = _safe_number(totals["goals_3_open_net"])
    goals_3_guarded = _safe_number(totals["goals_3_guarded"])
    goals_total = int(goals_2_open + goals_2_guarded + goals_3_open + goals_3_guarded)
    raw_goal_points_total = int((goals_2_open * 2.0) + (goals_2_guarded * 2.0) + (goals_3_open * 3.0) + (goals_3_guarded * 3.0))
    failed_shots_total = int(totals["missed_shots"]) + int(totals["shots_saved_against"])
    shooting_percentage = (
        round((float(goals_total) / float(failed_shots_total)) * 100.0, 2)
        if failed_shots_total > 0
        else None
    )
    bounded_conversion = (
        round((float(goals_total) / float(goals_total + failed_shots_total)) * 100.0, 2)
        if (goals_total + failed_shots_total) > 0
        else None
    )

    return {
        "rounds": rounds,
        "metric_totals": dict(totals),
        "stat_totals": dict(stat_totals),
        "metadata_totals": dict(metadata_totals),
        "goals_total": goals_total,
        "raw_goal_points_total": raw_goal_points_total,
        "failed_shots_total": failed_shots_total,
        "shooting_percentage": shooting_percentage,
        "bounded_conversion": bounded_conversion,
    }


def _aggregate_competitive_sample_row(row: Any) -> dict[str, Any]:
    totals = Counter()
    stat_totals = Counter()
    metadata_totals = Counter()
    _merge_metric_row_into_totals(row, totals, metadata_totals)
    _merge_stat_row_into_totals(row, stat_totals)
    rounds = _row_active_rounds_estimate(row, fallback_to_total_rounds=True)

    goals_2_open = _safe_number(totals["goals_2_open_net"])
    goals_2_guarded = _safe_number(totals["goals_2_guarded"])
    goals_3_open = _safe_number(totals["goals_3_open_net"])
    goals_3_guarded = _safe_number(totals["goals_3_guarded"])
    goals_total = int(goals_2_open + goals_2_guarded + goals_3_open + goals_3_guarded)
    raw_goal_points_total = int((goals_2_open * 2.0) + (goals_2_guarded * 2.0) + (goals_3_open * 3.0) + (goals_3_guarded * 3.0))
    failed_shots_total = int(totals["missed_shots"]) + int(totals["shots_saved_against"])
    shooting_percentage = (
        round((float(goals_total) / float(failed_shots_total)) * 100.0, 2)
        if failed_shots_total > 0
        else None
    )
    bounded_conversion = (
        round((float(goals_total) / float(goals_total + failed_shots_total)) * 100.0, 2)
        if (goals_total + failed_shots_total) > 0
        else None
    )
    return {
        "rounds": rounds,
        "metric_totals": dict(totals),
        "stat_totals": dict(stat_totals),
        "metadata_totals": dict(metadata_totals),
        "goals_total": goals_total,
        "raw_goal_points_total": raw_goal_points_total,
        "failed_shots_total": failed_shots_total,
        "shooting_percentage": shooting_percentage,
        "bounded_conversion": bounded_conversion,
    }


def _build_category_breakdown(aggregate: dict[str, Any], baselines: Optional[dict[str, dict[str, Any]]] = None) -> dict[str, Any]:
    rounds = float(aggregate.get("rounds") or 0.0)
    totals = aggregate.get("metric_totals") or {}
    stat_totals = aggregate.get("stat_totals") or {}
    metadata_totals = aggregate.get("metadata_totals") or {}

    goals_2_guarded = _safe_number(totals.get("goals_2_guarded"))
    goals_3_guarded = _safe_number(totals.get("goals_3_guarded"))
    goals_2_open = _safe_number(totals.get("goals_2_open_net"))
    goals_3_open = _safe_number(totals.get("goals_3_open_net"))
    missed_shots = _safe_number(totals.get("missed_shots"))
    shots_saved = _safe_number(totals.get("shots_saved_against"))
    dunk_like_open_2s = _safe_number(metadata_totals.get("dunk_like_open_2s"))
    dunk_like_guarded_2s = _safe_number(metadata_totals.get("dunk_like_guarded_2s"))
    shooting_percentage = aggregate.get("shooting_percentage")
    bounded_conversion = aggregate.get("bounded_conversion")
    adjusted_guarded_2_for_bonus = max(0.0, goals_2_guarded - dunk_like_guarded_2s)
    actual_scoreboard_points = _safe_number(stat_totals.get("points"))
    raw_goal_points_total = _safe_number(aggregate.get("raw_goal_points_total"))
    if actual_scoreboard_points <= 0.0:
        actual_scoreboard_points = raw_goal_points_total

    goals_total = _safe_number(aggregate.get("goals_total"))
    blocked_shots = _safe_number(totals.get("blocked_shots"))
    stuffed_shots = _safe_number(totals.get("stuffed_shots"))
    base_saves = _safe_number(stat_totals.get("saves"))
    base_goals = _safe_number(stat_totals.get("goals"))
    base_assists = _safe_number(stat_totals.get("assists"))
    base_points = _safe_number(stat_totals.get("points"))
    base_passes = _safe_number(stat_totals.get("passes"))
    base_catches = _safe_number(stat_totals.get("catches"))
    base_steals = _safe_number(stat_totals.get("steals"))
    base_turnovers = _safe_number(stat_totals.get("turnovers"))
    base_interceptions = _safe_number(stat_totals.get("interceptions"))
    base_blocks = _safe_number(stat_totals.get("blocks"))
    possession_time = float(stat_totals.get("possession_time") or 0.0)

    completed_passes = _safe_number(totals.get("completed_passes"))
    inferred_catches = _safe_number(totals.get("inferred_catches"))
    initiators = _safe_number(totals.get("initiators"))
    open_for_pass_samples = _safe_number(totals.get("open_for_pass_samples"))
    lane_blocked_samples = _safe_number(totals.get("lane_blocked_samples"))
    lane_blocks = _safe_number(totals.get("lane_blocks"))
    clear_attempts = _safe_number(totals.get("clear_attempts"))
    successful_clears = _safe_number(totals.get("successful_clears"))
    failed_clears = _safe_number(totals.get("failed_clears"))
    inferred_turnovers = _safe_number(totals.get("inferred_turnovers"))
    inferred_interceptions = _safe_number(totals.get("inferred_interceptions"))
    steal_takeaways = _safe_number(totals.get("steal_takeaways"))
    stun_takeaways = _safe_number(totals.get("stun_takeaways"))
    tight_man_coverage_samples = _safe_number(totals.get("tight_man_coverage_samples"))
    loose_man_coverage_samples = _safe_number(totals.get("loose_man_coverage_samples"))
    no_man_coverage_samples = _safe_number(totals.get("no_man_coverage_samples"))
    goalie_coverage_samples = _safe_number(totals.get("goalie_coverage_samples"))

    passes_to_open_receiver = _safe_number(metadata_totals.get("passes_to_open_receiver"))
    passes_to_covered_receiver = _safe_number(metadata_totals.get("passes_to_covered_receiver"))
    catches_open = _safe_number(metadata_totals.get("catches_open"))
    catches_covered = _safe_number(metadata_totals.get("catches_covered"))
    lane_coverage_failures = _safe_number(metadata_totals.get("lane_coverage_failures"))

    coverage_sample_total = (
        tight_man_coverage_samples + loose_man_coverage_samples + no_man_coverage_samples + goalie_coverage_samples
    )
    lane_block_rate = _rate(lane_blocks, coverage_sample_total + lane_blocks)
    tight_coverage_rate = _rate(tight_man_coverage_samples, coverage_sample_total)
    loose_coverage_rate = _rate(loose_man_coverage_samples, coverage_sample_total)
    no_man_coverage_rate = _rate(no_man_coverage_samples, coverage_sample_total)
    goalie_coverage_rate = _rate(goalie_coverage_samples, coverage_sample_total)
    open_pass_rate = _rate(open_for_pass_samples, open_for_pass_samples + lane_blocked_samples)
    clear_success_rate = _rate(successful_clears, clear_attempts)

    estimated_possession_releases = (
        completed_passes
        + clear_attempts
        + missed_shots
        + shots_saved
        + blocked_shots
        + stuffed_shots
        + goals_total
        + inferred_turnovers
    )
    avg_possession_time_per_touch = (
        round(possession_time / estimated_possession_releases, 3)
        if possession_time > 0.0 and estimated_possession_releases > 0.0
        else None
    )

    actual_points_per_round = _per_round(actual_scoreboard_points, rounds)
    shot_type_bonus_per_round = _per_round(
        (goals_3_guarded * 2.0)
        + (adjusted_guarded_2_for_bonus * 1.0)
        + (goals_3_open * 1.0),
        rounds,
    )
    miss_save_penalty_per_round = _per_round(
        (missed_shots * 0.5) + (shots_saved * 1.0),
        rounds,
    )
    effective_shooting_points_per_round = (
        actual_points_per_round + shot_type_bonus_per_round - miss_save_penalty_per_round
        if rounds
        else 0.0
    )
    shooting_overall = _normalized_category_score("shooting", effective_shooting_points_per_round, baselines)
    if shooting_overall is None and rounds:
        shooting_overall = round(max(0.0, (effective_shooting_points_per_round / 15.0) * 100.0), 1)

    avg_time_to_offense = _average_from_totals(totals.get("offensive_transition_total"), totals.get("offensive_transition_count"))
    avg_time_to_defense = _average_from_totals(totals.get("defensive_transition_total"), totals.get("defensive_transition_count"))
    speed_effective_value = _weighted_score(
        [
            (_inverse_time_score(avg_time_to_offense, 2.5), 0.45),
            (_inverse_time_score(avg_time_to_defense, 2.5), 0.45),
            (_inverse_time_score(avg_possession_time_per_touch, 2.0), 0.10),
        ]
    )
    speed_overall = _normalized_category_score("speed", speed_effective_value, baselines)
    if speed_overall is None:
        speed_overall = speed_effective_value

    possession_effective_per_round = (
        _per_round(inferred_interceptions, rounds) * 2.5
        + _per_round(steal_takeaways, rounds) * 1.5
        + _per_round(stun_takeaways, rounds) * 1.25
        + _per_round(base_steals, rounds) * 1.0
        - _per_round(inferred_turnovers, rounds) * 1.5
        - _per_round(base_turnovers, rounds) * 1.0
    )
    if avg_possession_time_per_touch is not None:
        possession_effective_per_round += min(float(avg_possession_time_per_touch) / 3.0, 1.0) * 0.75
    possession_overall = _normalized_category_score("possession", possession_effective_per_round, baselines)
    if possession_overall is None:
        possession_overall = _score_from_target(possession_effective_per_round, 4.0)

    offense_effective_per_round = (
        _per_round(completed_passes, rounds) * 0.4
        + _per_round(inferred_catches, rounds) * 0.25
        + _per_round(initiators, rounds) * 1.5
        + _per_round(base_assists, rounds) * 1.5
        + _per_round(base_goals, rounds) * 1.25
        + _per_round(base_points, rounds) * 0.35
        + _per_round(passes_to_open_receiver, rounds) * 0.35
        + _per_round(catches_open, rounds) * 0.25
        - _per_round(passes_to_covered_receiver, rounds) * 0.25
        - _per_round(catches_covered, rounds) * 0.10
    )
    if open_pass_rate is not None:
        offense_effective_per_round += (float(open_pass_rate) / 100.0) * 1.0
    offense_overall = _normalized_category_score("offense", offense_effective_per_round, baselines)
    if offense_overall is None:
        offense_overall = _score_from_target(offense_effective_per_round, 6.0)

    tight_coverage_score = (float(tight_coverage_rate) / 100.0) * 1.0 if tight_coverage_rate is not None else 0.0
    goalie_coverage_score = (float(goalie_coverage_rate) / 100.0) * 0.75 if goalie_coverage_rate is not None else 0.0
    lane_block_score = (float(lane_block_rate) / 100.0) * 1.5 if lane_block_rate is not None else 0.0
    loose_coverage_penalty = (float(loose_coverage_rate) / 100.0) * 0.25 if loose_coverage_rate is not None else 0.0
    no_man_penalty = (float(no_man_coverage_rate) / 100.0) * 1.5 if no_man_coverage_rate is not None else 0.0
    defense_effective_per_round = (
        _per_round(base_saves, rounds) * 3.0
        + _per_round(inferred_interceptions, rounds) * 1.5
        + _per_round(steal_takeaways, rounds) * 1.25
        + _per_round(stun_takeaways, rounds) * 1.0
        + _per_round(blocked_shots, rounds) * 1.0
        + _per_round(stuffed_shots, rounds) * 1.25
        + _per_round(base_blocks, rounds) * 0.5
        + _per_round(base_steals, rounds) * 0.5
        + _per_round(base_interceptions, rounds) * 0.5
        + tight_coverage_score
        + goalie_coverage_score
        + lane_block_score
        - loose_coverage_penalty
        - no_man_penalty
        - _per_round(lane_coverage_failures, rounds) * 0.75
    )
    defense_overall = _normalized_category_score("defense", defense_effective_per_round, baselines)
    if defense_overall is None:
        defense_overall = _score_from_target(defense_effective_per_round, 4.0)

    passing_effective_per_round = (
        _per_round(completed_passes, rounds) * 1.2
        + _per_round(inferred_catches, rounds) * 0.6
        + _per_round(successful_clears, rounds) * 1.0
        + _per_round(base_catches, rounds) * 0.5
        + _per_round(base_passes, rounds) * 0.5
        + _per_round(passes_to_open_receiver, rounds) * 0.35
        + _per_round(catches_open, rounds) * 0.25
        - _per_round(failed_clears, rounds) * 1.0
        - _per_round(passes_to_covered_receiver, rounds) * 0.35
        - _per_round(catches_covered, rounds) * 0.15
    )
    if clear_success_rate is not None:
        passing_effective_per_round += (float(clear_success_rate) / 100.0) * 1.5
    passing_overall = _normalized_category_score("passing", passing_effective_per_round, baselines)
    if passing_overall is None:
        passing_overall = _score_from_target(passing_effective_per_round, 5.0)

    normalized_note_suffix = ""
    if baselines:
        normalized_note_suffix = " Displayed score is normalized against the current competitive sample."

    return {
        "shooting": {
            "title": "Shooting",
            "overall_score": shooting_overall,
            "score_note": (
                "Round-based shooting score anchored to actual scoreboard points per round. "
                "Guarded 3s add +2 each; guarded 2s and open 3s add +1 each; "
                "open 2s add no bonus; missed shots subtract 0.5 each; saved shots subtract 1 each. "
                "Dunk-like guarded 2s are treated like open 2s for bonus purposes. "
                "15 effective shooting points per round maps to 100. Scores above 100 are allowed."
                + normalized_note_suffix
            ),
            "metrics": [
                _metric_entry("Guarded 3s", goals_3_guarded, rounds),
                _metric_entry("Guarded 2s", goals_2_guarded, rounds),
                _metric_entry("Open 3s", goals_3_open, rounds),
                _metric_entry("Open 2s", goals_2_open, rounds),
                _metric_entry("Possible dunk-like open 2s", dunk_like_open_2s, rounds),
                _metric_entry("Possible dunk-like guarded 2s", dunk_like_guarded_2s, rounds),
                _metric_entry("Missed shots", missed_shots, rounds),
                _metric_entry("Shots saved by goalie", shots_saved, rounds),
                _value_entry("Shooting percentage", _percentage_text(shooting_percentage), "Goals / (missed + saved)"),
                _value_entry(
                    "Actual scoreboard points / round",
                    f"{actual_points_per_round:.2f}" if rounds else "No round data",
                    "Based on real in-game points.",
                ),
                _value_entry(
                    "Shot-type bonus / round",
                    f"{shot_type_bonus_per_round:.2f}" if rounds else "No round data",
                    "Guarded 3s = +2, guarded 2s/open 3s = +1, open 2s = +0.",
                ),
                _value_entry(
                    "Miss/save penalty / round",
                    f"{miss_save_penalty_per_round:.2f}" if rounds else "No round data",
                    "Misses = -0.5, saved shots = -1.",
                ),
                _value_entry(
                    "Effective shooting points / round",
                    f"{effective_shooting_points_per_round:.2f}" if rounds else "No round data",
                    "15 effective points per round maps to a score of 100.",
                ),
            ],
        },
        "speed": {
            "title": "Speed",
            "overall_score": speed_overall,
            "score_note": "Lower transition times score better. Average possession time per estimated touch is used as a smaller release-speed input." + normalized_note_suffix,
            "metrics": [
                _value_entry(
                    "Average time to offense",
                    _seconds_text(avg_time_to_offense),
                    f"Samples {int(_safe_number(totals.get('offensive_transition_count')))}",
                ),
                _value_entry(
                    "Average time to defense",
                    _seconds_text(avg_time_to_defense),
                    f"Samples {int(_safe_number(totals.get('defensive_transition_count')))}",
                ),
                _value_entry(
                    "Average possession time / estimated touch",
                    _seconds_text(avg_possession_time_per_touch),
                    f"Estimated releases {int(round(estimated_possession_releases))}",
                ),
                _metric_entry("Possession time", float(stat_totals.get("possession_time") or 0.0), rounds, decimals=2, suffix="s"),
            ],
        },
        "possession": {
            "title": "Possession",
            "overall_score": possession_overall,
            "score_note": "Per-round possession control score. Interceptions and takeaways help; turnovers hurt. Average hold time per touch adds a small bonus." + normalized_note_suffix,
            "metrics": [
                _metric_entry("Inferred turnovers", _safe_number(totals.get("inferred_turnovers")), rounds),
                _metric_entry("Inferred interceptions", _safe_number(totals.get("inferred_interceptions")), rounds),
                _metric_entry("Steal takeaways", _safe_number(totals.get("steal_takeaways")), rounds),
                _metric_entry("Stun takeaways", _safe_number(totals.get("stun_takeaways")), rounds),
                _metric_entry("Base steals", _safe_number(stat_totals.get("steals")), rounds),
                _metric_entry("Base turnovers", _safe_number(stat_totals.get("turnovers")), rounds),
                _metric_entry("Possession time", float(stat_totals.get("possession_time") or 0.0), rounds, decimals=2, suffix="s"),
                _value_entry(
                    "Average possession time / estimated touch",
                    _seconds_text(avg_possession_time_per_touch),
                    f"Estimated releases {int(round(estimated_possession_releases))}",
                ),
                _value_entry(
                    "Effective possession units / round",
                    f"{possession_effective_per_round:.2f}" if rounds else "No round data",
                    "4 effective units per round maps to a score of 100.",
                ),
            ],
        },
        "offense": {
            "title": "Offense",
            "overall_score": offense_overall,
            "score_note": "Per-round offensive creation score. Points, assists, goals, initiators, and quality passing involvement all contribute." + normalized_note_suffix,
            "metrics": [
                _metric_entry("Completed passes", _safe_number(totals.get("completed_passes")), rounds),
                _metric_entry("Initiators", _safe_number(totals.get("initiators")), rounds),
                _metric_entry("Inferred catches", _safe_number(totals.get("inferred_catches")), rounds),
                _metric_entry("Assists", _safe_number(stat_totals.get("assists")), rounds),
                _metric_entry("Goals", _safe_number(stat_totals.get("goals")), rounds),
                _metric_entry("Points", _safe_number(stat_totals.get("points")), rounds),
                _metric_entry("Open-pass samples", _safe_number(totals.get("open_for_pass_samples")), rounds),
                _value_entry("Open-pass rate", _percentage_text(open_pass_rate), "Open samples / (open + lane-blocked samples)"),
                _metric_entry("Passes to open receiver", passes_to_open_receiver, rounds),
                _metric_entry("Passes to covered receiver", passes_to_covered_receiver, rounds),
                _metric_entry("Catches open", catches_open, rounds),
                _metric_entry("Catches covered", catches_covered, rounds),
                _value_entry(
                    "Effective offense units / round",
                    f"{offense_effective_per_round:.2f}" if rounds else "No round data",
                    "6 effective units per round maps to a score of 100.",
                ),
            ],
        },
        "defense": {
            "title": "Defense",
            "overall_score": defense_overall,
            "score_note": "Per-round defensive impact score. Saves, takeaways, lane-block rate, and stronger coverage help; lane failures and weak coverage hurt." + normalized_note_suffix,
            "metrics": [
                _metric_entry("Saves", _safe_number(stat_totals.get("saves")), rounds),
                _metric_entry("Inferred interceptions", _safe_number(totals.get("inferred_interceptions")), rounds),
                _metric_entry("Steal takeaways", _safe_number(totals.get("steal_takeaways")), rounds),
                _metric_entry("Stun takeaways", _safe_number(totals.get("stun_takeaways")), rounds),
                _metric_entry("Lane blocks", _safe_number(totals.get("lane_blocks")), rounds),
                _metric_entry("Tight man coverage samples", _safe_number(totals.get("tight_man_coverage_samples")), rounds),
                _metric_entry("Loose man coverage samples", _safe_number(totals.get("loose_man_coverage_samples")), rounds),
                _metric_entry("No-man coverage samples", _safe_number(totals.get("no_man_coverage_samples")), rounds),
                _metric_entry("Goalie coverage samples", _safe_number(totals.get("goalie_coverage_samples")), rounds),
                _metric_entry("Lane coverage failures", lane_coverage_failures, rounds),
                _metric_entry("Blocked shots", blocked_shots, rounds),
                _metric_entry("Stuffed shots", stuffed_shots, rounds),
                _metric_entry("Base blocks", _safe_number(stat_totals.get("blocks")), rounds),
                _metric_entry("Base steals", _safe_number(stat_totals.get("steals")), rounds),
                _metric_entry("Base interceptions", _safe_number(stat_totals.get("interceptions")), rounds),
                _value_entry("Lane-block rate", _percentage_text(lane_block_rate), "Lane blocks as a share of tracked lane+coverage samples"),
                _value_entry("Tight coverage rate", _percentage_text(tight_coverage_rate), "Share of tracked defensive coverage samples"),
                _value_entry("Goalie coverage rate", _percentage_text(goalie_coverage_rate), "Share of tracked defensive coverage samples"),
                _value_entry("Loose coverage rate", _percentage_text(loose_coverage_rate), "Share of tracked defensive coverage samples"),
                _value_entry("No-man coverage rate", _percentage_text(no_man_coverage_rate), "Share of tracked defensive coverage samples"),
                _value_entry(
                    "Effective defense units / round",
                    f"{defense_effective_per_round:.2f}" if rounds else "No round data",
                    "6 effective units per round maps to a score of 100.",
                ),
            ],
        },
        "passing": {
            "title": "Passing",
            "overall_score": passing_overall,
            "score_note": "Per-round passing score. Completed passes, catches, and successful clears help; failed clears and covered passes hurt." + normalized_note_suffix,
            "metrics": [
                _metric_entry("Completed passes", _safe_number(totals.get("completed_passes")), rounds),
                _metric_entry("Inferred catches", _safe_number(totals.get("inferred_catches")), rounds),
                _metric_entry("Clear attempts", _safe_number(totals.get("clear_attempts")), rounds),
                _metric_entry("Successful clears", _safe_number(totals.get("successful_clears")), rounds),
                _metric_entry("Failed clears", _safe_number(totals.get("failed_clears")), rounds),
                _metric_entry("Base catches", _safe_number(stat_totals.get("catches")), rounds),
                _metric_entry("Base passes", _safe_number(stat_totals.get("passes")), rounds),
                _metric_entry("Passes to open receiver", passes_to_open_receiver, rounds),
                _metric_entry("Passes to covered receiver", passes_to_covered_receiver, rounds),
                _metric_entry("Catches open", catches_open, rounds),
                _metric_entry("Catches covered", catches_covered, rounds),
                _value_entry(
                    "Clear success rate",
                    _percentage_text(clear_success_rate),
                    None,
                ),
                _value_entry(
                    "Effective passing units / round",
                    f"{passing_effective_per_round:.2f}" if rounds else "No round data",
                    "5 effective units per round maps to a score of 100.",
                ),
            ],
        },
    }


def _metric_entry(label: str, total: float, rounds: int, *, decimals: int = 0, suffix: str = "") -> dict[str, Any]:
    value = float(total)
    if decimals == 0:
        total_text = f"{int(round(value))}"
    else:
        total_text = f"{value:.{decimals}f}"
    if suffix:
        total_text = f"{total_text}{suffix}"
    per_round = _per_round(value, rounds)
    if decimals == 0:
        avg_text = f"{per_round:.2f}/rd" if rounds else "No round data"
    else:
        avg_text = f"{per_round:.2f}{suffix}/rd" if rounds else "No round data"
    return {
        "label": label,
        "value": total_text,
        "note": avg_text,
    }


def _value_entry(label: str, value: str, note: Optional[str]) -> dict[str, Any]:
    return {"label": label, "value": value, "note": note}


def _per_round(value: float, rounds: int) -> float:
    if rounds <= 0:
        return 0.0
    return float(value) / float(rounds)


def _rate(numerator: float, denominator: float) -> Optional[float]:
    if denominator <= 0:
        return None
    return (float(numerator) / float(denominator)) * 100.0


def _percentage_text(value: Optional[float]) -> str:
    if value is None:
        return "No samples"
    return f"{float(value):.2f}%"


def _competitive_category_baselines(rows: list[Any]) -> dict[str, dict[str, Any]]:
    distributions: dict[str, list[float]] = {
        "shooting": [],
        "speed": [],
        "possession": [],
        "offense": [],
        "defense": [],
        "passing": [],
    }
    for row in rows:
        aggregate = _aggregate_competitive_sample_row(row)
        raw_values = _raw_category_values(aggregate)
        for category, value in raw_values.items():
            if value is None:
                continue
            numeric = float(value)
            if numeric != numeric:
                continue
            distributions[category].append(numeric)

    baselines: dict[str, dict[str, Any]] = {}
    for category, values in distributions.items():
        if not values:
            continue
        mean_value = statistics.mean(values)
        std_value = statistics.stdev(values) if len(values) > 1 else 0.0
        scale = max(float(std_value), abs(float(mean_value)) * 0.25, 0.25)
        baselines[category] = {
            "sample_size": len(values),
            "mean": float(mean_value),
            "stdev": float(std_value),
            "scale": float(scale),
        }
    return baselines


def _normalized_category_score(
    category: str,
    raw_value: Optional[float],
    baselines: Optional[dict[str, dict[str, Any]]],
) -> Optional[float]:
    if raw_value is None or not baselines:
        return None
    baseline = baselines.get(category)
    if not baseline:
        return None
    scale = float(baseline.get("scale") or 0.0)
    if scale <= 0.0:
        return None
    mean_value = float(baseline.get("mean") or 0.0)
    z_like = (float(raw_value) - mean_value) / scale
    return round(max(0.0, 50.0 + (z_like * 18.0)), 1)


def _raw_category_values(aggregate: dict[str, Any]) -> dict[str, Optional[float]]:
    rounds = int(aggregate.get("rounds") or 0)
    if rounds <= 0:
        return {
            "shooting": None,
            "speed": None,
            "possession": None,
            "offense": None,
            "defense": None,
            "passing": None,
        }

    totals = aggregate.get("metric_totals") or {}
    stat_totals = aggregate.get("stat_totals") or {}
    metadata_totals = aggregate.get("metadata_totals") or {}

    goals_2_guarded = _safe_number(totals.get("goals_2_guarded"))
    goals_3_guarded = _safe_number(totals.get("goals_3_guarded"))
    goals_2_open = _safe_number(totals.get("goals_2_open_net"))
    goals_3_open = _safe_number(totals.get("goals_3_open_net"))
    missed_shots = _safe_number(totals.get("missed_shots"))
    shots_saved = _safe_number(totals.get("shots_saved_against"))
    dunk_like_guarded_2s = _safe_number(metadata_totals.get("dunk_like_guarded_2s"))
    adjusted_guarded_2_for_bonus = max(0.0, goals_2_guarded - dunk_like_guarded_2s)

    goals_total = _safe_number(aggregate.get("goals_total"))
    blocked_shots = _safe_number(totals.get("blocked_shots"))
    stuffed_shots = _safe_number(totals.get("stuffed_shots"))
    base_saves = _safe_number(stat_totals.get("saves"))
    base_goals = _safe_number(stat_totals.get("goals"))
    base_assists = _safe_number(stat_totals.get("assists"))
    base_points = _safe_number(stat_totals.get("points"))
    base_passes = _safe_number(stat_totals.get("passes"))
    base_catches = _safe_number(stat_totals.get("catches"))
    base_steals = _safe_number(stat_totals.get("steals"))
    base_turnovers = _safe_number(stat_totals.get("turnovers"))
    base_interceptions = _safe_number(stat_totals.get("interceptions"))
    base_blocks = _safe_number(stat_totals.get("blocks"))
    possession_time = float(stat_totals.get("possession_time") or 0.0)

    completed_passes = _safe_number(totals.get("completed_passes"))
    inferred_catches = _safe_number(totals.get("inferred_catches"))
    initiators = _safe_number(totals.get("initiators"))
    open_for_pass_samples = _safe_number(totals.get("open_for_pass_samples"))
    lane_blocked_samples = _safe_number(totals.get("lane_blocked_samples"))
    lane_blocks = _safe_number(totals.get("lane_blocks"))
    clear_attempts = _safe_number(totals.get("clear_attempts"))
    successful_clears = _safe_number(totals.get("successful_clears"))
    failed_clears = _safe_number(totals.get("failed_clears"))
    inferred_turnovers = _safe_number(totals.get("inferred_turnovers"))
    inferred_interceptions = _safe_number(totals.get("inferred_interceptions"))
    steal_takeaways = _safe_number(totals.get("steal_takeaways"))
    stun_takeaways = _safe_number(totals.get("stun_takeaways"))
    tight_man_coverage_samples = _safe_number(totals.get("tight_man_coverage_samples"))
    loose_man_coverage_samples = _safe_number(totals.get("loose_man_coverage_samples"))
    no_man_coverage_samples = _safe_number(totals.get("no_man_coverage_samples"))
    goalie_coverage_samples = _safe_number(totals.get("goalie_coverage_samples"))

    passes_to_open_receiver = _safe_number(metadata_totals.get("passes_to_open_receiver"))
    passes_to_covered_receiver = _safe_number(metadata_totals.get("passes_to_covered_receiver"))
    catches_open = _safe_number(metadata_totals.get("catches_open"))
    catches_covered = _safe_number(metadata_totals.get("catches_covered"))
    lane_coverage_failures = _safe_number(metadata_totals.get("lane_coverage_failures"))

    coverage_sample_total = (
        tight_man_coverage_samples + loose_man_coverage_samples + no_man_coverage_samples + goalie_coverage_samples
    )
    lane_block_rate = _rate(lane_blocks, coverage_sample_total + lane_blocks)
    tight_coverage_rate = _rate(tight_man_coverage_samples, coverage_sample_total)
    loose_coverage_rate = _rate(loose_man_coverage_samples, coverage_sample_total)
    no_man_coverage_rate = _rate(no_man_coverage_samples, coverage_sample_total)
    goalie_coverage_rate = _rate(goalie_coverage_samples, coverage_sample_total)
    open_pass_rate = _rate(open_for_pass_samples, open_for_pass_samples + lane_blocked_samples)
    clear_success_rate = _rate(successful_clears, clear_attempts)

    estimated_possession_releases = (
        completed_passes
        + clear_attempts
        + missed_shots
        + shots_saved
        + blocked_shots
        + stuffed_shots
        + goals_total
        + inferred_turnovers
    )
    avg_possession_time_per_touch = (
        round(possession_time / estimated_possession_releases, 3)
        if possession_time > 0.0 and estimated_possession_releases > 0.0
        else None
    )

    actual_scoreboard_points = _safe_number(stat_totals.get("points"))
    raw_goal_points_total = _safe_number(aggregate.get("raw_goal_points_total"))
    if actual_scoreboard_points <= 0.0:
        actual_scoreboard_points = raw_goal_points_total

    actual_points_per_round = _per_round(actual_scoreboard_points, rounds)
    shot_type_bonus_per_round = _per_round(
        (goals_3_guarded * 2.0)
        + (adjusted_guarded_2_for_bonus * 1.0)
        + (goals_3_open * 1.0),
        rounds,
    )
    miss_save_penalty_per_round = _per_round(
        (missed_shots * 0.5) + (shots_saved * 1.0),
        rounds,
    )
    effective_shooting_points_per_round = actual_points_per_round + shot_type_bonus_per_round - miss_save_penalty_per_round

    speed_effective_value = _weighted_score(
        [
            (_inverse_time_score(_average_from_totals(totals.get("offensive_transition_total"), totals.get("offensive_transition_count")), 2.5), 0.45),
            (_inverse_time_score(_average_from_totals(totals.get("defensive_transition_total"), totals.get("defensive_transition_count")), 2.5), 0.45),
            (_inverse_time_score(avg_possession_time_per_touch, 2.0), 0.10),
        ]
    )

    possession_effective_per_round = (
        _per_round(inferred_interceptions, rounds) * 2.5
        + _per_round(steal_takeaways, rounds) * 1.5
        + _per_round(stun_takeaways, rounds) * 1.25
        + _per_round(base_steals, rounds) * 1.0
        - _per_round(inferred_turnovers, rounds) * 1.5
        - _per_round(base_turnovers, rounds) * 1.0
    )
    if avg_possession_time_per_touch is not None:
        possession_effective_per_round += min(float(avg_possession_time_per_touch) / 3.0, 1.0) * 0.75

    offense_effective_per_round = (
        _per_round(completed_passes, rounds) * 0.4
        + _per_round(inferred_catches, rounds) * 0.25
        + _per_round(initiators, rounds) * 1.5
        + _per_round(base_assists, rounds) * 1.5
        + _per_round(base_goals, rounds) * 1.25
        + _per_round(base_points, rounds) * 0.35
        + _per_round(passes_to_open_receiver, rounds) * 0.35
        + _per_round(catches_open, rounds) * 0.25
        - _per_round(passes_to_covered_receiver, rounds) * 0.25
        - _per_round(catches_covered, rounds) * 0.10
    )
    if open_pass_rate is not None:
        offense_effective_per_round += (float(open_pass_rate) / 100.0) * 1.0

    tight_coverage_score = (float(tight_coverage_rate) / 100.0) * 1.0 if tight_coverage_rate is not None else 0.0
    goalie_coverage_score = (float(goalie_coverage_rate) / 100.0) * 0.75 if goalie_coverage_rate is not None else 0.0
    lane_block_score = (float(lane_block_rate) / 100.0) * 1.5 if lane_block_rate is not None else 0.0
    loose_coverage_penalty = (float(loose_coverage_rate) / 100.0) * 0.25 if loose_coverage_rate is not None else 0.0
    no_man_penalty = (float(no_man_coverage_rate) / 100.0) * 1.5 if no_man_coverage_rate is not None else 0.0
    defense_effective_per_round = (
        _per_round(base_saves, rounds) * 3.0
        + _per_round(inferred_interceptions, rounds) * 1.5
        + _per_round(steal_takeaways, rounds) * 1.25
        + _per_round(stun_takeaways, rounds) * 1.0
        + _per_round(blocked_shots, rounds) * 1.0
        + _per_round(stuffed_shots, rounds) * 1.25
        + _per_round(base_blocks, rounds) * 0.5
        + _per_round(base_steals, rounds) * 0.5
        + _per_round(base_interceptions, rounds) * 0.5
        + tight_coverage_score
        + goalie_coverage_score
        + lane_block_score
        - loose_coverage_penalty
        - no_man_penalty
        - _per_round(lane_coverage_failures, rounds) * 0.75
    )

    passing_effective_per_round = (
        _per_round(completed_passes, rounds) * 1.2
        + _per_round(inferred_catches, rounds) * 0.6
        + _per_round(successful_clears, rounds) * 1.0
        + _per_round(base_catches, rounds) * 0.5
        + _per_round(base_passes, rounds) * 0.5
        + _per_round(passes_to_open_receiver, rounds) * 0.35
        + _per_round(catches_open, rounds) * 0.25
        - _per_round(failed_clears, rounds) * 1.0
        - _per_round(passes_to_covered_receiver, rounds) * 0.35
        - _per_round(catches_covered, rounds) * 0.15
    )
    if clear_success_rate is not None:
        passing_effective_per_round += (float(clear_success_rate) / 100.0) * 1.5

    return {
        "shooting": effective_shooting_points_per_round,
        "speed": speed_effective_value,
        "possession": possession_effective_per_round,
        "offense": offense_effective_per_round,
        "defense": defense_effective_per_round,
        "passing": passing_effective_per_round,
    }


def _competitive_baseline_match_ids(matches: Iterable[Any], filters: Any) -> list[int]:
    ordered_matches = sorted(
        list(matches),
        key=lambda match: (
            str(getattr(match, "started_at", None) or getattr(match, "created_at", None) or ""),
            int(getattr(match, "id", 0)),
        ),
        reverse=True,
    )
    scoped_matches: list[Any] = []
    for match in ordered_matches:
        if not getattr(match, "finalized", False):
            continue
        if not _match_passes_scope_filters(match, filters):
            continue
        scoped_matches.append(match)
    if getattr(filters, "last_n", None) is not None:
        scoped_matches = scoped_matches[: int(filters.last_n)]

    baseline_ids: list[int] = []
    for match in scoped_matches:
        if not _match_counts_for_competitive_baseline(match):
            continue
        baseline_ids.append(int(match.id))
    return baseline_ids


def _match_passes_scope_filters(match: Any, filters: Any) -> bool:
    if not filters.allows_classification(getattr(match, "match_classification", None)):
        return False
    if not filters.allows_private_match_type(
        getattr(match, "private_match_type", None),
        getattr(match, "match_classification", None),
    ):
        return False
    match_date = _optional_match_date(getattr(match, "started_at", None) or getattr(match, "created_at", None))
    if filters.from_date and match_date and match_date < filters.from_date:
        return False
    if filters.to_date and match_date and match_date > filters.to_date:
        return False
    return True


def _match_counts_for_competitive_baseline(match: Any) -> bool:
    classification = str(getattr(match, "match_classification", "") or "").strip().casefold()
    if classification == "public":
        return True
    if classification == "private":
        private_type = str(getattr(match, "private_match_type", "") or "").strip().casefold()
        return private_type in {"pug", "official"}
    return False


def _optional_match_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _score_from_target(value: float, target: float) -> Optional[float]:
    if target <= 0:
        return None
    return round(max(0.0, (float(value) / float(target)) * 100.0), 1)


def _inverse_time_score(actual_seconds: Optional[float], target_seconds: float) -> Optional[float]:
    if actual_seconds is None or actual_seconds <= 0.0 or target_seconds <= 0.0:
        return None
    return (float(target_seconds) / float(actual_seconds)) * 100.0


def _weighted_score(components: list[tuple[Optional[float], float]]) -> Optional[float]:
    total_weight = 0.0
    total = 0.0
    for score, weight in components:
        if score is None or weight <= 0.0:
            continue
        total += float(score) * float(weight)
        total_weight += float(weight)
    if total_weight <= 0.0:
        return None
    return round(total / total_weight, 1)


def _merge_metric_row_into_totals(row: Any, totals: Counter, metadata_totals: Counter) -> None:
    for key in (
        "completed_passes",
        "inferred_catches",
        "initiators",
        "open_for_pass_samples",
        "lane_blocked_samples",
        "lane_blocks",
        "tight_man_coverage_samples",
        "loose_man_coverage_samples",
        "no_man_coverage_samples",
        "goalie_coverage_samples",
        "clear_attempts",
        "successful_clears",
        "failed_clears",
        "inferred_turnovers",
        "inferred_interceptions",
        "steal_takeaways",
        "stun_takeaways",
        "missed_shots",
        "shots_saved_against",
        "blocked_shots",
        "stuffed_shots",
        "offensive_transition_count",
        "defensive_transition_count",
        "goals_2_open_net",
        "goals_2_guarded",
        "goals_3_open_net",
        "goals_3_guarded",
    ):
        totals[key] += _safe_number(_value(row, key))
    totals["offensive_transition_total"] += float(_value(row, "offensive_transition_total") or 0.0)
    totals["defensive_transition_total"] += float(_value(row, "defensive_transition_total") or 0.0)
    metadata = _json_load(_value(row, "metadata_json"))
    for key in (
        "dunk_like_open_2s",
        "dunk_like_guarded_2s",
        "passes_to_open_receiver",
        "passes_to_covered_receiver",
        "catches_open",
        "catches_covered",
        "self_goals",
        "lane_coverage_failures",
        "shooter_uncovered",
    ):
        metadata_totals[key] += _safe_number(metadata.get(key))


def _merge_stat_row_into_totals(row: Any, stat_totals: Counter) -> None:
    for key in (
        "points",
        "goals",
        "assists",
        "saves",
        "stuns",
        "steals",
        "shots",
        "passes",
        "catches",
        "turnovers",
        "interceptions",
        "blocks",
    ):
        stat_totals[key] += _safe_number(_value(row, key))
    stat_totals["possession_time"] += float(_value(row, "possession_time") or 0.0)


def _row_afk_suspected(row: Any) -> bool:
    metadata = _json_load(_value(row, "stat_metadata_json"))
    afk = metadata.get("afk_detection") if isinstance(metadata, dict) else None
    return bool(isinstance(afk, dict) and afk.get("suspected"))


def _seconds_text(value: Optional[float]) -> str:
    if value is None:
        return "No samples"
    return f"{float(value):.2f}s"


def _average_from_totals(total: Any, count: Any) -> Optional[float]:
    count_value = _safe_number(count)
    if count_value <= 0:
        return None
    return float(total or 0.0) / float(count_value)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _round_count(value: Any) -> int:
    try:
        parsed = int(value) if value is not None else 0
    except (TypeError, ValueError):
        parsed = 0
    return parsed if parsed > 0 else 1


def _personal_rounds_by_match(metric_rows: list[Any], stat_rows: list[Any]) -> dict[int, float]:
    round_limits: dict[int, float] = {}
    estimated_rounds: dict[int, float] = {}
    for row in metric_rows:
        match_id = int(row["match_id"])
        round_limits[match_id] = float(_round_count(_value(row, "total_rounds_played")))
        if str(_value(row, "team") or "").casefold() not in {"blue", "orange"}:
            continue
        estimated_rounds[match_id] = estimated_rounds.get(match_id, 0.0) + _row_active_rounds_estimate(
            row,
            fallback_to_total_rounds=False,
        )
    for row in stat_rows:
        match_id = int(row["match_id"])
        round_limits.setdefault(match_id, float(_round_count(_value(row, "total_rounds_played"))))

    rounds_by_match: dict[int, float] = {}
    for match_id, round_limit in round_limits.items():
        estimated = estimated_rounds.get(match_id, 0.0)
        if estimated <= 0.0:
            rounds_by_match[match_id] = round_limit
        else:
            rounds_by_match[match_id] = min(round_limit, estimated)
    return rounds_by_match


def _row_active_rounds_estimate(row: Any, *, fallback_to_total_rounds: bool) -> float:
    metadata = _json_load(_value(row, "metadata_json"))
    try:
        estimated = float(metadata.get("active_rounds_estimated") or 0.0)
    except (TypeError, ValueError):
        estimated = 0.0
    if estimated > 0.0:
        return estimated
    if fallback_to_total_rounds:
        return float(_round_count(_value(row, "total_rounds_played")))
    return 0.0


def _safe_number(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _json_load(value: Optional[str]) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _perspective_event_totals(rows: list[Any], player_ids: set[int]) -> dict[str, int]:
    counts = Counter()
    seen_turnovers: set[tuple[Any, ...]] = set()
    seen_interceptions: set[tuple[Any, ...]] = set()
    for row in rows:
        event_type = str(row["event_type"] or "")
        actor_id = _optional_int(row["actor_player_id"])
        target_id = _optional_int(row["target_player_id"])
        start_sequence = _optional_int(row["start_sequence"])
        end_sequence = _optional_int(row["end_sequence"])
        sequence_token: tuple[Any, ...]
        if start_sequence is None and end_sequence is None:
            sequence_token = ("row", _optional_int(row["id"]))
        else:
            sequence_token = (start_sequence, end_sequence)
        sequence_key = (
            _optional_int(row["match_id"]),
            sequence_token,
            actor_id,
            target_id,
        )
        if event_type in {"turnover", "intercepted_pass"} and actor_id in player_ids:
            if sequence_key not in seen_turnovers:
                seen_turnovers.add(sequence_key)
                counts["turnover"] += 1
        elif event_type in {"turnover", "intercepted_pass"} and target_id in player_ids:
            if sequence_key not in seen_interceptions:
                seen_interceptions.add(sequence_key)
                counts["interception"] += 1
        elif event_type == "missed_shot" and actor_id in player_ids:
            counts["missed_shot"] += 1
        elif event_type == "shot_saved" and actor_id in player_ids:
            counts["shot_saved"] += 1
        elif event_type == "clear" and actor_id in player_ids:
            counts["clear"] += 1
        elif event_type == "initiator" and actor_id in player_ids:
            counts["initiator"] += 1
        elif event_type == "pass_to_covered_teammate" and actor_id in player_ids:
            counts["pass_to_covered_teammate"] += 1
        elif event_type == "shooter_uncovered" and actor_id in player_ids:
            counts["shooter_uncovered"] += 1
        elif event_type == "lane_coverage_failure" and actor_id in player_ids:
            counts["lane_coverage_failure"] += 1
    return dict(sorted(counts.items()))


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _value(row: Any, key: str) -> Any:
    try:
        if hasattr(row, "keys") and key in row.keys():
            return row[key]
    except Exception:
        pass
    if isinstance(row, dict):
        return row.get(key)
    return None
