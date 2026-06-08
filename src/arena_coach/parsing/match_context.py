"""Derive higher-level match context from raw snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from arena_coach.match_context import dominant_team, has_meaningful_participation, meaningful_stat_total, point_winner, round_record_warning
from arena_coach.parsing import snapshot_parser as sp
from arena_coach.parsing.raw_log_reader import RawSnapshotRecord


LIVE_STATUSES = {"round_start", "playing", "score", "sudden_death"}
ROUND_END_STATUSES = {"round_over", "post_match", "pre_match"}
TRACKED_STAT_KEYS = (
    "points",
    "goals",
    "assists",
    "saves",
    "stuns",
    "steals",
    "shots_taken",
    "passes",
    "catches",
    "turnovers",
    "interceptions",
    "blocks",
    "possession_time",
)


@dataclass
class RoundContext:
    blue_round_wins: int = 0
    orange_round_wins: int = 0
    total_rounds_played: int = 0
    round_summary: List[Dict[str, Any]] = None
    points_carry_over: Optional[bool] = None
    warning: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blue_round_wins": self.blue_round_wins,
            "orange_round_wins": self.orange_round_wins,
            "total_rounds_played": self.total_rounds_played,
            "round_summary_json": list(self.round_summary or []),
            "points_carry_over": self.points_carry_over,
            "warning": self.warning,
        }


def derive_team_split_stats(
    records: Iterable[RawSnapshotRecord],
    afk_assessments: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    previous_by_identity: Dict[str, Dict[str, Any]] = {}
    max_seen_by_row: Dict[str, Dict[str, Any]] = {}
    identities_to_rows: Dict[str, set[str]] = {}

    for record in records:
        grouped = _group_players_by_identity(record.snapshot)
        primary_by_identity = {
            identity: _choose_primary_player(players)
            for identity, players in grouped.items()
            if players
        }

        for identity, players in grouped.items():
            identities_to_rows.setdefault(identity, set())
            for player in players:
                row_key = _team_row_key(identity, player.get("team"))
                identities_to_rows[identity].add(row_key)
                row = rows.setdefault(row_key, _new_row(player))
                row["name"] = player.get("name") or row["name"]
                row["userid"] = player.get("userid") or row["userid"]
                row["playerid"] = player.get("playerid") or row["playerid"]
                row["team"] = player.get("team") or row["team"]
                row["metadata"]["snapshot_samples"] = int(row["metadata"].get("snapshot_samples") or 0) + 1
                if sp.game_status(record.snapshot) in LIVE_STATUSES:
                    row["metadata"]["live_samples"] = int(row["metadata"].get("live_samples") or 0) + 1
                _append_unique(row["metadata"]["aliases"], player.get("name"))
                _append_unique(row["metadata"]["teams_seen"], player.get("team"))
                best_stats = max_seen_by_row.setdefault(row_key, {})
                _merge_best_stats(best_stats, player.get("stats", {}))

        for identity, current_player in primary_by_identity.items():
            previous_player = previous_by_identity.get(identity)
            if previous_player is not None:
                deltas = _stat_deltas(previous_player.get("stats", {}), current_player.get("stats", {}))
                if deltas:
                    row_key = _team_row_key(identity, current_player.get("team"))
                    row = rows.setdefault(row_key, _new_row(current_player))
                    for stat_name, delta in deltas.items():
                        row["stats"][stat_name] = row["stats"].get(stat_name, 0) + delta
            previous_by_identity[identity] = current_player

    by_name = _afk_by_name(afk_assessments)
    for row_key, row in rows.items():
        best_stats = max_seen_by_row.get(row_key, {})
        if meaningful_stat_total(row["stats"]) == 0 and meaningful_stat_total(best_stats) > 0 and len(identities_to_rows.get(row["identity"], [])) == 1:
            row["stats"] = dict(best_stats)
        assessment = _match_assessment(row, by_name)
        if assessment is not None:
            row["metadata"]["afk_detection"] = assessment
        row["metadata"]["active_participation"] = has_meaningful_participation(row["stats"], row["metadata"])
        row["metadata"]["meaningful"] = row["metadata"]["active_participation"]
        row["metadata"]["identity"] = row["identity"]
        row["metadata"]["stat_total"] = meaningful_stat_total(row["stats"])
        row["metadata"]["suppressed_default"] = (
            not row["metadata"]["active_participation"]
            and len(identities_to_rows.get(row["identity"], [])) > 1
        )
    return rows


def apply_primary_teams(
    detected_players: List[Dict[str, Any]],
    split_rows: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in split_rows.values():
        grouped.setdefault(row["identity"], []).append(row)

    updated: List[Dict[str, Any]] = []
    for player in detected_players:
        identity = _identity_from_detected_player(player)
        row_group = grouped.get(identity, [])
        copy = dict(player)
        primary = dominant_team(row_group)
        if primary:
            copy["team"] = primary
        updated.append(copy)
    return updated


def derive_round_context(records: Iterable[RawSnapshotRecord], final_blue: Any, final_orange: Any) -> RoundContext:
    previous_status = ""
    current_round_start: Optional[Tuple[int, int]] = None
    previous_round_end: Optional[Tuple[int, int]] = None
    saw_reset_between_rounds = False
    any_reset = False
    any_carry = False
    round_summary: List[Dict[str, Any]] = []
    direct_blue_rounds = 0
    direct_orange_rounds = 0

    previous_score: Optional[Tuple[int, int]] = None
    for record in records:
        snapshot = record.snapshot
        status = sp.game_status(snapshot)
        score = _score(snapshot)
        if previous_score is not None and score is not None:
            if score[0] < previous_score[0] or score[1] < previous_score[1]:
                saw_reset_between_rounds = True
                any_reset = True
        direct_blue_rounds = max(direct_blue_rounds, _safe_int(snapshot.get("blue_round_score")) or 0)
        direct_orange_rounds = max(direct_orange_rounds, _safe_int(snapshot.get("orange_round_score")) or 0)

        if status in LIVE_STATUSES and previous_status not in LIVE_STATUSES:
            current_round_start = score or (0, 0)
            if previous_round_end is not None and not saw_reset_between_rounds:
                any_carry = True

        if previous_status in LIVE_STATUSES and status in ROUND_END_STATUSES and score is not None:
            if current_round_start is None:
                current_round_start = previous_round_end or (0, 0)
            summary = _round_summary_entry(
                len(round_summary) + 1,
                current_round_start,
                previous_round_end,
                score,
                saw_reset_between_rounds,
            )
            round_summary.append(summary)
            previous_round_end = score
            current_round_start = None
            saw_reset_between_rounds = False

        previous_status = status
        previous_score = score

    if not round_summary and final_blue is not None and final_orange is not None:
        winner = point_winner(final_blue, final_orange)
        round_summary.append(
            {
                "round": 1,
                "blue_points": _safe_int(final_blue) or 0,
                "orange_points": _safe_int(final_orange) or 0,
                "winner": winner if winner != "tie" else "unknown",
                "confidence": "fallback",
            }
        )

    blue_round_wins = direct_blue_rounds
    orange_round_wins = direct_orange_rounds
    if blue_round_wins == 0 and orange_round_wins == 0:
        blue_round_wins = sum(1 for item in round_summary if item.get("winner") == "blue")
        orange_round_wins = sum(1 for item in round_summary if item.get("winner") == "orange")

    total_rounds_played = len(round_summary) or (blue_round_wins + orange_round_wins)
    context = RoundContext(
        blue_round_wins=blue_round_wins,
        orange_round_wins=orange_round_wins,
        total_rounds_played=total_rounds_played,
        round_summary=round_summary,
        points_carry_over=(True if any_carry and not any_reset else False if any_reset else None),
    )
    context.warning = round_record_warning(
        {
            "blue_score": final_blue,
            "orange_score": final_orange,
            "blue_round_wins": context.blue_round_wins,
            "orange_round_wins": context.orange_round_wins,
        }
    )
    return context


def _round_summary_entry(
    round_number: int,
    start_score: Tuple[int, int],
    previous_round_end: Optional[Tuple[int, int]],
    end_score: Tuple[int, int],
    reset_scores: bool,
) -> Dict[str, Any]:
    if previous_round_end is not None and not reset_scores:
        blue_points = max(0, end_score[0] - previous_round_end[0])
        orange_points = max(0, end_score[1] - previous_round_end[1])
        confidence = "derived_carry_over"
    elif start_score != (0, 0) and not reset_scores:
        blue_points = max(0, end_score[0] - start_score[0])
        orange_points = max(0, end_score[1] - start_score[1])
        confidence = "derived_start_delta"
    else:
        blue_points = end_score[0]
        orange_points = end_score[1]
        confidence = "derived"
    winner = point_winner(blue_points, orange_points)
    return {
        "round": round_number,
        "blue_points": blue_points,
        "orange_points": orange_points,
        "winner": winner if winner != "tie" else "unknown",
        "confidence": confidence,
    }


def _group_players_by_identity(snapshot: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for player in sp.iter_players(snapshot):
        identity = _identity(player)
        grouped.setdefault(identity, []).append(player)
    return grouped


def _choose_primary_player(players: List[Dict[str, Any]]) -> Dict[str, Any]:
    return sorted(
        players,
        key=lambda player: (
            meaningful_stat_total(player.get("stats", {})),
            1 if player.get("possession") else 0,
            1 if player.get("holding_left") == "disc" or player.get("holding_right") == "disc" else 0,
        ),
        reverse=True,
    )[0]


def _identity(player: Dict[str, Any]) -> str:
    userid = str(player.get("userid") or "").strip()
    if userid:
        return f"userid:{userid.casefold()}"
    name = str(player.get("name") or "").strip().casefold()
    if name:
        return f"name:{name}"
    playerid = str(player.get("playerid") or "").strip()
    return f"playerid:{playerid}:{player.get('team_index')}"


def _identity_from_detected_player(player: Dict[str, Any]) -> str:
    userid = str(player.get("userid") or "").strip()
    if userid:
        return f"userid:{userid.casefold()}"
    name = str(player.get("name") or "").strip().casefold()
    if name:
        return f"name:{name}"
    playerid = str(player.get("playerid") or "").strip()
    team = str(player.get("team") or "")
    return f"playerid:{playerid}:{team}"


def _team_row_key(identity: str, team: Any) -> str:
    return f"{identity}|{str(team or 'unknown').casefold()}"


def _new_row(player: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "identity": _identity(player),
        "name": player.get("name"),
        "userid": player.get("userid"),
        "playerid": player.get("playerid"),
        "team": player.get("team"),
        "stats": {key: 0 for key in TRACKED_STAT_KEYS},
        "metadata": {"aliases": [], "teams_seen": [], "snapshot_samples": 0, "live_samples": 0},
    }


def _stat_deltas(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, float]:
    deltas = {}
    for key in TRACKED_STAT_KEYS:
        current_value = _safe_float(current.get(key))
        previous_value = _safe_float(previous.get(key))
        if current_value is None or previous_value is None:
            continue
        if current_value > previous_value:
            deltas[key] = current_value - previous_value
    return deltas


def _merge_best_stats(target: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    for key in TRACKED_STAT_KEYS:
        current = _safe_float(target.get(key))
        incoming_value = _safe_float(incoming.get(key))
        if incoming_value is None:
            continue
        if current is None or incoming_value > current:
            target[key] = incoming_value


def _afk_by_name(afk_assessments: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    lookup = {}
    for assessment in afk_assessments.values():
        if assessment.get("userid"):
            lookup[f"userid:{str(assessment['userid']).casefold()}"] = assessment
        if assessment.get("name"):
            lookup[f"name:{str(assessment['name']).casefold()}"] = assessment
    return lookup


def _match_assessment(row: Dict[str, Any], lookup: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return lookup.get(row["identity"])


def _append_unique(values: List[Any], value: Any) -> None:
    if value is not None and value not in values:
        values.append(value)


def _score(snapshot: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    blue, orange = sp.score(snapshot)
    if blue is None or orange is None:
        return None
    return blue, orange


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
