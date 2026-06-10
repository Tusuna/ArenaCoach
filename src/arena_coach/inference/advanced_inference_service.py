"""Advanced event inference service."""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any, Optional

from arena_coach.database import connect_database
from arena_coach.parsing.normalized_event import NormalizedEvent
from arena_coach.parsing.raw_log_reader import RawSnapshotRecord, read_raw_log
from arena_coach.parsing import snapshot_parser as sp
from arena_coach.repositories import advanced_events_repo, advanced_player_metrics_repo, matches_repo

from .clear_analysis import infer_clears
from .coverage_analysis import infer_coverage_events
from .inference_config import DEFAULT_CONFIG, InferenceConfig, meets_min_confidence
from .pass_analysis import infer_pass_events
from .player_metrics_analysis import build_player_metrics
from .possession_chains import build_possession_chains
from .shot_analysis import infer_shot_events
from .spatial_models import AdvancedEvent, DiscState, OrientationModel, PlayerState, SnapshotFrame
from .transition_analysis import infer_transition_events
from .turnover_analysis import infer_turnovers


LIVE_STATUSES = {"round_start", "playing", "score", "sudden_death", "round_over"}


@dataclass
class InferenceResult:
    match_id: int
    raw_log_path: Optional[str]
    advanced_events_saved: int
    deleted_existing_events: int
    advanced_player_metrics_saved: int
    deleted_existing_metrics: int
    event_counts: dict[str, int]
    orientation: dict[str, Any]


class AdvancedInferenceService:
    def __init__(self, database_path: Path, config: InferenceConfig | None = None) -> None:
        self.database_path = Path(database_path)
        self.config = config or DEFAULT_CONFIG

    def infer_match(self, match_id: int, *, force: bool = False) -> InferenceResult:
        with _connection(self.database_path) as connection:
            match = matches_repo.get_match(connection, match_id)
            if match is None:
                raise ValueError(f"Match id {match_id} does not exist.")
            existing = advanced_events_repo.get_match_advanced_events(connection, match_id)
            existing_metrics = advanced_player_metrics_repo.get_match_metrics(connection, match_id)
            metrics_current = _metrics_include_active_rounds(existing_metrics)
            if not force:
                if existing and existing_metrics and metrics_current:
                    return InferenceResult(
                        match_id=match_id,
                        raw_log_path=match["raw_log_path"],
                        advanced_events_saved=len(existing),
                        deleted_existing_events=0,
                        advanced_player_metrics_saved=len(existing_metrics),
                        deleted_existing_metrics=0,
                        event_counts=dict(sorted(Counter(str(row["event_type"]) for row in existing).items())),
                        orientation=_json_load(_match_metadata_value(match, "advanced_inference_orientation")),
                    )
            context = self._build_context(connection, match_id, match)
            if existing and not force:
                inferred_events: list[AdvancedEvent | Any] = existing
                deleted_events = 0
                saved_events = len(existing)
            else:
                inferred_events = self._infer(context)
                with connection:
                    deleted_events = advanced_events_repo.delete_match_events(connection, match_id)
                    saved_events = advanced_events_repo.add_advanced_events(connection, match_id, inferred_events)
                    metadata = _json_load(match["metadata_json"])
                    metadata["advanced_inference_orientation"] = context.orientation.__dict__
                    connection.execute(
                        "UPDATE matches SET metadata_json = ? WHERE id = ?",
                        (json.dumps(metadata, sort_keys=True), match_id),
                    )
            metric_rows = build_player_metrics(
                match_id,
                context.match_stat_rows,
                context.frames,
                context.chains,
                context.base_events,
                inferred_events,
                context.orientation,
                self.config,
                match_classification=match["match_classification"],
            )
            with connection:
                deleted_metrics = advanced_player_metrics_repo.delete_match_metrics(connection, match_id)
                saved_metrics = advanced_player_metrics_repo.add_metric_rows(connection, metric_rows)
            return InferenceResult(
                match_id=match_id,
                raw_log_path=match["raw_log_path"],
                advanced_events_saved=saved_events,
                deleted_existing_events=deleted_events,
                advanced_player_metrics_saved=saved_metrics,
                deleted_existing_metrics=deleted_metrics,
                event_counts=dict(sorted(Counter(_event_type_value(event) for event in inferred_events).items())),
                orientation=context.orientation.__dict__,
            )

    def infer_latest(self, *, force: bool = False) -> InferenceResult:
        with _connection(self.database_path) as connection:
            matches = matches_repo.list_matches(connection)
            if not matches:
                raise ValueError("No matches available.")
            latest_match_id = int(matches[0]["id"])
        return self.infer_match(latest_match_id, force=force)

    def infer_all_finalized(self, *, force: bool = False) -> dict[str, Any]:
        with _connection(self.database_path) as connection:
            match_ids = advanced_events_repo.list_finalized_match_ids(connection)
        results = [self.infer_match(match_id, force=force) for match_id in match_ids]
        return {
            "matches_processed": len(results),
            "total_advanced_events": sum(result.advanced_events_saved for result in results),
            "matches": [
                {
                    "match_id": result.match_id,
                    "advanced_events_saved": result.advanced_events_saved,
                    "event_counts": result.event_counts,
                }
                for result in results
            ],
        }

    def summary(
        self,
        match_id: int,
        *,
        min_confidence: str = "medium",
        confidence_levels: Optional[list[str]] = None,
        event_type: Optional[str] = None,
        player_id: Optional[int] = None,
        include_low_confidence: bool = False,
    ) -> dict[str, Any]:
        with _connection(self.database_path) as connection:
            match = matches_repo.get_match(connection, match_id)
            if match is None:
                raise ValueError(f"Match id {match_id} does not exist.")
            rows = advanced_events_repo.get_match_advanced_events(
                connection,
                match_id,
                min_confidence=min_confidence,
                confidence_levels=confidence_levels,
                event_type=event_type,
                player_id=player_id,
                include_low_confidence=include_low_confidence,
            )
            players = matches_repo.get_match_players(connection, match_id)
            stats_rows = matches_repo.get_match_player_stats(connection, match_id)
            metric_rows = advanced_player_metrics_repo.get_match_metrics(connection, match_id)
        timeline = [_advanced_row_dict(row) for row in rows]
        counts = dict(sorted(Counter(row["event_type"] for row in rows).items()))
        return {
            "match": {
                "id": int(match["id"]),
                "display_name": match["display_name"],
                "raw_log_path": match["raw_log_path"],
            },
            "counts": counts,
            "timeline": timeline,
            "player_breakdown": _player_breakdown(rows, players, stats_rows),
            "player_metrics": [_advanced_metric_row_dict(row) for row in metric_rows],
        }

    def timeline(
        self,
        match_id: int,
        *,
        min_confidence: str = "medium",
        confidence_levels: Optional[list[str]] = None,
        event_type: Optional[str] = None,
        player_id: Optional[int] = None,
        include_low_confidence: bool = False,
    ) -> list[dict[str, Any]]:
        return self.summary(
            match_id,
            min_confidence=min_confidence,
            confidence_levels=confidence_levels,
            event_type=event_type,
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
        with _connection(self.database_path) as connection:
            rows = advanced_events_repo.get_player_advanced_events(
                connection,
                player_id,
                min_confidence=min_confidence,
                confidence_levels=confidence_levels,
                event_type=event_type,
                include_low_confidence=include_low_confidence,
            )
        counts = Counter()
        by_match = Counter()
        for row in rows:
            counts[str(row["event_type"])] += 1
            by_match[int(row["match_id"])] += 1
        return {
            "player_id": player_id,
            "event_counts": dict(sorted(counts.items())),
            "matches": len(by_match),
            "timeline": [_advanced_row_dict(row) for row in rows],
        }

    def _build_context(self, connection, match_id: int, match_row: Any) -> "_InferenceContext":
        base_events = _load_base_events(connection, match_id)
        match_players = matches_repo.get_match_players(connection, match_id)
        raw_log_path = Path(match_row["raw_log_path"]) if match_row["raw_log_path"] else None
        records: list[RawSnapshotRecord] = []
        if raw_log_path and raw_log_path.exists():
            records = read_raw_log(raw_log_path).records
        frames = _build_frames(records, _player_ids_by_alias(match_players))
        frames_by_sequence = {frame.sequence: frame for frame in frames}
        orientation = _infer_orientation(frames, self.config)
        chains = build_possession_chains(frames, base_events)
        match_stat_rows = matches_repo.get_match_player_stats(connection, match_id)
        return _InferenceContext(
            match_id=match_id,
            raw_log_path=str(raw_log_path.resolve()) if raw_log_path and raw_log_path.exists() else (str(raw_log_path) if raw_log_path else None),
            base_events=base_events,
            frames=frames,
            frames_by_sequence=frames_by_sequence,
            orientation=orientation,
            player_ids_by_alias=_player_ids_by_alias(match_players),
            chains=chains,
            match_stat_rows=match_stat_rows,
        )

    def _infer(self, context: "_InferenceContext") -> list[AdvancedEvent]:
        inferred: list[AdvancedEvent] = []
        inferred.extend(infer_turnovers(context.chains, context.base_events, context.player_ids_by_alias))
        inferred.extend(infer_pass_events(context.base_events, context.frames_by_sequence, context.player_ids_by_alias, self.config))
        inferred.extend(infer_shot_events(context.base_events, context.frames_by_sequence, context.player_ids_by_alias, self.config))
        inferred.extend(infer_clears(context.chains, context.orientation, context.player_ids_by_alias, self.config))
        inferred.extend(
            infer_coverage_events(
                context.base_events,
                context.frames_by_sequence,
                context.player_ids_by_alias,
                self.config,
                context.orientation,
            )
        )
        inferred.extend(
            infer_transition_events(
                context.chains,
                context.frames,
                context.orientation,
                context.player_ids_by_alias,
                self.config,
            )
        )
        return _dedupe_events(inferred)


@dataclass
class _InferenceContext:
    match_id: int
    raw_log_path: Optional[str]
    base_events: list[NormalizedEvent]
    frames: list[SnapshotFrame]
    frames_by_sequence: dict[int, SnapshotFrame]
    orientation: OrientationModel
    player_ids_by_alias: dict[str, Optional[int]]
    chains: list[Any]
    match_stat_rows: list[Any]


@contextmanager
def _connection(database_path: Path):
    connection = connect_database(database_path)
    try:
        yield connection
    finally:
        connection.close()


def _load_base_events(connection, match_id: int) -> list[NormalizedEvent]:
    events = []
    for row in matches_repo.get_events(connection, match_id):
        metadata = _json_load(row["metadata_json"])
        events.append(
            NormalizedEvent(
                event_id=int(row["id"]),
                match_id=int(row["match_id"]),
                sequence=row["sequence"],
                captured_at=row["captured_at"],
                game_clock=row["game_clock"],
                game_clock_display=row["game_clock_display"],
                event_type=row["event_type"],
                actor_name=row["actor_alias"],
                target_name=row["target_alias"],
                assist_name=row["assist_alias"],
                actor_userid=row["actor_userid"],
                target_userid=row["target_userid"],
                assist_userid=row["assist_userid"],
                actor_playerid=row["actor_playerid"],
                target_playerid=row["target_playerid"],
                assist_playerid=row["assist_playerid"],
                team=row["team"],
                value=row["value"],
                raw_text=row["raw_text"],
                metadata=metadata,
            )
        )
    return events


def _player_ids_by_alias(rows: list[Any]) -> dict[str, Optional[int]]:
    mapping: dict[str, Optional[int]] = {}
    for row in rows:
        mapping[str(row["match_alias"]).casefold()] = int(row["player_id"]) if row["player_id"] is not None else None
    return mapping


def _build_frames(records: list[RawSnapshotRecord], player_ids_by_alias: dict[str, Optional[int]]) -> list[SnapshotFrame]:
    frames: list[SnapshotFrame] = []
    for record in records:
        parsed_teams = sp.parsed_teams(record.snapshot)
        players: list[PlayerState] = []
        for team in parsed_teams:
            for player in team.get("players", []):
                alias = str(player.get("name") or "")
                players.append(
                    PlayerState(
                        alias=alias,
                        userid=player.get("userid"),
                        playerid=player.get("playerid"),
                        team=str(player.get("team") or "spectator"),
                        head_position=_vector((_dict(player.get("head")).get("position"))),
                        body_position=_vector((_dict(player.get("body")).get("position"))),
                        left_hand_position=_vector((_dict(player.get("lhand")).get("pos"))),
                        right_hand_position=_vector((_dict(player.get("rhand")).get("pos"))),
                        velocity=_vector(player.get("velocity")),
                        holding_left=_optional_str(player.get("holding_left")),
                        holding_right=_optional_str(player.get("holding_right")),
                        possession=bool(player.get("possession")),
                        stunned=bool(player.get("stunned")),
                        blocking=bool(player.get("blocking")),
                        actor_player_id=player_ids_by_alias.get(alias.casefold()),
                        stats=_player_stats_snapshot(player.get("stats")),
                    )
                )
        disc = sp.disc(record.snapshot)
        blue_team = sp.blue_team(record.snapshot)
        orange_team = sp.orange_team(record.snapshot)
        frames.append(
            SnapshotFrame(
                sequence=int(record.sequence or 0),
                captured_at=record.captured_at,
                game_clock=sp.game_clock(record.snapshot),
                game_clock_display=sp.game_clock_display(record.snapshot),
                game_status=sp.game_status(record.snapshot),
                blue_score=sp.score(record.snapshot)[0],
                orange_score=sp.score(record.snapshot)[1],
                disc=DiscState(
                    position=_vector(disc.get("position")),
                    velocity=_vector(disc.get("velocity")),
                    forward=_vector(disc.get("forward")),
                    left=_vector(disc.get("left")),
                    up=_vector(disc.get("up")),
                    bounce_count=_optional_int(disc.get("bounce_count")),
                ),
                players=players,
                top_level_possession=sp.possession(record.snapshot),
                blue_team_possession=bool(blue_team.get("possession")),
                orange_team_possession=bool(orange_team.get("possession")),
            )
        )
    return frames


def _infer_orientation(frames: list[SnapshotFrame], config: InferenceConfig) -> OrientationModel:
    if not frames:
        return OrientationModel(explanation="No raw snapshot frames available.")
    axis_scores: dict[str, list[float]] = defaultdict(list)
    for frame in frames:
        if frame.game_status not in LIVE_STATUSES:
            continue
        blue = [player.best_position for player in frame.players if player.team == "blue" and player.best_position is not None]
        orange = [player.best_position for player in frame.players if player.team == "orange" and player.best_position is not None]
        if not blue or not orange:
            continue
        blue_mean = (
            sum(position[0] for position in blue) / len(blue),
            sum(position[1] for position in blue) / len(blue),
            sum(position[2] for position in blue) / len(blue),
        )
        orange_mean = (
            sum(position[0] for position in orange) / len(orange),
            sum(position[1] for position in orange) / len(orange),
            sum(position[2] for position in orange) / len(orange),
        )
        axis_scores["x"].append(orange_mean[0] - blue_mean[0])
        axis_scores["z"].append(orange_mean[2] - blue_mean[2])
    if not axis_scores["x"] and not axis_scores["z"]:
        return OrientationModel(explanation="Not enough live team-position data to infer court orientation.")
    axis = max(("x", "z"), key=lambda name: abs(sum(axis_scores[name]) / len(axis_scores[name])) if axis_scores[name] else 0.0)
    values = axis_scores[axis]
    if not values:
        return OrientationModel(explanation="Could not find a stable axis for team separation.")
    mean_difference = sum(values) / len(values)
    if abs(mean_difference) < config.orientation_min_team_separation:
        return OrientationModel(
            axis=axis,
            confidence="low",
            confidence_score=0.25,
            explanation="Team positions were too close together to infer a reliable offensive/defensive orientation.",
        )
    blue_side = "negative" if mean_difference > 0 else "positive"
    orange_side = "positive" if blue_side == "negative" else "negative"
    return OrientationModel(
        axis=axis,
        blue_side=blue_side,
        orange_side=orange_side,
        confidence="medium",
        confidence_score=0.7,
        explanation=f"Using the {axis}-axis because team centroids were consistently separated there.",
    )


def _vector(value: Any) -> Optional[tuple[float, float, float]]:
    if not isinstance(value, list) or len(value) < 3:
        return None
    try:
        return float(value[0]), float(value[1]), float(value[2])
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text or None


def _player_stats_snapshot(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    stats: dict[str, float] = {}
    for key, raw in value.items():
        try:
            stats[str(key)] = float(raw)
        except (TypeError, ValueError):
            continue
    return stats


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _event_type_value(row: Any) -> str:
    if isinstance(row, AdvancedEvent):
        return row.event_type
    return str(row["event_type"] or "")


def _metrics_include_active_rounds(rows: list[Any]) -> bool:
    relevant_rows = [
        row
        for row in rows
        if str(row["team"] or "").casefold() in {"blue", "orange"}
    ]
    if not relevant_rows:
        return False
    for row in relevant_rows:
        metadata = _json_load(row["metadata_json"])
        if "active_rounds_estimated" not in metadata:
            return False
    return True


def _advanced_row_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "match_id": int(row["match_id"]),
        "event_type": row["event_type"],
        "actor_player_id": row["actor_player_id"],
        "target_player_id": row["target_player_id"],
        "assist_player_id": row["assist_player_id"],
        "actor_alias": row["actor_alias"],
        "target_alias": row["target_alias"],
        "assist_alias": row["assist_alias"],
        "team": row["team"],
        "start_sequence": row["start_sequence"],
        "end_sequence": row["end_sequence"],
        "start_game_clock": row["start_game_clock"],
        "end_game_clock": row["end_game_clock"],
        "confidence": row["confidence"],
        "confidence_score": row["confidence_score"],
        "directness": row["directness"],
        "value": row["value"],
        "explanation": row["explanation"],
        "evidence": _json_load(row["evidence_json"]),
        "created_at": row["created_at"],
    }


def _advanced_metric_row_dict(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "match_id": int(row["match_id"]),
        "player_id": row["player_id"],
        "match_alias": row["match_alias"],
        "userid": row["userid"],
        "team": row["team"],
        "completed_passes": int(row["completed_passes"] or 0),
        "inferred_catches": int(row["inferred_catches"] or 0),
        "initiators": int(row["initiators"] or 0),
        "open_for_pass_samples": int(row["open_for_pass_samples"] or 0),
        "lane_blocked_samples": int(row["lane_blocked_samples"] or 0),
        "lane_blocks": int(row["lane_blocks"] or 0),
        "tight_man_coverage_samples": int(row["tight_man_coverage_samples"] or 0),
        "loose_man_coverage_samples": int(row["loose_man_coverage_samples"] or 0),
        "no_man_coverage_samples": int(row["no_man_coverage_samples"] or 0),
        "goalie_coverage_samples": int(row["goalie_coverage_samples"] or 0),
        "clear_attempts": int(row["clear_attempts"] or 0),
        "successful_clears": int(row["successful_clears"] or 0),
        "failed_clears": int(row["failed_clears"] or 0),
        "inferred_turnovers": int(row["inferred_turnovers"] or 0),
        "inferred_interceptions": int(row["inferred_interceptions"] or 0),
        "steal_takeaways": int(row["steal_takeaways"] or 0),
        "stun_takeaways": int(row["stun_takeaways"] or 0),
        "missed_shots": int(row["missed_shots"] or 0),
        "shots_saved_against": int(row["shots_saved_against"] or 0),
        "blocked_shots": int(row["blocked_shots"] or 0),
        "stuffed_shots": int(row["stuffed_shots"] or 0),
        "offensive_transition_count": int(row["offensive_transition_count"] or 0),
        "offensive_transition_total": float(row["offensive_transition_total"] or 0.0),
        "defensive_transition_count": int(row["defensive_transition_count"] or 0),
        "defensive_transition_total": float(row["defensive_transition_total"] or 0.0),
        "goals_2_open_net": int(row["goals_2_open_net"] or 0),
        "goals_2_guarded": int(row["goals_2_guarded"] or 0),
        "goals_3_open_net": int(row["goals_3_open_net"] or 0),
        "goals_3_guarded": int(row["goals_3_guarded"] or 0),
        "metadata": _json_load(row["metadata_json"]),
    }


def _player_breakdown(event_rows: list[Any], players: list[Any], stats_rows: list[Any]) -> list[dict[str, Any]]:
    players_by_id = {int(row["id"]): row for row in players}
    counts_by_alias: dict[str, Counter[str]] = defaultdict(Counter)
    for row in event_rows:
        event_type = str(row["event_type"] or "")
        actor_alias = row["actor_alias"]
        target_alias = row["target_alias"]
        assist_alias = row["assist_alias"]
        if event_type in {"turnover", "intercepted_pass"}:
            if actor_alias:
                counts_by_alias[str(actor_alias)]["turnover"] += 1
            if target_alias:
                counts_by_alias[str(target_alias)]["interception"] += 1
            continue
        for alias in (actor_alias, target_alias, assist_alias):
            if alias:
                counts_by_alias[str(alias)].update([event_type])
    stats_by_alias = {str(row["match_alias"]): row for row in stats_rows}
    rows: list[dict[str, Any]] = []
    for alias, counts in sorted(counts_by_alias.items(), key=lambda item: item[0].casefold()):
        matching_player = next((row for row in players_by_id.values() if str(row["match_alias"]).casefold() == alias.casefold()), None)
        stats_row = stats_by_alias.get(alias)
        rows.append(
            {
                "alias": alias,
                "player_id": matching_player["player_id"] if matching_player is not None else None,
                "canonical_name": matching_player["canonical_name"] if matching_player is not None else None,
                "team": matching_player["team"] if matching_player is not None else None,
                "counts": dict(sorted(counts.items())),
                "stats": {
                    "points": stats_row["points"] if stats_row is not None else 0,
                    "goals": stats_row["goals"] if stats_row is not None else 0,
                    "assists": stats_row["assists"] if stats_row is not None else 0,
                    "saves": stats_row["saves"] if stats_row is not None else 0,
                    "stuns": stats_row["stuns"] if stats_row is not None else 0,
                },
            }
        )
    return rows


def _dedupe_events(events: list[AdvancedEvent]) -> list[AdvancedEvent]:
    seen = set()
    deduped = []
    for event in events:
        key = (
            event.event_type,
            str(event.actor_alias or "").casefold(),
            str(event.target_alias or "").casefold(),
            event.start_sequence,
            event.end_sequence,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def _json_load(value: Optional[str]) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _match_metadata_value(match_row: Any, key: str) -> Optional[str]:
    metadata = _json_load(match_row["metadata_json"])
    value = metadata.get(key)
    return json.dumps(value) if isinstance(value, dict) else None
