"""Pure stats engine operating on loaded matches."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional

from arena_coach.stats.matchup_stats import calculate_matchups, matchup_to_dict
from arena_coach.stats.quality_filters import TRACKED_STATS, classify_match_quality, quality_to_dict
from arena_coach.stats.stat_filters import StatsFilter
from arena_coach.stats.stat_models import AggregateSlice, LoadedMatch, MatchParticipant, MatchQuality, MatchupSummary, PlaystyleResult
from arena_coach.stats.teammate_stats import calculate_teammates, teammate_to_dict
from arena_coach.stats.trend_stats import calculate_trends, trend_to_dict


SUMMARY_STATS = (
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
    "possession_time",
)


class StatsEngine:
    def __init__(self, matches: Iterable[LoadedMatch], active_profile: Optional[Dict[str, object]] = None) -> None:
        self.matches = list(matches)
        self.active_profile = active_profile or None
        for match in self.matches:
            if match.quality is None:
                match.quality = classify_match_quality(match)

    def quality_summary(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        filtered = self._apply_match_filters(self.matches, filters or StatsFilter(finalized_only=False))
        counts = {"Competitive Eligible": 0, "Low Quality": 0, "Unreviewed": 0, "AFK Affected": 0}
        rows = []
        for match in filtered:
            counts[match.quality.quality_label] = counts.get(match.quality.quality_label, 0) + 1
            rows.append(
                {
                    "match_id": match.id,
                    "display_name": match.display_name,
                    "quality_label": match.quality.quality_label,
                    "competitive_eligible": match.quality.competitive_eligible,
                    "quality_reasons": list(match.quality.quality_reasons),
                    "blue_score": match.blue_score,
                    "orange_score": match.orange_score,
                    "blue_round_wins": match.blue_round_wins,
                    "orange_round_wins": match.orange_round_wins,
                    "total_rounds_played": match.total_rounds_played,
                    "points_carry_over": match.points_carry_over,
                    "private_match_type": match.private_match_type,
                    "round_warning": match.metadata.get("round_context", {}).get("warning"),
                    **quality_to_dict(match.quality),
                }
            )
        return {"counts": counts, "matches": rows}

    def quality_for_match(self, match_id: int) -> Dict[str, object]:
        match = next((item for item in self.matches if item.id == match_id), None)
        if match is None:
            raise ValueError(f"Match id {match_id} does not exist.")
        return {
            **quality_to_dict(match.quality),
            "blue_score": match.blue_score,
            "orange_score": match.orange_score,
            "blue_round_wins": match.blue_round_wins,
            "orange_round_wins": match.orange_round_wins,
            "total_rounds_played": match.total_rounds_played,
            "points_carry_over": match.points_carry_over,
            "private_match_type": match.private_match_type,
            "round_warning": match.metadata.get("round_context", {}).get("warning"),
        }

    def profile_summary(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        base = filters or StatsFilter()
        all_rows = self._self_rows(
            self._apply_match_filters(self.matches, base),
            include_afk=base.include_afk_players,
        )
        competitive_rows = self._self_rows(
            self._apply_match_filters(
                self.matches,
                base.with_updates(competitive_only=True, include_low_quality=False),
            ),
            include_afk=base.include_afk_players,
        )
        public_rows = self._self_rows(
            self._apply_match_filters(self.matches, _classification_only(base, "public")),
            include_afk=base.include_afk_players,
        )
        private_rows = self._self_rows(
            self._apply_match_filters(self.matches, _classification_only(base, "private")),
            include_afk=base.include_afk_players,
        )
        tournament_rows = self._self_rows(
            self._apply_match_filters(self.matches, _classification_only(base, "tournament")),
            include_afk=base.include_afk_players,
        )

        all_slice = self._aggregate_slice(all_rows)
        competitive_slice = self._aggregate_slice(competitive_rows)
        summary = {
            "active_profile": self.active_profile,
            "matches_played": all_slice.matches_played,
            "competitive_eligible_matches": competitive_slice.matches_played,
            "excluded_low_quality_count": self._excluded_low_quality_count(base),
            "wins": all_slice.wins,
            "losses": all_slice.losses,
            "ties": all_slice.ties,
            "win_rate": all_slice.win_rate,
            "totals": all_slice.totals,
            "averages": all_slice.averages,
            "shot_efficiency": all_slice.shot_efficiency,
            "breakdowns": {
                "all_finalized": self._slice_to_dict(all_slice),
                "competitive_only": self._slice_to_dict(competitive_slice),
                "public_only": self._slice_to_dict(self._aggregate_slice(public_rows)),
                "private_only": self._slice_to_dict(self._aggregate_slice(private_rows)),
                "tournament_only": self._slice_to_dict(self._aggregate_slice(tournament_rows)),
                "last_5": self._slice_to_dict(self._aggregate_slice(all_rows[:5])),
                "last_10": self._slice_to_dict(self._aggregate_slice(all_rows[:10])),
                "last_25": self._slice_to_dict(self._aggregate_slice(all_rows[:25])),
            },
        }
        playstyle = self.playstyle(base.with_updates(competitive_only=True, include_low_quality=False))
        summary["playstyle"] = playstyle
        summary["low_sample_warning"] = (
            "Competitive sample is still small. Treat rivals, teammates, and playstyle as early signals."
            if competitive_slice.matches_played < 5
            else None
        )
        return summary

    def trends(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        base = filters or StatsFilter()
        rows = self._self_rows(
            self._apply_match_filters(self.matches, base),
            include_afk=base.include_afk_players,
        )
        metrics = calculate_trends(rows)
        return {"match_count": len(rows), "metrics": [trend_to_dict(metric) for metric in metrics]}

    def matchups(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        base = filters or StatsFilter()
        filtered = self._apply_match_filters(self.matches, base.with_updates(include_low_quality=False))
        rows = calculate_matchups(
            filtered,
            include_guests=base.include_guest_players,
            include_afk=base.include_afk_players,
        )
        top_rivals = self._rival_candidates(rows)
        return {
            "rows": [matchup_to_dict(row) for row in rows],
            "top_rivals": [matchup_to_dict(row) for row in top_rivals],
        }

    def teammates(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        base = filters or StatsFilter()
        filtered = self._apply_match_filters(self.matches, base.with_updates(include_low_quality=False))
        rows = calculate_teammates(
            filtered,
            include_guests=base.include_guest_players,
            include_afk=base.include_afk_players,
        )
        best = self._best_teammates(rows)
        return {
            "rows": [teammate_to_dict(row) for row in rows],
            "best_teammates": [teammate_to_dict(row) for row in best],
        }

    def playstyle(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        base = filters or StatsFilter()
        rows = self._self_rows(
            self._apply_match_filters(self.matches, base),
            include_afk=base.include_afk_players,
        )
        result = _classify_playstyle(rows)
        return asdict(result)

    def player_summary(self, player_id: int, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        base = filters or StatsFilter()
        filtered = self._apply_match_filters(self.matches, base)
        rows = []
        player_name = None
        with_user = 0
        against_user = 0
        afk_matches = 0
        for match in filtered:
            self_rows = [row for row in match.self_participants() if base.include_afk_players or not row.afk_suspected]
            for participant in match.participants:
                if participant.player_id != player_id:
                    continue
                if participant.afk_suspected and not base.include_afk_players:
                    continue
                if not participant.meaningful_participation:
                    continue
                player_name = participant.canonical_name or participant.match_alias
                rows.append(participant)
                if participant.afk_suspected:
                    afk_matches += 1
                if self_rows:
                    if any((self_row.team or "").casefold() == (participant.team or "").casefold() for self_row in self_rows):
                        with_user += 1
                    else:
                        against_user += 1
        aggregate = self._aggregate_slice(rows)
        return {
            "player_id": player_id,
            "display_name": player_name or f"Player {player_id}",
            "matches": aggregate.matches_played,
            "wins": aggregate.wins,
            "losses": aggregate.losses,
            "ties": aggregate.ties,
            "win_rate": aggregate.win_rate,
            "totals": aggregate.totals,
            "averages": aggregate.averages,
            "shot_efficiency": aggregate.shot_efficiency,
            "with_user_matches": with_user,
            "against_user_matches": against_user,
            "afk_matches": afk_matches,
        }

    def preview(self, filters: Optional[StatsFilter] = None) -> Dict[str, object]:
        summary = self.profile_summary(filters)
        quality = self.quality_summary(filters)
        trends = self.trends(filters)
        matchups = self.matchups(filters)
        teammates = self.teammates(filters)
        playstyle = self.playstyle(filters)
        return {
            "summary": summary,
            "quality": quality,
            "trends": trends,
            "matchups": matchups,
            "teammates": teammates,
            "playstyle": playstyle,
        }

    def _apply_match_filters(self, matches: Iterable[LoadedMatch], filters: StatsFilter) -> List[LoadedMatch]:
        filtered = []
        for match in matches:
            if filters.finalized_only and not match.finalized:
                continue
            if not filters.allows_classification(match.match_classification):
                continue
            if not filters.allows_private_match_type(match.private_match_type, match.match_classification):
                continue
            if not filters.include_low_quality and match.quality.is_low_quality:
                continue
            if filters.competitive_only and not match.quality.competitive_eligible:
                continue
            match_date = _match_date(match)
            if filters.from_date and match_date and match_date < filters.from_date:
                continue
            if filters.to_date and match_date and match_date > filters.to_date:
                continue
            filtered.append(match)
        filtered.sort(key=lambda item: (item.started_at or item.created_at or "", item.id), reverse=True)
        if filters.last_n is not None:
            filtered = filtered[: filters.last_n]
        return filtered

    def _self_rows(self, matches: Iterable[LoadedMatch], include_afk: bool = False) -> List[MatchParticipant]:
        rows = []
        for match in matches:
            participants = [participant for participant in match.self_participants() if include_afk or not participant.afk_suspected]
            participants = [participant for participant in participants if participant.meaningful_participation or participant.activity_total > 0 or participant.stats]
            if not participants:
                continue
            totals = {stat_name: 0.0 for stat_name in SUMMARY_STATS}
            for participant in participants:
                for stat_name in SUMMARY_STATS:
                    totals[stat_name] += float(participant.stats.get(stat_name) or 0)
            row = MatchParticipant(
                match_id=match.id,
                match_alias=participants[0].match_alias,
                canonical_name=participants[0].canonical_name,
                player_id=participants[0].player_id,
                userid=participants[0].userid,
                team=match.user_team or participants[0].team,
                is_user=True,
                confirmed=True,
                participant_key=participants[0].participant_key,
                team_row_key=f"self:{match.id}",
                stats={key: round(value, 2) for key, value in totals.items()},
                metadata={"team_rows": [participant.team for participant in participants]},
                afk_suspected=False,
                live_samples=sum(int(participant.live_samples or 0) for participant in participants),
                activity_total=sum(float(participant.activity_total or 0.0) for participant in participants),
                meaningful_participation=True,
            )
            row.stats["_result"] = match.result or "unknown"
            row.stats["_match_id"] = match.id
            rows.append(row)
        return rows

    def _aggregate_slice(self, rows: List[MatchParticipant]) -> AggregateSlice:
        totals = {stat_name: 0.0 for stat_name in SUMMARY_STATS}
        result_by_match: Dict[int, str] = {}
        match_ids: List[int] = []
        for row in rows:
            match_id = int(row.stats.get("_match_id") or row.match_id)
            match_ids.append(match_id)
            result_by_match.setdefault(match_id, str(row.stats.get("_result") or "unknown"))
            for stat_name in SUMMARY_STATS:
                totals[stat_name] += float(row.stats.get(stat_name) or 0)
        unique_match_ids = list(dict.fromkeys(match_ids))
        wins = len([value for value in result_by_match.values() if value == "win"])
        losses = len([value for value in result_by_match.values() if value == "loss"])
        ties = len([value for value in result_by_match.values() if value == "tie"])
        matches_played = len(unique_match_ids)
        averages = {stat_name: round((value / matches_played), 2) if matches_played else 0.0 for stat_name, value in totals.items()}
        shot_efficiency = round((totals["goals"] / totals["shots"]), 3) if totals["shots"] > 0 else 0.0
        win_rate = round((wins / matches_played) * 100, 2) if matches_played else 0.0
        return AggregateSlice(
            matches_played=matches_played,
            wins=wins,
            losses=losses,
            ties=ties,
            win_rate=win_rate,
            totals={key: round(value, 2) for key, value in totals.items()},
            averages=averages,
            shot_efficiency=shot_efficiency,
            match_ids=unique_match_ids,
        )

    @staticmethod
    def _slice_to_dict(slice_model: AggregateSlice) -> Dict[str, object]:
        return {
            "matches_played": slice_model.matches_played,
            "wins": slice_model.wins,
            "losses": slice_model.losses,
            "ties": slice_model.ties,
            "win_rate": slice_model.win_rate,
            "totals": slice_model.totals,
            "averages": slice_model.averages,
            "shot_efficiency": slice_model.shot_efficiency,
            "match_ids": slice_model.match_ids,
        }

    def _excluded_low_quality_count(self, filters: StatsFilter) -> int:
        filtered = self._apply_match_filters(self.matches, filters.with_updates(include_low_quality=True))
        return len([match for match in filtered if match.quality.is_low_quality])

    @staticmethod
    def _rival_candidates(rows: List[MatchupSummary]) -> List[MatchupSummary]:
        candidates = []
        for row in rows:
            opponent_points = row.opponent_totals.get("points", 0.0) / row.matches_against if row.matches_against else 0.0
            if row.matches_against >= 3 and row.win_rate_against < 50.0 and opponent_points >= 2.0:
                candidates.append(row)
        return sorted(candidates, key=lambda item: (item.win_rate_against, -item.matches_against, item.display_name.casefold()))[:5]

    @staticmethod
    def _best_teammates(rows: List[object]) -> List[object]:
        candidates = []
        for row in rows:
            if row.matches_together >= 3 and row.win_rate_together >= 50.0:
                candidates.append(row)
        return sorted(candidates, key=lambda item: (-item.win_rate_together, -item.matches_together, item.display_name.casefold()))[:5]


def _classification_only(filters: StatsFilter, mode: str) -> StatsFilter:
    normalized = mode.casefold()
    return filters.with_updates(
        include_public=normalized == "public",
        include_private=normalized == "private",
        include_tournament=normalized == "tournament",
        include_unknown=False,
    )


def _match_date(match: LoadedMatch) -> Optional[date]:
    text = match.started_at or match.created_at
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _classify_playstyle(rows: List[MatchParticipant]) -> PlaystyleResult:
    if len(rows) < 5:
        return PlaystyleResult(
            label="Low Sample",
            explanation="Fewer than 5 competitive matches are available, so the playstyle guess is still early.",
            sample_size=len(rows),
            weights={},
        )

    averages = StatsEngine([])._aggregate_slice(rows).averages
    weights = {
        "Scorer": averages["goals"] * 3.0 + averages["points"] * 0.6 + averages["shots"] * 0.5,
        "Playmaker": averages["assists"] * 2.4 + averages["passes"] * 0.7 + averages["catches"] * 0.6,
        "Defender": averages["saves"] * 2.8 + averages["blocks"] * 1.7 + averages["interceptions"] * 1.6,
        "Disruptor": averages["stuns"] * 1.2 + averages["steals"] * 2.0,
        "Possession Player": averages["possession_time"] * 0.08 + averages["passes"] * 0.6 + averages["catches"] * 0.6,
        "High-Volume Shooter": averages["shots"] * 1.2 + averages["goals"] * 0.6,
        "Support/Utility": averages["assists"] * 1.4 + averages["saves"] * 1.4 + averages["stuns"] * 0.8,
    }
    ordered = sorted(weights.items(), key=lambda item: item[1], reverse=True)
    top_label, top_value = ordered[0]
    second_value = ordered[1][1] if len(ordered) > 1 else 0.0

    if top_value < 1.0:
        label = "Balanced"
        explanation = "No category stands out strongly yet."
    elif top_label == "High-Volume Shooter" and averages["shots"] >= 5 and averages["goals"] < averages["shots"] * 0.35:
        label = "High-Volume Shooter"
        explanation = f"Shot volume is high at {averages['shots']:.2f} per match."
    elif abs(top_value - second_value) <= 0.4:
        label = "Balanced"
        explanation = "Multiple stat categories are close together, so no single role dominates."
    else:
        label = top_label
        explanation = _playstyle_explanation(label, averages)

    return PlaystyleResult(label=label, explanation=explanation, sample_size=len(rows), weights={key: round(value, 2) for key, value in weights.items()})


def _playstyle_explanation(label: str, averages: Dict[str, float]) -> str:
    if label == "Scorer":
        return f"Goals ({averages['goals']:.2f}) and points ({averages['points']:.2f}) lead the profile."
    if label == "Playmaker":
        return f"Assists ({averages['assists']:.2f}), passes ({averages['passes']:.2f}), and catches ({averages['catches']:.2f}) stand out."
    if label == "Defender":
        return f"Saves ({averages['saves']:.2f}), blocks ({averages['blocks']:.2f}), and interceptions ({averages['interceptions']:.2f}) are strongest."
    if label == "Disruptor":
        return f"Stuns ({averages['stuns']:.2f}) and steals ({averages['steals']:.2f}) drive the profile."
    if label == "Possession Player":
        return f"Possession time ({averages['possession_time']:.2f}) plus steady passing and catching define the profile."
    if label == "Support/Utility":
        return "Assists, saves, and disruption all contribute without one scoring-heavy category dominating."
    return "The current sample leans toward this role."
