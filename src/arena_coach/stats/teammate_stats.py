"""Teammate synergy aggregation."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List

from arena_coach.stats.stat_models import LoadedMatch, MatchParticipant, TeammateSummary


USER_TRACKED = ("points", "goals", "assists", "saves", "stuns", "steals", "shots", "passes", "catches", "interceptions", "blocks")
TEAMMATE_TRACKED = ("points", "goals", "assists", "saves", "stuns", "steals")


def calculate_teammates(
    matches: Iterable[LoadedMatch],
    include_guests: bool = False,
    include_afk: bool = False,
) -> List[TeammateSummary]:
    summaries: Dict[str, Dict[str, object]] = {}

    for match in matches:
        self_rows = [
            row
            for row in match.self_participants()
            if (row.team or "").casefold() in {"blue", "orange"}
            and (row.meaningful_participation or (include_afk and row.afk_suspected))
            and (include_afk or not row.afk_suspected)
        ]
        if not self_rows:
            continue
        per_match: Dict[str, Dict[str, object]] = {}

        for participant in match.participants:
            if participant.is_user:
                continue
            if participant.afk_suspected and not include_afk:
                continue
            if participant.player_id is None and not include_guests:
                continue
            if not participant.meaningful_participation and not (include_afk and participant.afk_suspected):
                continue
            matching_self_rows = [
                self_row
                for self_row in self_rows
                if (participant.team or "").casefold() == (self_row.team or "").casefold()
            ]
            if not matching_self_rows:
                continue
            key = _entity_key(participant)
            bucket = per_match.setdefault(key, {"participant": participant, "rows": [], "self_rows": []})
            bucket["rows"].append(participant)
            for self_row in matching_self_rows:
                if self_row not in bucket["self_rows"]:
                    bucket["self_rows"].append(self_row)

        for key, bucket in per_match.items():
            participant = bucket["participant"]
            teammate_rows = bucket["rows"]
            relevant_self_rows = bucket["self_rows"] or self_rows
            primary_team = (relevant_self_rows[0].team or "").casefold()
            team_score = match.blue_score if primary_team == "blue" else match.orange_score

            summary = summaries.setdefault(
                key,
                {
                    "player_id": participant.player_id,
                    "display_name": participant.display_name,
                    "is_guest": participant.player_id is None,
                    "matches_together": 0,
                    "wins_together": 0,
                    "losses_together": 0,
                    "ties_together": 0,
                    "user_totals": defaultdict(float),
                    "teammate_totals": defaultdict(float),
                    "team_score_total": 0.0,
                    "teammate_afk_count": 0,
                    "low_quality_match_count": 0,
                },
            )
            summary["matches_together"] = int(summary["matches_together"]) + 1
            if match.result == "win":
                summary["wins_together"] = int(summary["wins_together"]) + 1
            elif match.result == "loss":
                summary["losses_together"] = int(summary["losses_together"]) + 1
            elif match.result == "tie":
                summary["ties_together"] = int(summary["ties_together"]) + 1
            if any(row.afk_suspected for row in teammate_rows):
                summary["teammate_afk_count"] = int(summary["teammate_afk_count"]) + 1
            if match.quality and match.quality.is_low_quality:
                summary["low_quality_match_count"] = int(summary["low_quality_match_count"]) + 1
            summary["team_score_total"] = float(summary["team_score_total"]) + float(team_score or 0)

            for stat_name in USER_TRACKED:
                summary["user_totals"][stat_name] += sum(float(self_row.stats.get(stat_name) or 0) for self_row in relevant_self_rows)
            for stat_name in TEAMMATE_TRACKED:
                summary["teammate_totals"][stat_name] += sum(float(row.stats.get(stat_name) or 0) for row in teammate_rows)

    results: List[TeammateSummary] = []
    for key, summary in summaries.items():
        matches_together = int(summary["matches_together"])
        wins_together = int(summary["wins_together"])
        losses_together = int(summary["losses_together"])
        ties_together = int(summary["ties_together"])
        results.append(
            TeammateSummary(
                entity_key=key,
                player_id=summary["player_id"],
                display_name=str(summary["display_name"]),
                is_guest=bool(summary["is_guest"]),
                matches_together=matches_together,
                wins_together=wins_together,
                losses_together=losses_together,
                ties_together=ties_together,
                win_rate_together=round((wins_together / matches_together) * 100, 2) if matches_together else 0.0,
                user_averages=_averages(dict(summary["user_totals"]), matches_together),
                teammate_averages=_averages(dict(summary["teammate_totals"]), matches_together),
                team_score_average=round(float(summary["team_score_total"]) / matches_together, 2) if matches_together else 0.0,
                teammate_afk_count=int(summary["teammate_afk_count"]),
                low_quality_match_count=int(summary["low_quality_match_count"]),
                confidence=_confidence(matches_together),
            )
        )

    return sorted(results, key=lambda item: (-item.matches_together, -item.win_rate_together, item.display_name.casefold()))


def teammate_to_dict(summary: TeammateSummary) -> Dict[str, object]:
    return {
        "entity_key": summary.entity_key,
        "player_id": summary.player_id,
        "display_name": summary.display_name,
        "is_guest": summary.is_guest,
        "matches_together": summary.matches_together,
        "wins_together": summary.wins_together,
        "losses_together": summary.losses_together,
        "ties_together": summary.ties_together,
        "win_rate_together": summary.win_rate_together,
        "user_averages": summary.user_averages,
        "teammate_averages": summary.teammate_averages,
        "team_score_average": summary.team_score_average,
        "teammate_afk_count": summary.teammate_afk_count,
        "low_quality_match_count": summary.low_quality_match_count,
        "confidence": summary.confidence,
    }


def _entity_key(participant: MatchParticipant) -> str:
    if participant.player_id is not None:
        return f"player:{participant.player_id}"
    return f"guest:{participant.match_alias.casefold()}"


def _averages(totals: Dict[str, float], matches_together: int) -> Dict[str, float]:
    if matches_together <= 0:
        return {key: 0.0 for key in totals}
    return {key: round(value / matches_together, 2) for key, value in totals.items()}


def _confidence(matches_together: int) -> str:
    if matches_together < 5:
        return "Low sample"
    if matches_together < 15:
        return "Medium sample"
    return "High sample"
