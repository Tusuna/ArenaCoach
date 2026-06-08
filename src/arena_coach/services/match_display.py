"""User-facing match names and lightweight match classification."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from arena_coach.match_context import normalize_private_match_type


def classify_match(match_type: Optional[str], private_match: Any = None, tournament_match: Any = None) -> str:
    if _truthy(tournament_match):
        return "Tournament"
    match_type_text = str(match_type or "").casefold()
    if _truthy(private_match) or "private" in match_type_text:
        return "Private"
    if match_type_text == "echo_arena" or private_match is False:
        return "Public"
    return "Unknown"


def build_match_display_name(match: Dict[str, Any]) -> str:
    status = "Finalized" if bool(match.get("finalized")) else "Unreviewed"
    classification = match.get("match_classification") or classify_match(match.get("match_type"))
    private_type = normalize_private_match_type(match.get("private_match_type"), allow_none=True)
    date_text = _date_text(match.get("started_at") or match.get("created_at"))
    result = match.get("result")
    blue = match.get("blue_score")
    orange = match.get("orange_score")
    user_team = match.get("user_team")
    class_text = classification
    if classification == "Private" and private_type:
        class_text = f"{classification} {private_type}"
    if result and user_team in {"blue", "orange"}:
        own = blue if user_team == "blue" else orange
        other = orange if user_team == "blue" else blue
        score_text = f"{str(result).title()} {own}-{other}"
    else:
        score_text = f"Blue {blue} / Orange {orange}"
    rounds_text = _round_text(match)
    if rounds_text:
        score_text = f"{score_text} - {rounds_text}"
    return f"{status} {class_text} {date_text} - {score_text}"


def _round_text(match: Dict[str, Any]) -> str:
    blue_rounds = match.get("blue_round_wins")
    orange_rounds = match.get("orange_round_wins")
    total_rounds = match.get("total_rounds_played")
    try:
        blue_value = int(blue_rounds or 0)
        orange_value = int(orange_rounds or 0)
        total_value = int(total_rounds or 0)
    except (TypeError, ValueError):
        return ""
    if total_value <= 1 and blue_value + orange_value <= 1:
        return ""
    return f"Rounds Blue {blue_value}-{orange_value}"


def _date_text(value: Any) -> str:
    if not value:
        return "Unknown Time"
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return parsed.strftime("%Y-%m-%d %I:%M %p").lstrip("0")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().casefold() in {"true", "1", "yes"}
