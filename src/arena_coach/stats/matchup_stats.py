"""Opponent matchup aggregation."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List

from arena_coach.stats.stat_models import LoadedMatch, MatchParticipant, MatchupSummary


TRACKED = ("points", "goals", "assists", "saves", "stuns", "steals", "shots", "passes", "catches", "interceptions", "blocks")


def calculate_matchups(
    matches: Iterable[LoadedMatch],
    include_guests: bool = False,
    include_afk: bool = False,
) -> List[MatchupSummary]:
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
            if (participant.team or "").casefold() == "spectator":
                continue
            if participant.afk_suspected and not include_afk:
                continue
            if participant.player_id is None and not include_guests:
                continue
            if not participant.meaningful_participation and not (include_afk and participant.afk_suspected):
                continue
            if not any((participant.team or "").casefold() != (self_row.team or "").casefold() for self_row in self_rows):
                continue

            key = _entity_key(participant)
            bucket = per_match.setdefault(key, {"participant": participant, "rows": [], "self_rows": []})
            bucket["rows"].append(participant)
            for self_row in self_rows:
                if (participant.team or "").casefold() != (self_row.team or "").casefold():
                    if self_row not in bucket["self_rows"]:
                        bucket["self_rows"].append(self_row)

        for key, bucket in per_match.items():
            participant = bucket["participant"]
            opponent_rows = bucket["rows"]
            relevant_self_rows = bucket["self_rows"] or self_rows

            summary = summaries.setdefault(
                key,
                {
                    "player_id": participant.player_id,
                    "display_name": participant.display_name,
                    "is_guest": participant.player_id is None,
                    "matches_against": 0,
                    "wins_against": 0,
                    "losses_against": 0,
                    "ties_against": 0,
                    "user_totals": defaultdict(float),
                    "opponent_totals": defaultdict(float),
                    "direct_stuns_against_user": 0,
                    "direct_steals_against_user": 0,
                    "opponent_context_notes": set(),
                },
            )
            summary["matches_against"] = int(summary["matches_against"]) + 1
            if match.result == "win":
                summary["wins_against"] = int(summary["wins_against"]) + 1
            elif match.result == "loss":
                summary["losses_against"] = int(summary["losses_against"]) + 1
            elif match.result == "tie":
                summary["ties_against"] = int(summary["ties_against"]) + 1

            for stat_name in TRACKED:
                summary["user_totals"][stat_name] += sum(float(self_row.stats.get(stat_name) or 0) for self_row in relevant_self_rows)
                summary["opponent_totals"][stat_name] += sum(float(row.stats.get(stat_name) or 0) for row in opponent_rows)

            targeted_stun = 0
            targeted_steal = 0
            for self_row in relevant_self_rows:
                for opponent_row in opponent_rows:
                    targeted = _targeted_event_counts(match.events, opponent_row.player_id, self_row.player_id)
                    targeted_stun += targeted["stun"]
                    targeted_steal += targeted["steal"]
            summary["direct_stuns_against_user"] = int(summary["direct_stuns_against_user"]) + targeted_stun
            summary["direct_steals_against_user"] = int(summary["direct_steals_against_user"]) + targeted_steal
            if targeted_stun == 0:
                summary["opponent_context_notes"].add("stuns are opponent-context totals unless target data exists")
            if targeted_steal == 0:
                summary["opponent_context_notes"].add("steals are opponent-context totals unless target data exists")

    results: List[MatchupSummary] = []
    for key, summary in summaries.items():
        matches_against = int(summary["matches_against"])
        wins_against = int(summary["wins_against"])
        losses_against = int(summary["losses_against"])
        ties_against = int(summary["ties_against"])
        opponent_totals = dict(summary["opponent_totals"])
        user_totals = dict(summary["user_totals"])
        differentials = {name: round(user_totals.get(name, 0.0) - opponent_totals.get(name, 0.0), 2) for name in TRACKED}
        results.append(
            MatchupSummary(
                entity_key=key,
                player_id=summary["player_id"],
                display_name=str(summary["display_name"]),
                is_guest=bool(summary["is_guest"]),
                matches_against=matches_against,
                wins_against=wins_against,
                losses_against=losses_against,
                ties_against=ties_against,
                win_rate_against=round((wins_against / matches_against) * 100, 2) if matches_against else 0.0,
                user_totals=user_totals,
                opponent_totals=opponent_totals,
                differentials=differentials,
                direct_stuns_against_user=int(summary["direct_stuns_against_user"]),
                direct_steals_against_user=int(summary["direct_steals_against_user"]),
                opponent_context_notes=sorted(summary["opponent_context_notes"]),
            )
        )

    return sorted(
        results,
        key=lambda item: (-item.matches_against, item.win_rate_against, item.display_name.casefold()),
    )


def matchup_to_dict(summary: MatchupSummary) -> Dict[str, object]:
    return {
        "entity_key": summary.entity_key,
        "player_id": summary.player_id,
        "display_name": summary.display_name,
        "is_guest": summary.is_guest,
        "matches_against": summary.matches_against,
        "wins_against": summary.wins_against,
        "losses_against": summary.losses_against,
        "ties_against": summary.ties_against,
        "win_rate_against": summary.win_rate_against,
        "user_totals": summary.user_totals,
        "opponent_totals": summary.opponent_totals,
        "differentials": summary.differentials,
        "direct_stuns_against_user": summary.direct_stuns_against_user,
        "direct_steals_against_user": summary.direct_steals_against_user,
        "opponent_context_notes": list(summary.opponent_context_notes),
    }


def _entity_key(participant: MatchParticipant) -> str:
    if participant.player_id is not None:
        return f"player:{participant.player_id}"
    return f"guest:{participant.match_alias.casefold()}"


def _targeted_event_counts(events: List[Dict[str, object]], actor_player_id: int | None, target_player_id: int | None) -> Dict[str, int]:
    if actor_player_id is None or target_player_id is None:
        return {"stun": 0, "steal": 0}
    counts = {"stun": 0, "steal": 0}
    for event in events:
        if event.get("actor_player_id") != actor_player_id:
            continue
        if event.get("target_player_id") != target_player_id:
            continue
        if event.get("event_type") in counts:
            counts[str(event["event_type"])] += 1
    return counts
