"""Derive normalized events from ordered Echo Arena snapshots."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from arena_coach.parsing.normalized_event import NormalizedEvent
from arena_coach.parsing.raw_log_reader import RawSnapshotRecord
from arena_coach.parsing import snapshot_parser as sp


LIVE_STATUSES = {"round_start", "playing", "score", "sudden_death"}
END_STATUSES = {"post_match", "pre_match", "round_over", ""}

STAT_EVENT_MAP = {
    "saves": "save",
    "stuns": "stun",
    "steals": "steal",
    "passes": "pass",
    "catches": "catch",
    "shots_taken": "shot",
    "interceptions": "interception",
    "blocks": "block",
    "assists": "assist",
    "goals": "goal",
}


@dataclass
class EventDerivationResult:
    events: List[NormalizedEvent] = field(default_factory=list)
    detected_sessionid: Optional[str] = None
    detected_sessionip: Optional[str] = None
    detected_match_type: Optional[str] = None
    detected_map_name: Optional[str] = None
    detected_client_name: Optional[str] = None
    first_captured_at: Optional[str] = None
    last_captured_at: Optional[str] = None
    latest_blue_score: Optional[int] = None
    latest_orange_score: Optional[int] = None
    detected_players: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    detected_teams: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    latest_player_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def event_counts(self) -> Dict[str, int]:
        return dict(sorted(Counter(event.event_type for event in self.events).items()))

    def detected_player_list(self) -> List[Dict[str, Any]]:
        return sorted(
            self.detected_players.values(),
            key=lambda player: (str(player.get("name", "")), str(player.get("userid", ""))),
        )

    def detected_team_list(self) -> List[Dict[str, Any]]:
        return sorted(self.detected_teams.values(), key=lambda team: int(team.get("index", 99)))


def derive_events(records: Iterable[RawSnapshotRecord]) -> EventDerivationResult:
    result = EventDerivationResult()
    previous_record: Optional[RawSnapshotRecord] = None
    previous_snapshot: Optional[Dict[str, Any]] = None
    previous_players: Dict[str, Dict[str, Any]] = {}
    previous_possession = None
    recent_score_credits: Dict[Tuple[str, str], List[int]] = {}

    for record in records:
        snapshot = record.snapshot
        _update_summary(result, record)

        current_players = sp.player_lookup(snapshot)
        current_possession = _possession_signature(snapshot, current_players)

        result.events.extend(_derive_unknown_snapshot_events(record))
        result.events.extend(_derive_match_boundary_events(record, previous_snapshot))
        score_events, goal_names, assist_names = _derive_score_events(record, previous_snapshot)
        result.events.extend(score_events)
        _remember_score_credits(score_events, recent_score_credits)
        _expire_score_credits(record.sequence, recent_score_credits)
        result.events.extend(_derive_player_join_leave_events(record, previous_players, current_players))
        result.events.extend(
            _derive_stat_delta_events(
                record,
                previous_players,
                current_players,
                goal_names,
                assist_names,
                recent_score_credits,
            )
        )
        result.events.extend(_derive_possession_events(record, previous_possession, current_possession))
        result.events.extend(_derive_unknown_stat_events(record, previous_players, current_players))

        previous_record = record
        previous_snapshot = snapshot
        previous_players = current_players
        previous_possession = current_possession

    if previous_record is not None:
        result.last_captured_at = previous_record.captured_at

    return result


def _derive_match_boundary_events(
    record: RawSnapshotRecord,
    previous_snapshot: Optional[Dict[str, Any]],
) -> List[NormalizedEvent]:
    current_status = sp.game_status(record.snapshot)
    previous_status = sp.game_status(previous_snapshot) if previous_snapshot else ""
    events: List[NormalizedEvent] = []

    if current_status in LIVE_STATUSES and previous_status not in LIVE_STATUSES:
        events.append(
            _event(
                "match_start",
                record,
                raw_text=f"match entered {current_status}",
                metadata={"previous_game_status": previous_status, "current_game_status": current_status},
            )
        )

    if previous_status in LIVE_STATUSES and current_status in END_STATUSES:
        events.append(
            _event(
                "match_end",
                record,
                raw_text=f"match left live status for {current_status}",
                metadata={"previous_game_status": previous_status, "current_game_status": current_status},
            )
        )

    return events


def _derive_score_events(
    record: RawSnapshotRecord,
    previous_snapshot: Optional[Dict[str, Any]],
) -> Tuple[List[NormalizedEvent], Set[str], Set[str]]:
    previous_blue, previous_orange = sp.score(previous_snapshot or {})
    current_blue, current_orange = sp.score(record.snapshot)
    events: List[NormalizedEvent] = []
    goal_names: Set[str] = set()
    assist_names: Set[str] = set()

    if previous_blue == current_blue and previous_orange == current_orange:
        return events, goal_names, assist_names

    if previous_blue is not None and current_blue is not None and previous_blue != current_blue:
        events.append(
            _event(
                "score_update",
                record,
                team="blue",
                value=current_blue - previous_blue,
                raw_text=f"blue score changed {previous_blue}->{current_blue}",
                metadata=_score_metadata(previous_blue, previous_orange, current_blue, current_orange),
            )
        )

    if previous_orange is not None and current_orange is not None and previous_orange != current_orange:
        events.append(
            _event(
                "score_update",
                record,
                team="orange",
                value=current_orange - previous_orange,
                raw_text=f"orange score changed {previous_orange}->{current_orange}",
                metadata=_score_metadata(previous_blue, previous_orange, current_blue, current_orange),
            )
        )

    positive_score_change = (
        previous_blue is not None
        and current_blue is not None
        and current_blue > previous_blue
    ) or (
        previous_orange is not None
        and current_orange is not None
        and current_orange > previous_orange
    )

    last_score = sp.last_score(record.snapshot)
    scorer = last_score.get("person_scored")
    if positive_score_change and sp.valid_player_name(scorer):
        scorer_name = str(scorer)
        assist_name = last_score.get("assist_scored") if sp.valid_player_name(last_score.get("assist_scored")) else None
        goal_names.add(scorer_name.casefold())
        if assist_name:
            assist_names.add(str(assist_name).casefold())

        goal_metadata = {
            "disc_speed": last_score.get("disc_speed"),
            "goal_type": last_score.get("goal_type"),
            "distance_thrown": last_score.get("distance_thrown"),
            "previous_score": {"blue": previous_blue, "orange": previous_orange},
            "current_score": {"blue": current_blue, "orange": current_orange},
        }
        events.append(
            _event(
                "goal",
                record,
                actor_name=scorer_name,
                assist_name=str(assist_name) if assist_name else None,
                team=_safe_team(last_score.get("team")),
                value=_float(last_score.get("point_amount")),
                raw_text=f"{scorer_name} scored",
                metadata=goal_metadata,
            )
        )

        if assist_name:
            events.append(
                _event(
                    "assist",
                    record,
                    actor_name=str(assist_name),
                    target_name=scorer_name,
                    team=_safe_team(last_score.get("team")),
                    value=_float(last_score.get("point_amount")),
                    raw_text=f"{assist_name} assisted {scorer_name}",
                    metadata={"source": "last_score", **goal_metadata},
                )
            )
    elif positive_score_change:
        events.append(
            _event(
                "unknown",
                record,
                raw_text="score changed but last_score did not name a scorer",
                metadata={"reason": "score_change_without_valid_scorer", "last_score": last_score},
            )
        )

    return events, goal_names, assist_names


def _derive_stat_delta_events(
    record: RawSnapshotRecord,
    previous_players: Dict[str, Dict[str, Any]],
    current_players: Dict[str, Dict[str, Any]],
    goal_names: Set[str],
    assist_names: Set[str],
    recent_score_credits: Dict[Tuple[str, str], List[int]],
) -> List[NormalizedEvent]:
    events: List[NormalizedEvent] = []
    if not previous_players:
        return events

    for key, current_player in current_players.items():
        previous_player = previous_players.get(key)
        if not previous_player:
            continue

        for stat_name, event_type in STAT_EVENT_MAP.items():
            delta = _numeric_delta(previous_player.get("stats", {}).get(stat_name), current_player.get("stats", {}).get(stat_name))
            if delta is None or delta <= 0:
                continue

            actor_name = current_player.get("name")
            actor_folded = str(actor_name or "").casefold()
            if event_type == "goal" and actor_folded in goal_names:
                continue
            if event_type == "assist" and actor_folded in assist_names:
                continue
            if event_type in {"goal", "assist"} and _consume_score_credit(actor_folded, event_type, recent_score_credits):
                continue

            events.append(
                _player_event(
                    event_type,
                    record,
                    current_player,
                    value=delta,
                    raw_text=f"{actor_name} {stat_name} increased by {delta:g}",
                    metadata={
                        "source": "player_stat_delta",
                        "stat": stat_name,
                        "previous": previous_player.get("stats", {}).get(stat_name),
                        "current": current_player.get("stats", {}).get(stat_name),
                    },
                )
            )

    return events


def _remember_score_credits(events: List[NormalizedEvent], recent_score_credits: Dict[Tuple[str, str], List[int]]) -> None:
    for event in events:
        if event.event_type not in {"goal", "assist"} or not event.actor_name:
            continue
        # Echo API commonly applies player stat deltas a few snapshots after last_score.
        expire_sequence = (event.sequence or 0) + 20
        key = (event.actor_name.casefold(), event.event_type)
        recent_score_credits.setdefault(key, []).append(expire_sequence)


def _expire_score_credits(sequence: Optional[int], recent_score_credits: Dict[Tuple[str, str], List[int]]) -> None:
    if sequence is None:
        return
    expired_keys = []
    for key, expirations in recent_score_credits.items():
        recent_score_credits[key] = [expires_at for expires_at in expirations if expires_at >= sequence]
        if not recent_score_credits[key]:
            expired_keys.append(key)
    for key in expired_keys:
        recent_score_credits.pop(key, None)


def _consume_score_credit(actor_name: str, event_type: str, recent_score_credits: Dict[Tuple[str, str], List[int]]) -> bool:
    key = (actor_name, event_type)
    credits = recent_score_credits.get(key)
    if not credits:
        return False
    credits.pop(0)
    if not credits:
        recent_score_credits.pop(key, None)
    return True


def _derive_player_join_leave_events(
    record: RawSnapshotRecord,
    previous_players: Dict[str, Dict[str, Any]],
    current_players: Dict[str, Dict[str, Any]],
) -> List[NormalizedEvent]:
    events: List[NormalizedEvent] = []

    for key in sorted(set(current_players) - set(previous_players)):
        player = current_players[key]
        events.append(
            _player_event(
                "player_join",
                record,
                player,
                raw_text=f"{player.get('name')} joined",
                metadata={"player_key": key},
            )
        )

    for key in sorted(set(previous_players) - set(current_players)):
        player = previous_players[key]
        events.append(
            _player_event(
                "player_leave",
                record,
                player,
                raw_text=f"{player.get('name')} left",
                metadata={"player_key": key},
            )
        )

    return events


def _derive_possession_events(
    record: RawSnapshotRecord,
    previous_possession: Optional[Dict[str, Any]],
    current_possession: Dict[str, Any],
) -> List[NormalizedEvent]:
    if previous_possession is None or previous_possession == current_possession:
        return []

    current_player = current_possession.get("players", [None])[0] if current_possession.get("players") else None
    previous_player = previous_possession.get("players", [None])[0] if previous_possession.get("players") else None

    return [
        _event(
            "possession_change",
            record,
            actor_name=current_player.get("name") if isinstance(current_player, dict) else None,
            target_name=previous_player.get("name") if isinstance(previous_player, dict) else None,
            team=current_player.get("team") if isinstance(current_player, dict) else current_possession.get("team"),
            raw_text="possession changed",
            metadata={"previous": _possession_for_metadata(previous_possession), "current": _possession_for_metadata(current_possession)},
        )
    ]


def _possession_for_metadata(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "top_level": value.get("top_level"),
        "teams": value.get("teams"),
        "players": [
            {"name": player.get("name"), "team": player.get("team"), "userid": player.get("userid"), "playerid": player.get("playerid")}
            for player in value.get("players", [])
        ],
    }


def _derive_unknown_snapshot_events(record: RawSnapshotRecord) -> List[NormalizedEvent]:
    snapshot = record.snapshot
    events: List[NormalizedEvent] = []
    err_code = snapshot.get("err_code")
    if err_code not in (None, 0, "0"):
        events.append(
            _event(
                "unknown",
                record,
                raw_text=f"snapshot reported err_code {err_code}",
                metadata={"reason": "snapshot_error_code", "err_code": err_code},
            )
        )

    teams = snapshot.get("teams")
    if teams is not None and not isinstance(teams, list):
        events.append(
            _event(
                "unknown",
                record,
                raw_text="snapshot teams field was not a list",
                metadata={"reason": "malformed_teams", "teams_type": type(teams).__name__},
            )
        )

    return events


def _derive_unknown_stat_events(
    record: RawSnapshotRecord,
    previous_players: Dict[str, Dict[str, Any]],
    current_players: Dict[str, Dict[str, Any]],
) -> List[NormalizedEvent]:
    events: List[NormalizedEvent] = []
    known_stats = set(STAT_EVENT_MAP) | {"points", "possession_time"}
    if not previous_players:
        return events

    for key, current_player in current_players.items():
        previous_player = previous_players.get(key)
        if not previous_player:
            continue

        current_stats = current_player.get("raw_stats", {})
        previous_stats = previous_player.get("raw_stats", {})
        if not isinstance(current_stats, dict) or not isinstance(previous_stats, dict):
            continue

        for stat_name, current_value in current_stats.items():
            if stat_name in known_stats:
                continue
            delta = _numeric_delta(previous_stats.get(stat_name), current_value)
            if delta is None or delta <= 0:
                continue
            events.append(
                _player_event(
                    "unknown",
                    record,
                    current_player,
                    value=delta,
                    raw_text=f"unsupported stat {stat_name} increased by {delta:g}",
                    metadata={
                        "reason": "unsupported_stat_delta",
                        "stat": stat_name,
                        "previous": previous_stats.get(stat_name),
                        "current": current_value,
                    },
                )
            )

    return events


def _update_summary(result: EventDerivationResult, record: RawSnapshotRecord) -> None:
    snapshot = record.snapshot
    if result.first_captured_at is None:
        result.first_captured_at = record.captured_at
    result.last_captured_at = record.captured_at

    result.detected_sessionid = sp.sessionid(snapshot) or result.detected_sessionid
    result.detected_sessionip = sp.sessionip(snapshot) or result.detected_sessionip
    result.detected_match_type = sp.match_type(snapshot) or result.detected_match_type
    result.detected_map_name = sp.map_name(snapshot) or result.detected_map_name
    result.detected_client_name = sp.client_name(snapshot) or result.detected_client_name
    blue_score, orange_score = sp.score(snapshot)
    status = sp.game_status(snapshot)
    should_update_score = (
        result.latest_blue_score is None
        or status not in {"pre_match", ""}
        or bool(blue_score)
        or bool(orange_score)
    )
    if should_update_score:
        result.latest_blue_score = blue_score
        result.latest_orange_score = orange_score

    for team in sp.parsed_teams(snapshot):
        key = f"{team['index']}:{team.get('label') or team['team']}"
        result.detected_teams[key] = {
            "index": team["index"],
            "team": team["team"],
            "label": team.get("label"),
            "possession": team.get("possession"),
            "stats": team.get("stats", {}),
        }

    for key, player in sp.player_lookup(snapshot).items():
        existing = result.detected_players.setdefault(
            key,
            {
                "name": player.get("name"),
                "aliases": [],
                "userid": player.get("userid"),
                "playerid": player.get("playerid"),
                "number": player.get("number"),
                "level": player.get("level"),
                "team": player.get("team"),
                "teams": [],
            },
        )
        _append_unique(existing["aliases"], player.get("name"))
        _append_unique(existing["teams"], player.get("team"))
        existing.update(
            {
                "name": player.get("name"),
                "userid": player.get("userid"),
                "playerid": player.get("playerid"),
                "number": player.get("number"),
                "level": player.get("level"),
                "team": player.get("team"),
            }
        )
        previous_latest = result.latest_player_stats.get(key, {})
        result.latest_player_stats[key] = {
            "name": player.get("name"),
            "userid": player.get("userid"),
            "playerid": player.get("playerid"),
            "team": player.get("team"),
            "stats": _merge_best_stats(previous_latest.get("stats", {}), player.get("stats", {})),
        }


def _possession_signature(snapshot: Dict[str, Any], players: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    team_possession = []
    for team in sp.parsed_teams(snapshot):
        if team.get("possession"):
            team_possession.append({"team": team.get("team"), "index": team.get("index"), "label": team.get("label")})

    player_possession = []
    for key, player in players.items():
        if player.get("possession"):
            player_possession.append(
                {
                    "key": key,
                    "name": player.get("name"),
                    "team": player.get("team"),
                    "userid": player.get("userid"),
                    "playerid": player.get("playerid"),
                }
            )

    return {
        "top_level": sp.possession(snapshot),
        "teams": sorted(team_possession, key=lambda team: int(team.get("index", 99))),
        "players": sorted(player_possession, key=lambda player: str(player.get("key", ""))),
        "team": team_possession[0]["team"] if team_possession else None,
    }


def _player_event(
    event_type: str,
    record: RawSnapshotRecord,
    player: Dict[str, Any],
    value: Optional[float] = None,
    raw_text: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> NormalizedEvent:
    return _event(
        event_type,
        record,
        actor_name=player.get("name"),
        actor_userid=player.get("userid"),
        actor_playerid=player.get("playerid"),
        team=player.get("team"),
        value=value,
        raw_text=raw_text,
        metadata=metadata,
    )


def _event(
    event_type: str,
    record: RawSnapshotRecord,
    actor_name: Optional[str] = None,
    target_name: Optional[str] = None,
    assist_name: Optional[str] = None,
    actor_userid: Optional[str] = None,
    target_userid: Optional[str] = None,
    assist_userid: Optional[str] = None,
    actor_playerid: Optional[str] = None,
    target_playerid: Optional[str] = None,
    assist_playerid: Optional[str] = None,
    team: Optional[str] = None,
    value: Optional[float] = None,
    raw_text: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> NormalizedEvent:
    return NormalizedEvent(
        event_type=event_type,
        sequence=record.sequence,
        captured_at=record.captured_at,
        game_clock=sp.game_clock(record.snapshot),
        game_clock_display=sp.game_clock_display(record.snapshot),
        actor_name=actor_name,
        target_name=target_name,
        assist_name=assist_name,
        actor_userid=_optional_str(actor_userid),
        target_userid=_optional_str(target_userid),
        assist_userid=_optional_str(assist_userid),
        actor_playerid=_optional_str(actor_playerid),
        target_playerid=_optional_str(target_playerid),
        assist_playerid=_optional_str(assist_playerid),
        team=team,
        value=value,
        raw_text=raw_text,
        metadata=metadata or {},
    )


def _score_metadata(previous_blue: Optional[int], previous_orange: Optional[int], current_blue: Optional[int], current_orange: Optional[int]) -> Dict[str, Any]:
    return {
        "previous_score": {"blue": previous_blue, "orange": previous_orange},
        "current_score": {"blue": current_blue, "orange": current_orange},
    }


def _numeric_delta(previous_value: Any, current_value: Any) -> Optional[float]:
    previous = _float(previous_value)
    current = _float(current_value)
    if previous is None or current is None:
        return None
    return current - previous


def _float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_team(value: Any) -> Optional[str]:
    if value is None:
        return None
    team = str(value).strip().lower()
    return team or None


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _append_unique(values: List[Any], value: Any) -> None:
    if value is not None and value not in values:
        values.append(value)


def _merge_best_stats(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(previous or {})
    for key, value in (current or {}).items():
        previous_value = _float(merged.get(key))
        current_value = _float(value)
        if previous_value is not None and current_value is not None:
            merged[key] = value if current_value >= previous_value else merged.get(key)
        elif key not in merged:
            merged[key] = value
    return merged
