"""Match quality helpers."""

from __future__ import annotations

from typing import Dict, List

from arena_coach.match_context import participant_identity_key
from arena_coach.stats.stat_models import LoadedMatch, MatchParticipant, MatchQuality


TRACKED_STATS = (
    "points",
    "goals",
    "assists",
    "saves",
    "stuns",
    "steals",
    "shots",
    "passes",
    "catches",
    "interceptions",
    "blocks",
    "turnovers",
)


def classify_match_quality(match: LoadedMatch) -> MatchQuality:
    participants = [participant for participant in match.participants if (participant.team or "").casefold() != "spectator"]
    identities: Dict[str, List[MatchParticipant]] = {}
    for participant in participants:
        identities.setdefault(_identity_key(participant), []).append(participant)

    active_non_afk = [rows[0] for rows in identities.values() if any(is_active_non_afk(participant) for participant in rows)]
    suspected_afk = [rows[0] for rows in identities.values() if any(participant.afk_suspected for participant in rows)]
    mapped = [rows[0] for rows in identities.values() if any(participant.player_id is not None for participant in rows)]
    guests = [rows[0] for rows in identities.values() if all(participant.player_id is None for participant in rows)]
    self_identities = [rows for rows in identities.values() if any(participant.is_user for participant in rows)]
    self_count = len(self_identities)
    team_switch_affected = any(
        len({(participant.team or "").casefold() for participant in rows if participant.meaningful_participation}) > 1
        for rows in self_identities
    )

    reasons: List[str] = []
    if not match.finalized:
        reasons.append("unfinalized_match")
    if self_count == 0:
        reasons.append("missing_self_player")
    if match.blue_score is None or match.orange_score is None:
        reasons.append("missing_final_score")
    if len(active_non_afk) < 6:
        reasons.append("fewer_than_6_active_non_afk_players")
    if _suspected_data_corruption(match, participants, self_count):
        reasons.append("suspected_data_corruption")
    if team_switch_affected:
        reasons.append("team_switch_affected")

    is_low_quality = any(
        reason in reasons
        for reason in (
            "unfinalized_match",
            "missing_self_player",
            "missing_final_score",
            "fewer_than_6_active_non_afk_players",
            "suspected_data_corruption",
            "team_switch_affected",
        )
    )
    if not match.finalized:
        label = "Unreviewed"
    elif is_low_quality:
        label = "Low Quality"
    elif suspected_afk:
        label = "AFK Affected"
    else:
        label = "Competitive Eligible"

    competitive_eligible = match.finalized and not is_low_quality and self_count == 1

    return MatchQuality(
        active_non_afk_player_count=len(active_non_afk),
        suspected_afk_count=len(suspected_afk),
        mapped_player_count=len(mapped),
        guest_player_count=len(guests),
        has_self=self_count == 1,
        match_classification=match.match_classification or "Unknown",
        is_low_quality=is_low_quality,
        quality_reasons=reasons,
        quality_label=label,
        competitive_eligible=competitive_eligible,
        team_switch_affected=team_switch_affected,
    )


def is_active_non_afk(participant: MatchParticipant) -> bool:
    if participant.afk_suspected:
        return False
    return participant.meaningful_participation or participant_activity_value(participant) > 0 or int(participant.live_samples or 0) >= 120


def participant_activity_value(participant: MatchParticipant) -> float:
    total = 0.0
    for key in TRACKED_STATS:
        total += float(participant.stats.get(key) or 0)
    total += float(participant.stats.get("possession_time") or 0)
    return total


def quality_to_dict(quality: MatchQuality) -> Dict[str, object]:
    return {
        "active_non_afk_player_count": quality.active_non_afk_player_count,
        "suspected_afk_count": quality.suspected_afk_count,
        "mapped_player_count": quality.mapped_player_count,
        "guest_player_count": quality.guest_player_count,
        "has_self": quality.has_self,
        "match_classification": quality.match_classification,
        "is_low_quality": quality.is_low_quality,
        "quality_reasons": list(quality.quality_reasons),
        "quality_label": quality.quality_label,
        "competitive_eligible": quality.competitive_eligible,
        "team_switch_affected": quality.team_switch_affected,
    }


def _suspected_data_corruption(match: LoadedMatch, participants: List[MatchParticipant], self_count: int) -> bool:
    if self_count > 1:
        return True
    if not participants:
        return True
    if match.blue_score is not None and int(match.blue_score) < 0:
        return True
    if match.orange_score is not None and int(match.orange_score) < 0:
        return True
    for participant in participants:
        if participant.stats and (participant.team or "").casefold() not in {"blue", "orange", "spectator"}:
            return True
    return False


def _identity_key(participant: MatchParticipant) -> str:
    if participant.participant_key:
        return participant.participant_key
    return participant_identity_key(
        player_id=participant.player_id,
        userid=participant.userid,
        match_alias=participant.match_alias,
    )
