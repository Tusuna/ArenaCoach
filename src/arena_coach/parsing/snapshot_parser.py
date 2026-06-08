"""Safe extractors for Echo Arena snapshots."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple


PLAYER_STAT_KEYS = (
    "possession_time",
    "points",
    "saves",
    "goals",
    "stuns",
    "passes",
    "catches",
    "steals",
    "blocks",
    "interceptions",
    "assists",
    "shots_taken",
)

PLAYER_FIELDS = (
    "name",
    "userid",
    "playerid",
    "number",
    "level",
    "ping",
    "stats",
    "stunned",
    "blocking",
    "invulnerable",
    "possession",
    "holding_left",
    "holding_right",
    "velocity",
    "head",
    "body",
    "lhand",
    "rhand",
)

TOP_LEVEL_FIELDS = (
    "sessionid",
    "sessionip",
    "game_status",
    "game_clock",
    "game_clock_display",
    "match_type",
    "map_name",
    "client_name",
    "orange_points",
    "blue_points",
    "private_match",
    "tournament_match",
    "blue_round_score",
    "orange_round_score",
    "total_round_count",
    "last_score",
    "teams",
    "disc",
    "possession",
)


def top_level(snapshot: Any) -> Dict[str, Any]:
    data = _dict(snapshot)
    return {key: data.get(key) for key in TOP_LEVEL_FIELDS}


def sessionid(snapshot: Any) -> Optional[str]:
    return _str(_dict(snapshot).get("sessionid"))


def sessionip(snapshot: Any) -> Optional[str]:
    return _str(_dict(snapshot).get("sessionip"))


def game_status(snapshot: Any) -> str:
    return _str(_dict(snapshot).get("game_status")) or ""


def game_clock(snapshot: Any) -> Optional[float]:
    return _float(_dict(snapshot).get("game_clock"))


def game_clock_display(snapshot: Any) -> Optional[str]:
    return _str(_dict(snapshot).get("game_clock_display"))


def match_type(snapshot: Any) -> Optional[str]:
    return _str(_dict(snapshot).get("match_type"))


def map_name(snapshot: Any) -> Optional[str]:
    return _str(_dict(snapshot).get("map_name"))


def client_name(snapshot: Any) -> Optional[str]:
    return _str(_dict(snapshot).get("client_name"))


def score(snapshot: Any) -> Tuple[Optional[int], Optional[int]]:
    data = _dict(snapshot)
    return _int(data.get("blue_points")), _int(data.get("orange_points"))


def last_score(snapshot: Any) -> Dict[str, Any]:
    return _dict(_dict(snapshot).get("last_score"))


def round_score(snapshot: Any) -> Tuple[Optional[int], Optional[int]]:
    data = _dict(snapshot)
    return _int(data.get("blue_round_score")), _int(data.get("orange_round_score"))


def total_round_count(snapshot: Any) -> Optional[int]:
    return _int(_dict(snapshot).get("total_round_count"))


def disc(snapshot: Any) -> Dict[str, Any]:
    return _dict(_dict(snapshot).get("disc"))


def possession(snapshot: Any) -> Any:
    return _dict(snapshot).get("possession")


def teams(snapshot: Any) -> List[Dict[str, Any]]:
    team_value = _dict(snapshot).get("teams")
    if not isinstance(team_value, list):
        return []
    return [_dict(team) for team in team_value]


def team(snapshot: Any, index: int) -> Dict[str, Any]:
    team_list = teams(snapshot)
    if index < 0 or index >= len(team_list):
        return {}
    return team_list[index]


def blue_team(snapshot: Any) -> Dict[str, Any]:
    return team(snapshot, 0)


def orange_team(snapshot: Any) -> Dict[str, Any]:
    return team(snapshot, 1)


def parse_team(team_data: Any, index: int) -> Dict[str, Any]:
    data = _dict(team_data)
    return {
        "index": index,
        "team": team_color(index),
        "label": _str(data.get("team")),
        "possession": data.get("possession"),
        "stats": _dict(data.get("stats")),
        "players": [parse_player(player, index) for player in _list(data.get("players")) if isinstance(player, dict)],
    }


def parsed_teams(snapshot: Any) -> List[Dict[str, Any]]:
    return [parse_team(team_data, index) for index, team_data in enumerate(teams(snapshot))]


def iter_players(snapshot: Any) -> Iterable[Dict[str, Any]]:
    for parsed_team in parsed_teams(snapshot):
        for player in parsed_team["players"]:
            yield player


def player_stats(player_data: Any) -> Dict[str, Any]:
    stats = _dict(_dict(player_data).get("stats"))
    return {key: stats.get(key) for key in PLAYER_STAT_KEYS}


def parse_player(player_data: Any, team_index: int) -> Dict[str, Any]:
    data = _dict(player_data)
    parsed = {key: data.get(key) for key in PLAYER_FIELDS}
    parsed["name"] = _str(parsed.get("name"))
    parsed["userid"] = _str(parsed.get("userid"))
    parsed["playerid"] = _str(parsed.get("playerid"))
    parsed["team"] = team_color(team_index)
    parsed["team_index"] = team_index
    parsed["raw_stats"] = _dict(data.get("stats"))
    parsed["stats"] = player_stats(data)
    return parsed


def player_key(player: Dict[str, Any]) -> str:
    userid = _str(player.get("userid"))
    if userid:
        return f"userid:{userid}"
    name = (_str(player.get("name")) or "").casefold()
    if name:
        return f"name:{name}"
    playerid = _str(player.get("playerid"))
    if playerid:
        return f"playerid:{player.get('team_index')}:{playerid}"
    return f"unknown:{id(player)}"


def player_lookup(snapshot: Any) -> Dict[str, Dict[str, Any]]:
    return {player_key(player): player for player in iter_players(snapshot)}


def team_color(index: int) -> str:
    if index == 0:
        return "blue"
    if index == 1:
        return "orange"
    return "spectator"


def valid_player_name(value: Any) -> bool:
    text = _str(value)
    return bool(text and text != "[INVALID]" and text.lower() != "none")


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
