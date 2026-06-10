"""Shared match context constants and helpers."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional


PRIVATE_MATCH_TYPES = ("PUG", "Scrimmage", "Official", "Casual", "Unknown")
PRIVATE_MATCH_TYPE_LABELS = {
    "PUG": "PUG / Ranked Pickup",
    "Scrimmage": "Scrimmage",
    "Official": "Official",
    "Casual": "Casual / Testing",
    "Unknown": "Unknown",
}

MEANINGFUL_STAT_KEYS = (
    "points",
    "goals",
    "assists",
    "saves",
    "stuns",
    "steals",
    "shots",
    "shots_taken",
    "passes",
    "catches",
    "interceptions",
    "blocks",
    "possession_time",
)


def normalize_private_match_type(value: Any, allow_none: bool = True) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None if allow_none else "Unknown"
    text = str(value).strip().casefold()
    mapping = {
        "pug": "PUG",
        "ranked pickup": "PUG",
        "pickup": "PUG",
        "scrimmage": "Scrimmage",
        "official": "Official",
        "casual": "Casual",
        "casual / testing": "Casual",
        "testing": "Casual",
        "unknown": "Unknown",
    }
    normalized = mapping.get(text)
    if normalized is None:
        raise ValueError(
            "Private match type must be one of: PUG, Scrimmage, Official, Casual, Unknown."
        )
    return normalized


def private_match_type_label(value: Any) -> str:
    normalized = normalize_private_match_type(value, allow_none=False)
    return PRIVATE_MATCH_TYPE_LABELS.get(normalized, normalized)


def meaningful_stat_total(stats: Dict[str, Any]) -> float:
    total = 0.0
    for key in MEANINGFUL_STAT_KEYS:
        try:
            total += float(stats.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


def has_meaningful_participation(stats: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> bool:
    if meaningful_stat_total(stats) > 0:
        return True
    metadata = metadata or {}
    if isinstance(metadata, dict) and "active_participation" in metadata:
        return bool(metadata.get("active_participation"))
    afk = metadata.get("afk_detection") if isinstance(metadata, dict) else {}
    row_live_samples = int(metadata.get("live_samples") or 0) if isinstance(metadata, dict) else 0
    if row_live_samples >= 120 and not bool((afk or {}).get("suspected")):
        return True
    return False


def point_winner(blue_points: Any, orange_points: Any) -> Optional[str]:
    blue = _int(blue_points)
    orange = _int(orange_points)
    if blue is None or orange is None:
        return None
    if blue > orange:
        return "blue"
    if orange > blue:
        return "orange"
    return "tie"


def round_winner(blue_round_wins: Any, orange_round_wins: Any) -> Optional[str]:
    blue = _int(blue_round_wins)
    orange = _int(orange_round_wins)
    if blue is None or orange is None:
        return None
    if blue > orange:
        return "blue"
    if orange > blue:
        return "orange"
    return "tie"


def round_record_warning(match: Dict[str, Any]) -> Optional[str]:
    point = point_winner(match.get("blue_score"), match.get("orange_score"))
    rounds = round_winner(match.get("blue_round_wins"), match.get("orange_round_wins"))
    if point in {None, "tie"} or rounds in {None, "tie"}:
        return None
    if point != rounds:
        return "Point winner and round-record winner differ."
    return None


def participant_identity_key(
    *,
    player_id: Any = None,
    userid: Any = None,
    match_alias: Any = None,
) -> str:
    if player_id is not None:
        return f"player:{player_id}"
    if userid:
        return f"userid:{str(userid).strip().casefold()}"
    alias = str(match_alias or "").strip().casefold()
    return f"alias:{alias}"


def dominant_team(rows: Iterable[Dict[str, Any]]) -> Optional[str]:
    best_team = None
    best_value = -1.0
    for row in rows:
        value = meaningful_stat_total(row.get("stats", {}))
        if value > best_value:
            best_value = value
            best_team = row.get("team")
    return best_team


def _int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
