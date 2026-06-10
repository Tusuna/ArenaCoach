"""GUI-friendly wrapper for advanced inference and advanced event queries."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, date, datetime
import json
import math
from pathlib import Path
import statistics
from typing import Any, Iterable, Mapping, Optional

from arena_coach.database import connect_database
from arena_coach.inference import AdvancedInferenceService
from arena_coach.repositories import players_repo, profiles_repo


CATEGORY_SCORE_ANCHORS = {
    "shooting": {"anchor_50": 2.75, "anchor_100": 8.02},
    "speed": {"anchor_50": 137.30, "anchor_100": 188.60},
    "possession": {"anchor_50": 7.78, "anchor_100": 26.64},
    "offense": {"anchor_50": 12.04, "anchor_100": 24.15},
    "defense": {"anchor_50": 8.92, "anchor_100": 31.32},
    "passing": {"anchor_50": 14.78, "anchor_100": 34.77},
}

DISPLAY_SCORE_ABSOLUTE_WEIGHT = 0.35
DISPLAY_CATEGORY_ORDER = ("shooting", "speed", "possession", "offense", "defense", "passing")


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

    def player_metric_summary(
        self,
        player_id: int,
        filters: Optional["StatsFilter"] = None,
        *,
        scoring_mode: Optional[str] = None,
    ) -> dict[str, Any]:
        from arena_coach.services.stats_service import DatabaseStatsService
        from arena_coach.stats.stat_filters import StatsFilter

        active_filters = filters or StatsFilter()
        resolved_scoring_mode = _normalize_category_scoring_mode(
            scoring_mode or getattr(active_filters, "category_scoring_mode", None)
        )
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
                    "category_scoring_mode": resolved_scoring_mode,
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
        category_breakdown = (
            _build_category_breakdown(aggregate, baselines=baselines, scoring_mode=resolved_scoring_mode)
            if metric_rows
            else {}
        )
        reference_breakdowns = build_reference_category_breakdowns(
            baseline_rows,
            baselines=baselines,
            scoring_mode=resolved_scoring_mode,
        )
        display_payload = apply_hybrid_display_scores(
            category_breakdown,
            reference_breakdowns,
            context_label="competitive player-team rows from your reviewed matches",
        )
        warnings: list[str] = []
        if not metric_rows:
            warnings.append("No advanced player metrics are available for this player in the current filters yet.")

        return {
            "player_id": player_id,
            "display_name": player["canonical_name"],
            "match_ids": sorted({int(row["match_id"]) for row in metric_rows or stat_rows}, reverse=True),
            "match_count": len({int(row["match_id"]) for row in metric_rows or stat_rows}),
            "metric_rounds_considered": aggregate["rounds"],
            "category_breakdown": display_payload["category_breakdown"],
            "category_scoring_mode": resolved_scoring_mode,
            "absolute_overall_score": display_payload["absolute_overall_score"],
            "display_overall_score": display_payload["display_overall_score"],
            "display_percentile": display_payload["display_percentile"],
            "display_context": display_payload["display_context"],
            "display_reference_count": display_payload["display_reference_count"],
            "display_absolute_weight": display_payload["display_absolute_weight"],
            "display_percentile_weight": display_payload["display_percentile_weight"],
            "warnings": warnings,
            "competitive_baseline_sample_size": len(baseline_rows),
            "competitive_baseline_match_ids": sorted({int(row["match_id"]) for row in baseline_rows}),
        }

    def local_user_summary(
        self,
        *,
        confidence_levels: Optional[Iterable[str]] = None,
        filters: Optional["StatsFilter"] = None,
        scoring_mode: Optional[str] = None,
    ) -> dict[str, Any]:
        from arena_coach.services.stats_service import DatabaseStatsService
        from arena_coach.stats.stat_filters import StatsFilter

        selected_levels = _normalized_confidence_levels(confidence_levels)
        active_filters = filters or StatsFilter()
        resolved_scoring_mode = _normalize_category_scoring_mode(
            scoring_mode or getattr(active_filters, "category_scoring_mode", None)
        )
        engine = DatabaseStatsService(self.database_path)._engine()
        with _connection(self.database_path) as connection:
            active = profiles_repo.get_active_profile(connection)
            if active is None:
                return {
                    "active_profile": None,
                    "confidence_levels": selected_levels,
                    "category_scoring_mode": resolved_scoring_mode,
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
                    "category_scoring_mode": resolved_scoring_mode,
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
                    "category_scoring_mode": resolved_scoring_mode,
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
        category_breakdown = _build_category_breakdown(
            metric_aggregate,
            baselines=competitive_baselines,
            scoring_mode=resolved_scoring_mode,
        )
        reference_breakdowns = build_reference_category_breakdowns(
            competitive_sample_rows,
            baselines=competitive_baselines,
            scoring_mode=resolved_scoring_mode,
        )
        display_payload = apply_hybrid_display_scores(
            category_breakdown,
            reference_breakdowns,
            context_label="competitive player-team rows from your reviewed matches",
        )

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
            "category_breakdown": display_payload["category_breakdown"],
            "category_scoring_mode": resolved_scoring_mode,
            "absolute_overall_score": display_payload["absolute_overall_score"],
            "display_overall_score": display_payload["display_overall_score"],
            "display_percentile": display_payload["display_percentile"],
            "display_context": display_payload["display_context"],
            "display_reference_count": display_payload["display_reference_count"],
            "display_absolute_weight": display_payload["display_absolute_weight"],
            "display_percentile_weight": display_payload["display_percentile_weight"],
            "metric_summary_note": "Category cards use estimated personal active rounds from stored per-match player metrics. Confidence toggles still affect event views only.",
            "competitive_baseline_sample_size": len(competitive_sample_rows),
            "competitive_baseline_match_ids": sorted({int(row["match_id"]) for row in competitive_sample_rows}),
        }

    def export_metric_tuning_report(
        self,
        filters: Optional["StatsFilter"] = None,
        *,
        scoring_mode: Optional[str] = None,
    ) -> dict[str, Any]:
        from arena_coach.services.stats_service import DatabaseStatsService
        from arena_coach.stats.stat_filters import StatsFilter

        active_filters = filters or StatsFilter(
            competitive_only=True,
            include_low_quality=False,
            include_public=True,
            include_private=True,
            include_tournament=True,
            include_unknown=False,
            include_afk_players=False,
            include_guest_players=False,
            private_match_types=("PUG", "Official"),
        )
        resolved_scoring_mode = _normalize_category_scoring_mode(
            scoring_mode or getattr(active_filters, "category_scoring_mode", None)
        )
        export_filters = active_filters.with_updates(category_scoring_mode=resolved_scoring_mode)
        stats_service = DatabaseStatsService(self.database_path)

        with _connection(self.database_path) as connection:
            active_profile = profiles_repo.get_active_profile(connection)
            player_rows = list(
                connection.execute(
                    """
                    SELECT id, canonical_name, created_at
                    FROM players
                    ORDER BY lower(canonical_name), id
                    """
                )
            )

        players: list[dict[str, Any]] = []
        for row in player_rows:
            player_id = int(row["id"])
            stats_summary = stats_service.player(player_id, export_filters)
            advanced_summary = self.player_metric_summary(
                player_id,
                export_filters,
                scoring_mode=resolved_scoring_mode,
            )
            if int(stats_summary.get("matches", 0)) <= 0 and float(advanced_summary.get("metric_rounds_considered", 0.0)) <= 0.0:
                continue

            categories = {}
            for category_key, category in (advanced_summary.get("category_breakdown") or {}).items():
                if not isinstance(category, dict):
                    continue
                categories[str(category_key)] = {
                    "final_score": category.get("final_score"),
                    "display_score": category.get("display_score"),
                    "display_percentile": category.get("display_percentile"),
                    "base_score": category.get("base_score"),
                    "mistake_penalty": category.get("mistake_penalty"),
                    "mistake_adjusted_score": category.get("mistake_adjusted_score"),
                    "sample_confidence": category.get("sample_confidence"),
                    "confidence_label": category.get("confidence_label"),
                    "raw_value": category.get("raw_value"),
                    "main_positive_inputs": list(category.get("main_positive_inputs") or []),
                    "main_mistake_inputs": list(category.get("main_mistake_inputs") or []),
                    "explanation": category.get("explanation"),
                }

            players.append(
                {
                    "player_id": player_id,
                    "canonical_name": row["canonical_name"],
                    "created_at": row["created_at"],
                    "matches": stats_summary.get("matches", 0),
                    "absolute_overall_score": advanced_summary.get("absolute_overall_score"),
                    "display_overall_score": advanced_summary.get("display_overall_score"),
                    "display_percentile": advanced_summary.get("display_percentile"),
                    "record": {
                        "wins": stats_summary.get("wins", 0),
                        "losses": stats_summary.get("losses", 0),
                        "ties": stats_summary.get("ties", 0),
                        "win_rate": stats_summary.get("win_rate", 0.0),
                    },
                    "shot_efficiency": stats_summary.get("shot_efficiency", 0.0),
                    "totals": stats_summary.get("totals") or {},
                    "averages": stats_summary.get("averages") or {},
                    "advanced_rounds_considered": advanced_summary.get("metric_rounds_considered", 0.0),
                    "advanced_match_count": advanced_summary.get("match_count", 0),
                    "advanced_categories": categories,
                    "advanced_warnings": list(advanced_summary.get("warnings") or []),
                }
            )

        category_averages: dict[str, dict[str, Any]] = {}
        for category_key in ("shooting", "speed", "possession", "offense", "defense", "passing"):
            category_rows = [player["advanced_categories"].get(category_key) for player in players if player["advanced_categories"].get(category_key)]
            if not category_rows:
                continue
            category_averages[category_key] = {
                "player_count": len(category_rows),
                "average_final_score": _mean_optional(row.get("final_score") for row in category_rows),
                "average_base_score": _mean_optional(row.get("base_score") for row in category_rows),
                "average_mistake_penalty": _mean_optional(row.get("mistake_penalty") for row in category_rows),
                "average_raw_value": _mean_optional(row.get("raw_value") for row in category_rows),
                "average_sample_confidence": _mean_optional(row.get("sample_confidence") for row in category_rows),
                "top_positive_inputs": _most_common_strings(
                    entry
                    for row in category_rows
                    for entry in list(row.get("main_positive_inputs") or [])
                ),
                "top_mistake_inputs": _most_common_strings(
                    entry
                    for row in category_rows
                    for entry in list(row.get("main_mistake_inputs") or [])
                ),
            }

        player_match_counts = [int(player.get("matches", 0)) for player in players]
        player_round_counts = [float(player.get("advanced_rounds_considered", 0.0)) for player in players]
        return {
            "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "database_path": str(self.database_path),
            "active_profile": {
                "id": int(active_profile["id"]) if active_profile is not None else None,
                "display_name": active_profile["display_name"] if active_profile is not None else None,
                "primary_echo_name": active_profile["primary_echo_name"] if active_profile is not None else None,
            },
            "filters": {
                "competitive_only": export_filters.competitive_only,
                "include_low_quality": export_filters.include_low_quality,
                "include_public": export_filters.include_public,
                "include_private": export_filters.include_private,
                "include_tournament": export_filters.include_tournament,
                "include_unknown": export_filters.include_unknown,
                "include_afk_players": export_filters.include_afk_players,
                "include_guest_players": export_filters.include_guest_players,
                "private_match_types": list(export_filters.selected_private_match_types()),
                "from_date": export_filters.from_date.isoformat() if export_filters.from_date else None,
                "to_date": export_filters.to_date.isoformat() if export_filters.to_date else None,
                "last_n": export_filters.last_n,
                "category_scoring_mode": resolved_scoring_mode,
            },
            "player_count": len(players),
            "player_match_average": round(statistics.mean(player_match_counts), 3) if player_match_counts else 0.0,
            "player_advanced_round_average": round(statistics.mean(player_round_counts), 3) if player_round_counts else 0.0,
            "category_averages": category_averages,
            "players": players,
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


def build_match_only_category_breakdown(
    metric_row: Optional[dict[str, Any]],
    stat_row: Optional[dict[str, Any]],
    *,
    total_rounds: Optional[float] = None,
    baselines: Optional[dict[str, dict[str, Any]]] = None,
    scoring_mode: str = "mistake_adjusted",
) -> dict[str, Any]:
    if not metric_row and not stat_row:
        return {}

    metric_payload = dict(metric_row or {})
    stat_payload = dict(stat_row or {})
    if metric_payload and "metadata_json" not in metric_payload:
        metric_payload["metadata_json"] = json.dumps(metric_payload.get("metadata") or {})
    if stat_payload and "metadata_json" not in stat_payload:
        stat_payload["metadata_json"] = json.dumps(stat_payload.get("metadata") or {})
    if total_rounds is not None:
        if metric_payload:
            metric_payload.setdefault("total_rounds_played", total_rounds)
        if stat_payload:
            stat_payload.setdefault("total_rounds_played", total_rounds)

    totals = Counter()
    metadata_totals = Counter()
    stat_totals = Counter()

    if metric_payload:
        _merge_metric_row_into_totals(metric_payload, totals, metadata_totals)
    if stat_payload:
        _merge_stat_row_into_totals(stat_payload, stat_totals)

    rounds = 0.0
    for row in (metric_payload, stat_payload):
        if not row:
            continue
        rounds = max(rounds, _row_active_rounds_estimate(row, fallback_to_total_rounds=True))
    if rounds <= 0.0 and total_rounds is not None:
        rounds = float(_round_count(total_rounds))
    if rounds <= 0.0:
        rounds = 1.0

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

    return _build_category_breakdown(
        {
            "rounds": float(rounds),
            "metric_totals": dict(totals),
            "stat_totals": dict(stat_totals),
            "metadata_totals": dict(metadata_totals),
            "goals_total": goals_total,
            "raw_goal_points_total": raw_goal_points_total,
            "failed_shots_total": failed_shots_total,
            "shooting_percentage": shooting_percentage,
            "bounded_conversion": bounded_conversion,
        },
        baselines=baselines,
        scoring_mode=scoring_mode,
    )


def build_reference_category_breakdowns(
    rows: Iterable[Any],
    *,
    baselines: Optional[dict[str, dict[str, Any]]] = None,
    scoring_mode: str = "mistake_adjusted",
) -> list[dict[str, Any]]:
    reference_breakdowns: list[dict[str, Any]] = []
    for row in rows:
        aggregate = _aggregate_competitive_sample_row(row)
        breakdown = _build_category_breakdown(
            aggregate,
            baselines=baselines,
            scoring_mode=scoring_mode,
        )
        if breakdown:
            reference_breakdowns.append(breakdown)
    return reference_breakdowns


def apply_hybrid_display_scores(
    category_breakdown: dict[str, Any],
    reference_breakdowns: Iterable[dict[str, Any]],
    *,
    context_label: str,
    absolute_weight: float = DISPLAY_SCORE_ABSOLUTE_WEIGHT,
) -> dict[str, Any]:
    updated_breakdown: dict[str, Any] = {}
    reference_list = [reference for reference in reference_breakdowns if isinstance(reference, dict)]
    reference_category_values = {
        key: _reference_category_scores(reference_list, key)
        for key in DISPLAY_CATEGORY_ORDER
    }
    reference_overall_values = _reference_overall_scores(reference_list)

    for key, detail in category_breakdown.items():
        if not isinstance(detail, dict):
            updated_breakdown[str(key)] = detail
            continue
        absolute_score = _extract_category_absolute_score(detail)
        display_percentile = _relative_percentile(absolute_score, reference_category_values.get(str(key), []))
        display_score = _hybrid_display_score(absolute_score, display_percentile, absolute_weight)
        enriched = dict(detail)
        enriched["absolute_score"] = _rounded_optional(absolute_score, decimals=1)
        enriched["display_score"] = _rounded_optional(display_score, decimals=1)
        enriched["display_percentile"] = _rounded_optional(display_percentile, decimals=1)
        enriched["display_context"] = context_label
        enriched["display_reference_count"] = len(reference_category_values.get(str(key), []))
        updated_breakdown[str(key)] = enriched

    absolute_overall_score = _breakdown_overall_score(updated_breakdown, score_key="absolute_score")
    display_percentile = _relative_percentile(absolute_overall_score, reference_overall_values)
    display_overall_score = _hybrid_display_score(absolute_overall_score, display_percentile, absolute_weight)
    return {
        "category_breakdown": updated_breakdown,
        "absolute_overall_score": _rounded_optional(absolute_overall_score, decimals=1),
        "display_overall_score": _rounded_optional(display_overall_score, decimals=1),
        "display_percentile": _rounded_optional(display_percentile, decimals=1),
        "display_context": context_label,
        "display_reference_count": len(reference_overall_values),
        "display_absolute_weight": round(float(absolute_weight), 3),
        "display_percentile_weight": round(1.0 - float(absolute_weight), 3),
    }


def competitive_category_baselines_for_database(database_path: Path) -> dict[str, dict[str, Any]]:
    with _connection(database_path) as connection:
        rows = list(
            connection.execute(
                """
                SELECT
                    apm.*,
                    m.match_classification,
                    m.private_match_type,
                    m.finalized
                FROM advanced_player_metrics apm
                INNER JOIN matches m ON m.id = apm.match_id
                WHERE m.finalized = 1
                """
            )
        )
    competitive_rows = []
    for row in rows:
        classification = str(row["match_classification"] or "").strip().casefold()
        private_type = str(row["private_match_type"] or "").strip().casefold()
        if classification == "public":
            eligible = True
        elif classification == "private":
            eligible = private_type in {"pug", "official"}
        else:
            eligible = False
        if not eligible or _row_afk_suspected(row):
            continue
        competitive_rows.append(row)
    return _competitive_category_baselines(competitive_rows)


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


def _build_category_breakdown(
    aggregate: dict[str, Any],
    baselines: Optional[dict[str, dict[str, Any]]] = None,
    *,
    scoring_mode: str = "mistake_adjusted",
) -> dict[str, Any]:
    rounds = float(aggregate.get("rounds") or 0.0)
    totals = aggregate.get("metric_totals") or {}
    stat_totals = aggregate.get("stat_totals") or {}
    metadata_totals = aggregate.get("metadata_totals") or {}
    scoring_mode = _normalize_category_scoring_mode(scoring_mode)

    goals_2_guarded = _safe_number(totals.get("goals_2_guarded"))
    goals_3_guarded = _safe_number(totals.get("goals_3_guarded"))
    goals_2_open = _safe_number(totals.get("goals_2_open_net"))
    goals_3_open = _safe_number(totals.get("goals_3_open_net"))
    missed_shots = _safe_number(totals.get("missed_shots"))
    shots_saved = _safe_number(totals.get("shots_saved_against"))
    dunk_like_open_2s = _safe_number(metadata_totals.get("dunk_like_open_2s"))
    dunk_like_guarded_2s = _safe_number(metadata_totals.get("dunk_like_guarded_2s"))
    self_goals = _safe_number(metadata_totals.get("self_goals"))
    shooter_uncovered = _safe_number(metadata_totals.get("shooter_uncovered"))
    shooting_percentage = aggregate.get("shooting_percentage")
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
    miss_save_penalty_per_round = _per_round((missed_shots * 0.5) + (shots_saved * 1.0), rounds)
    effective_shooting_points_per_round = actual_points_per_round + shot_type_bonus_per_round - miss_save_penalty_per_round

    avg_time_to_offense = _average_from_totals(totals.get("offensive_transition_total"), totals.get("offensive_transition_count"))
    avg_time_to_defense = _average_from_totals(totals.get("defensive_transition_total"), totals.get("defensive_transition_count"))
    speed_components = [
        (_inverse_time_score(avg_time_to_offense, 2.5), 0.45),
        (_inverse_time_score(avg_time_to_defense, 2.5), 0.45),
        (_inverse_time_score(avg_possession_time_per_touch, 2.0), 0.10),
    ]
    speed_effective_value = _weighted_score(speed_components)

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

    shooting_metrics = [
        _metric_entry("Guarded 3s", goals_3_guarded, rounds),
        _metric_entry("Guarded 2s", goals_2_guarded, rounds),
        _metric_entry("Open 3s", goals_3_open, rounds),
        _metric_entry("Open 2s", goals_2_open, rounds),
        _metric_entry("Possible dunk-like open 2s", dunk_like_open_2s, rounds),
        _metric_entry("Possible dunk-like guarded 2s", dunk_like_guarded_2s, rounds),
        _metric_entry("Missed shots", missed_shots, rounds),
        _metric_entry("Shots saved by goalie", shots_saved, rounds),
        _metric_entry("Stuffed shots", stuffed_shots, rounds),
        _metric_entry("Self goals", self_goals, rounds),
        _value_entry("Shooting percentage", _percentage_text(shooting_percentage), "Goals / (missed + saved)"),
        _value_entry("Actual scoreboard points / round", f"{actual_points_per_round:.2f}" if rounds else "No round data", "Based on real in-game points."),
        _value_entry("Shot-type bonus / round", f"{shot_type_bonus_per_round:.2f}" if rounds else "No round data", "Guarded 3s = +2, guarded 2s/open 3s = +1, open 2s = +0."),
        _value_entry("Miss/save penalty / round", f"{miss_save_penalty_per_round:.2f}" if rounds else "No round data", "Misses = -0.5, saved shots = -1."),
        _value_entry("Effective shooting points / round", f"{effective_shooting_points_per_round:.2f}" if rounds else "No round data", "Raw shooting production input before elite-curve scoring."),
    ]
    speed_metrics = [
        _value_entry("Average time to offense", _seconds_text(avg_time_to_offense), f"Samples {int(_safe_number(totals.get('offensive_transition_count')))}"),
        _value_entry("Average time to defense", _seconds_text(avg_time_to_defense), f"Samples {int(_safe_number(totals.get('defensive_transition_count')))}"),
        _value_entry("Average possession time / estimated touch", _seconds_text(avg_possession_time_per_touch), f"Estimated releases {int(round(estimated_possession_releases))}"),
        _metric_entry("Possession time", float(stat_totals.get("possession_time") or 0.0), rounds, decimals=2, suffix="s"),
        _value_entry("Speed raw value", f"{float(speed_effective_value):.2f}" if speed_effective_value is not None else "No samples", "Weighted inverse-time production input."),
    ]
    possession_metrics = [
        _metric_entry("Inferred turnovers", inferred_turnovers, rounds),
        _metric_entry("Inferred interceptions", inferred_interceptions, rounds),
        _metric_entry("Steal takeaways", steal_takeaways, rounds),
        _metric_entry("Stun takeaways", stun_takeaways, rounds),
        _metric_entry("Base steals", base_steals, rounds),
        _metric_entry("Base turnovers", base_turnovers, rounds),
        _metric_entry("Failed clears", failed_clears, rounds),
        _metric_entry("Covered catches", catches_covered, rounds),
        _metric_entry("Possession time", float(stat_totals.get("possession_time") or 0.0), rounds, decimals=2, suffix="s"),
        _value_entry("Average possession time / estimated touch", _seconds_text(avg_possession_time_per_touch), f"Estimated releases {int(round(estimated_possession_releases))}"),
        _value_entry("Effective possession units / round", f"{possession_effective_per_round:.2f}" if rounds else "No round data", "Raw possession production input before elite-curve scoring."),
    ]
    offense_metrics = [
        _metric_entry("Completed passes", completed_passes, rounds),
        _metric_entry("Initiators", initiators, rounds),
        _metric_entry("Inferred catches", inferred_catches, rounds),
        _metric_entry("Assists", base_assists, rounds),
        _metric_entry("Goals", base_goals, rounds),
        _metric_entry("Points", base_points, rounds),
        _metric_entry("Open-pass samples", open_for_pass_samples, rounds),
        _value_entry("Open-pass rate", _percentage_text(open_pass_rate), "Open samples / (open + lane-blocked samples)"),
        _metric_entry("Passes to open receiver", passes_to_open_receiver, rounds),
        _metric_entry("Passes to covered receiver", passes_to_covered_receiver, rounds),
        _metric_entry("Catches open", catches_open, rounds),
        _metric_entry("Catches covered", catches_covered, rounds),
        _metric_entry("Missed shots", missed_shots, rounds),
        _metric_entry("Shots saved against", shots_saved, rounds),
        _metric_entry("Stuffed shots", stuffed_shots, rounds),
        _metric_entry("Self goals", self_goals, rounds),
        _value_entry("Effective offense units / round", f"{offense_effective_per_round:.2f}" if rounds else "No round data", "Raw offensive production input before elite-curve scoring."),
    ]
    defense_metrics = [
        _metric_entry("Saves", base_saves, rounds),
        _metric_entry("Inferred interceptions", inferred_interceptions, rounds),
        _metric_entry("Steal takeaways", steal_takeaways, rounds),
        _metric_entry("Stun takeaways", stun_takeaways, rounds),
        _metric_entry("Lane blocks", lane_blocks, rounds),
        _metric_entry("Tight man coverage samples", tight_man_coverage_samples, rounds),
        _metric_entry("Loose man coverage samples", loose_man_coverage_samples, rounds),
        _metric_entry("No-man coverage samples", no_man_coverage_samples, rounds),
        _metric_entry("Goalie coverage samples", goalie_coverage_samples, rounds),
        _metric_entry("Lane coverage failures", lane_coverage_failures, rounds),
        _metric_entry("Shooter uncovered", shooter_uncovered, rounds),
        _metric_entry("Blocked shots", blocked_shots, rounds),
        _metric_entry("Stuffed shots", stuffed_shots, rounds),
        _metric_entry("Base blocks", base_blocks, rounds),
        _metric_entry("Base steals", base_steals, rounds),
        _metric_entry("Base interceptions", base_interceptions, rounds),
        _value_entry("Lane-block rate", _percentage_text(lane_block_rate), "Lane blocks as a share of tracked lane+coverage samples"),
        _value_entry("Tight coverage rate", _percentage_text(tight_coverage_rate), "Share of tracked defensive coverage samples"),
        _value_entry("Goalie coverage rate", _percentage_text(goalie_coverage_rate), "Share of tracked defensive coverage samples"),
        _value_entry("Loose coverage rate", _percentage_text(loose_coverage_rate), "Share of tracked defensive coverage samples"),
        _value_entry("No-man coverage rate", _percentage_text(no_man_coverage_rate), "Share of tracked defensive coverage samples"),
        _value_entry("Effective defense units / round", f"{defense_effective_per_round:.2f}" if rounds else "No round data", "Raw defensive production input before elite-curve scoring."),
    ]
    passing_metrics = [
        _metric_entry("Completed passes", completed_passes, rounds),
        _metric_entry("Inferred catches", inferred_catches, rounds),
        _metric_entry("Clear attempts", clear_attempts, rounds),
        _metric_entry("Successful clears", successful_clears, rounds),
        _metric_entry("Failed clears", failed_clears, rounds),
        _metric_entry("Base catches", base_catches, rounds),
        _metric_entry("Base passes", base_passes, rounds),
        _metric_entry("Passes to open receiver", passes_to_open_receiver, rounds),
        _metric_entry("Passes to covered receiver", passes_to_covered_receiver, rounds),
        _metric_entry("Catches open", catches_open, rounds),
        _metric_entry("Catches covered", catches_covered, rounds),
        _metric_entry("Inferred turnovers", inferred_turnovers, rounds),
        _metric_entry("Inferred interceptions", inferred_interceptions, rounds),
        _value_entry("Clear success rate", _percentage_text(clear_success_rate), None),
        _value_entry("Effective passing units / round", f"{passing_effective_per_round:.2f}" if rounds else "No round data", "Raw passing production input before elite-curve scoring."),
    ]

    return {
        "shooting": _category_score_entry(
            "shooting",
            "Shooting",
            raw_value=effective_shooting_points_per_round,
            rounds=rounds,
            scoring_mode=scoring_mode,
            score_note="Base shooting uses a fixed elite curve on effective shooting points per round, then mistake-adjusted mode subtracts missed, saved, stuffed, and self-goal penalties before confidence adjustment.",
            metrics=shooting_metrics,
            positive_inputs=[
                ("Guarded 3s", goals_3_guarded * 2.0),
                ("Guarded 2s", adjusted_guarded_2_for_bonus * 1.0),
                ("Open 3s", goals_3_open * 1.0),
                ("Open 2s", goals_2_open * 0.5),
                ("Scoreboard points / round", actual_points_per_round),
            ],
            mistake_inputs=[
                ("Missed shots", 2.0 * _per_round(missed_shots, rounds)),
                ("Shots saved against", 3.0 * _per_round(shots_saved, rounds)),
                ("Stuffed shots", 4.5 * _per_round(stuffed_shots, rounds)),
                ("Self goals", 20.0 * _per_round(self_goals, rounds)),
            ],
            mistake_cap=35.0,
        ),
        "speed": _category_score_entry(
            "speed",
            "Speed",
            raw_value=speed_effective_value,
            rounds=rounds,
            scoring_mode=scoring_mode,
            score_note="Base speed uses a fixed elite curve on weighted transition speed. Mistake-adjusted mode subtracts time-over-target penalties, then low samples are pulled toward 50.",
            metrics=speed_metrics,
            positive_inputs=[
                ("Average time to offense", _safe_number(speed_components[0][0])),
                ("Average time to defense", _safe_number(speed_components[1][0])),
                ("Release speed", _safe_number(speed_components[2][0])),
            ],
            mistake_inputs=[
                ("Slow offensive transitions", 5.0 * max(0.0, float(avg_time_to_offense or 0.0) - 1.5)),
                ("Slow defensive transitions", 5.0 * max(0.0, float(avg_time_to_defense or 0.0) - 1.5)),
            ],
            mistake_cap=25.0,
        ),
        "possession": _category_score_entry(
            "possession",
            "Possession",
            raw_value=possession_effective_per_round,
            rounds=rounds,
            scoring_mode=scoring_mode,
            score_note="Base possession rewards takeaways and control. Mistake-adjusted mode subtracts turnovers, failed clears, and covered catches before confidence adjustment.",
            metrics=possession_metrics,
            positive_inputs=[
                ("Inferred interceptions", _per_round(inferred_interceptions, rounds) * 2.5),
                ("Steal takeaways", _per_round(steal_takeaways, rounds) * 1.5),
                ("Stun takeaways", _per_round(stun_takeaways, rounds) * 1.25),
                ("Base steals", _per_round(base_steals, rounds) * 1.0),
            ],
            mistake_inputs=[
                ("Inferred turnovers", 1.25 * _per_round(inferred_turnovers, rounds)),
                ("Failed clears", 3.0 * _per_round(failed_clears, rounds)),
                ("Covered catches", 0.6 * _per_round(catches_covered, rounds)),
            ],
            mistake_cap=35.0,
        ),
        "offense": _category_score_entry(
            "offense",
            "Offense",
            raw_value=offense_effective_per_round,
            rounds=rounds,
            scoring_mode=scoring_mode,
            score_note="Base offense rewards creation: assists, goals, points, initiators, and quality pass involvement. Mistake-adjusted mode subtracts turnovers and bad shooting outcomes.",
            metrics=offense_metrics,
            positive_inputs=[
                ("Assists", _per_round(base_assists, rounds) * 1.5),
                ("Initiators", _per_round(initiators, rounds) * 1.5),
                ("Goals", _per_round(base_goals, rounds) * 1.25),
                ("Points", _per_round(base_points, rounds) * 0.35),
                ("Completed passes", _per_round(completed_passes, rounds) * 0.4),
            ],
            mistake_inputs=[
                ("Inferred turnovers", 1.0 * _per_round(inferred_turnovers, rounds)),
                ("Missed shots", 1.4 * _per_round(missed_shots, rounds)),
                ("Shots saved against", 2.0 * _per_round(shots_saved, rounds)),
                ("Stuffed shots", 3.5 * _per_round(stuffed_shots, rounds)),
                ("Self goals", 20.0 * _per_round(self_goals, rounds)),
            ],
            mistake_cap=35.0,
        ),
        "defense": _category_score_entry(
            "defense",
            "Defense",
            raw_value=defense_effective_per_round,
            rounds=rounds,
            scoring_mode=scoring_mode,
            score_note="Base defense rewards saves, takeaways, lane blocks, and stronger coverage. Mistake-adjusted mode subtracts uncovered shooters, lane failures, and no-man coverage pressure.",
            metrics=defense_metrics,
            positive_inputs=[
                ("Saves", _per_round(base_saves, rounds) * 3.0),
                ("Inferred interceptions", _per_round(inferred_interceptions, rounds) * 1.5),
                ("Steal takeaways", _per_round(steal_takeaways, rounds) * 1.25),
                ("Lane blocks", _per_round(lane_blocks, rounds) * 1.0),
                ("Tight coverage", tight_coverage_score),
            ],
            mistake_inputs=[
                ("Shooter uncovered", 7.0 * _per_round(shooter_uncovered, rounds)),
                ("Lane coverage failures", 8.0 * _per_round(lane_coverage_failures, rounds)),
                ("No-man coverage samples", 0.003 * _per_round(no_man_coverage_samples, rounds)),
            ],
            mistake_cap=30.0,
        ),
        "passing": _category_score_entry(
            "passing",
            "Passing",
            raw_value=passing_effective_per_round,
            rounds=rounds,
            scoring_mode=scoring_mode,
            score_note="Base passing rewards completed passes, catches, and good clears. Mistake-adjusted mode subtracts covered receivers, turnovers, and intercepted outcomes before confidence adjustment.",
            metrics=passing_metrics,
            positive_inputs=[
                ("Completed passes", _per_round(completed_passes, rounds) * 1.2),
                ("Inferred catches", _per_round(inferred_catches, rounds) * 0.6),
                ("Successful clears", _per_round(successful_clears, rounds) * 1.0),
                ("Passes to open receiver", _per_round(passes_to_open_receiver, rounds) * 0.35),
            ],
            mistake_inputs=[
                ("Passes to covered receiver", 1.4 * _per_round(passes_to_covered_receiver, rounds)),
                ("Inferred turnovers", 0.9 * _per_round(inferred_turnovers, rounds)),
                ("Inferred interceptions", 1.5 * _per_round(inferred_interceptions, rounds)),
            ],
            mistake_cap=35.0,
        ),
    }


def _reference_category_scores(reference_breakdowns: list[dict[str, Any]], category_key: str) -> list[float]:
    values: list[float] = []
    for breakdown in reference_breakdowns:
        detail = breakdown.get(category_key)
        if not isinstance(detail, dict):
            continue
        score = _extract_category_absolute_score(detail)
        if score is not None:
            values.append(float(score))
    return values


def _reference_overall_scores(reference_breakdowns: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for breakdown in reference_breakdowns:
        score = _breakdown_overall_score(breakdown, score_key="absolute_score")
        if score is not None:
            values.append(float(score))
    return values


def _breakdown_overall_score(
    category_breakdown: Mapping[str, Any],
    *,
    score_key: str,
) -> Optional[float]:
    values: list[float] = []
    for category_key in DISPLAY_CATEGORY_ORDER:
        detail = category_breakdown.get(category_key)
        if not isinstance(detail, Mapping):
            continue
        score = detail.get(score_key)
        if score is None and score_key == "absolute_score":
            score = _extract_category_absolute_score(detail)
        elif score is None and score_key == "display_score":
            score = detail.get("display_score")
        if score is None:
            continue
        values.append(float(score))
    if not values:
        return None
    return sum(values) / float(len(values))


def _extract_category_absolute_score(category: Mapping[str, Any]) -> Optional[float]:
    for key in ("absolute_score", "overall_score", "final_score"):
        value = category.get(key)
        if value is not None:
            return float(value)
    return None


def _relative_percentile(value: Optional[float], reference_values: Iterable[Any]) -> Optional[float]:
    if value is None:
        return None
    values = sorted(float(item) for item in reference_values if item is not None)
    if len(values) < 2:
        return None
    if abs(values[-1] - values[0]) < 0.0001:
        return None

    numeric_value = float(value)
    left = bisect_left(values, numeric_value)
    right = bisect_right(values, numeric_value)
    denominator = float(len(values) - 1)

    if right > left:
        position = (float(left) + float(right - 1)) / 2.0
        return (position / denominator) * 100.0

    upper_index = min(left, len(values) - 1)
    lower_index = max(0, upper_index - 1)
    lower_value = values[lower_index]
    upper_value = values[upper_index]

    if numeric_value <= values[0]:
        return 0.0
    if numeric_value >= values[-1]:
        return 100.0
    if abs(upper_value - lower_value) < 0.0001:
        position = float(lower_index)
    else:
        fraction = (numeric_value - lower_value) / (upper_value - lower_value)
        position = float(lower_index) + fraction
    return (position / denominator) * 100.0


def _hybrid_display_score(
    absolute_score: Optional[float],
    percentile: Optional[float],
    absolute_weight: float,
) -> Optional[float]:
    if absolute_score is None:
        return None
    if percentile is None:
        return float(absolute_score)
    weight = max(0.0, min(1.0, float(absolute_weight)))
    percentile_weight = 1.0 - weight
    return _clamp_score((float(absolute_score) * weight) + (float(percentile) * percentile_weight))


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


def _category_score_entry(
    category_key: str,
    title: str,
    *,
    raw_value: Optional[float],
    rounds: float,
    scoring_mode: str,
    score_note: str,
    metrics: list[dict[str, Any]],
    positive_inputs: list[tuple[str, float]],
    mistake_inputs: list[tuple[str, float]],
    mistake_cap: float,
) -> dict[str, Any]:
    normalized_mode = _normalize_category_scoring_mode(scoring_mode)
    base_score = _elite_curve_score(category_key, raw_value)
    mistake_penalty = min(mistake_cap, sum(max(0.0, float(value)) for _label, value in mistake_inputs))
    if base_score is None:
        mistake_adjusted_score = None
        final_score = None
    else:
        mistake_adjusted_score = _clamp_score(base_score - mistake_penalty)
        production_score = base_score if normalized_mode == "production_only" else mistake_adjusted_score
        final_score = _clamp_score(
            50.0 + (_sample_confidence(rounds) * (float(production_score) - 50.0))
        )
    sample_confidence = _sample_confidence(rounds)
    confidence_label = _confidence_label(rounds)
    main_positive_inputs = _top_named_inputs(positive_inputs)
    main_mistake_inputs = _top_named_inputs(mistake_inputs)
    explanation = _category_explanation(title, main_positive_inputs, main_mistake_inputs, confidence_label)

    summary_metrics = [
        _value_entry("Scoring mode", _scoring_mode_label(normalized_mode), "Switch in CLI or GUI to compare modes."),
        _value_entry("Final score", _number_text(final_score, decimals=1), "Displayed score after mode + confidence adjustment."),
        _value_entry("Base production score", _number_text(base_score, decimals=1), "Fixed elite-curve score before mistake penalties."),
        _value_entry("Mistake penalty", f"-{mistake_penalty:.1f}", f"Capped at {mistake_cap:.0f}"),
        _value_entry("Mistake-adjusted score", _number_text(mistake_adjusted_score, decimals=1), "Base score minus tracked mistake penalty."),
        _value_entry("Rounds considered", _number_text(rounds, decimals=2), None),
        _value_entry("Sample confidence", f"{sample_confidence:.2f}", confidence_label.title()),
        _value_entry("Raw category value", _number_text(raw_value, decimals=2), None),
    ]
    if main_positive_inputs:
        summary_metrics.append(_value_entry("Main positives", ", ".join(main_positive_inputs), None))
    if main_mistake_inputs:
        summary_metrics.append(_value_entry("Main issues", ", ".join(main_mistake_inputs), None))
    summary_metrics.append(_value_entry("Explanation", explanation, None))

    return {
        "category": title,
        "category_name": title,
        "title": title,
        "raw_value": _rounded_optional(raw_value, decimals=4),
        "rounds_considered": _rounded_optional(rounds, decimals=3),
        "base_score": _rounded_optional(base_score, decimals=1),
        "mistake_penalty": round(mistake_penalty, 1),
        "mistake_adjusted_score": _rounded_optional(mistake_adjusted_score, decimals=1),
        "sample_confidence": round(sample_confidence, 3),
        "confidence_label": confidence_label,
        "absolute_score": _rounded_optional(final_score, decimals=1),
        "final_score": _rounded_optional(final_score, decimals=1),
        "overall_score": _rounded_optional(final_score, decimals=1),
        "display_score": _rounded_optional(final_score, decimals=1),
        "display_percentile": None,
        "display_context": None,
        "display_reference_count": 0,
        "main_positive_inputs": main_positive_inputs,
        "main_mistake_inputs": main_mistake_inputs,
        "explanation": explanation,
        "score_note": score_note,
        "scoring_mode": normalized_mode,
        "metrics": summary_metrics + metrics,
    }


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


def _normalize_category_scoring_mode(value: Optional[str]) -> str:
    normalized = str(value or "mistake_adjusted").strip().casefold()
    return "production_only" if normalized == "production_only" else "mistake_adjusted"


def _scoring_mode_label(mode: str) -> str:
    return "Production Only" if _normalize_category_scoring_mode(mode) == "production_only" else "Mistake Adjusted"


def _elite_curve_score(category_key: str, raw_value: Optional[float]) -> Optional[float]:
    if raw_value is None:
        return None
    anchors = CATEGORY_SCORE_ANCHORS.get(category_key)
    if not anchors:
        return None
    anchor_50 = float(anchors["anchor_50"])
    anchor_100 = float(anchors["anchor_100"])
    if anchor_100 <= anchor_50:
        return None
    k_value = math.log(10.0) / (anchor_100 - anchor_50)
    score = 110.0 / (1.0 + math.exp(-k_value * (float(raw_value) - anchor_50)))
    return round(_clamp_score(score), 1)


def _sample_confidence(rounds: float) -> float:
    numeric_rounds = max(0.0, float(rounds or 0.0))
    return min(1.0, math.sqrt(numeric_rounds / 10.0)) if numeric_rounds > 0.0 else 0.0


def _confidence_label(rounds: float) -> str:
    numeric_rounds = max(0.0, float(rounds or 0.0))
    if numeric_rounds < 3.0:
        return "very low sample"
    if numeric_rounds < 5.0:
        return "low sample"
    if numeric_rounds < 10.0:
        return "medium sample"
    return "stable sample"


def _top_named_inputs(inputs: list[tuple[str, float]], limit: int = 3) -> list[str]:
    ranked = [
        (str(label), float(value))
        for label, value in inputs
        if float(value) > 0.0
    ]
    ranked.sort(key=lambda row: row[1], reverse=True)
    return [label for label, _value in ranked[:limit]]


def _category_explanation(
    title: str,
    main_positive_inputs: list[str],
    main_mistake_inputs: list[str],
    confidence_label: str,
) -> str:
    if main_positive_inputs and main_mistake_inputs:
        return (
            f"{title} production is driven by {', '.join(main_positive_inputs[:2])}, "
            f"but reduced by {', '.join(main_mistake_inputs[:3])}. "
            f"Confidence: {confidence_label}."
        )
    if main_positive_inputs:
        return (
            f"{title} production is mostly driven by {', '.join(main_positive_inputs[:3])}. "
            f"Few tracked mistake penalties were found. Confidence: {confidence_label}."
        )
    if main_mistake_inputs:
        return (
            f"{title} has limited tracked production and is mainly being pulled down by "
            f"{', '.join(main_mistake_inputs[:3])}. Confidence: {confidence_label}."
        )
    return f"{title} does not have enough tracked positive or mistake signals yet. Confidence: {confidence_label}."


def _rounded_optional(value: Optional[float], *, decimals: int) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), decimals)


def _number_text(value: Optional[float], *, decimals: int = 1) -> str:
    if value is None:
        return "No data"
    return f"{float(value):.{decimals}f}"


def _clamp_score(value: float) -> float:
    return max(0.0, min(110.0, float(value)))


def _mean_optional(values: Iterable[Any]) -> Optional[float]:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return None
    return round(statistics.mean(numbers), 4)


def _most_common_strings(values: Iterable[str], limit: int = 5) -> list[dict[str, Any]]:
    counter = Counter(str(value) for value in values if str(value).strip())
    return [
        {"label": label, "count": count}
        for label, count in counter.most_common(limit)
    ]


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
