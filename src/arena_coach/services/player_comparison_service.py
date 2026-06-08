"""Player-vs-player comparison helpers for the GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from arena_coach.services.advanced_analysis_service import AdvancedAnalysisService
from arena_coach.services.player_service import PlayerService
from arena_coach.services.stats_service import DatabaseStatsService
from arena_coach.stats.stat_filters import StatsFilter
from arena_coach.stats.stat_models import LoadedMatch, MatchParticipant


class PlayerComparisonService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.player_service = PlayerService(self.database_path)
        self.stats_service = DatabaseStatsService(self.database_path)
        self.advanced_service = AdvancedAnalysisService(self.database_path)

    def list_players(self, search: str = "") -> List[Dict[str, Any]]:
        return self.player_service.list_players(search)

    def compare(
        self,
        left_player_id: int,
        right_player_id: int,
        filters: Optional[StatsFilter] = None,
    ) -> Dict[str, Any]:
        if int(left_player_id) == int(right_player_id):
            raise ValueError("Select two different players to compare.")

        left_player = self.player_service.get_player(int(left_player_id))
        right_player = self.player_service.get_player(int(right_player_id))
        if left_player is None:
            raise ValueError(f"Player id {left_player_id} does not exist.")
        if right_player is None:
            raise ValueError(f"Player id {right_player_id} does not exist.")

        active_filters = filters or StatsFilter()
        left_stats = self.stats_service.player(int(left_player_id), active_filters)
        right_stats = self.stats_service.player(int(right_player_id), active_filters)
        left_advanced = self.advanced_service.player_metric_summary(int(left_player_id), active_filters)
        right_advanced = self.advanced_service.player_metric_summary(int(right_player_id), active_filters)
        shared = self._shared_summary(int(left_player_id), int(right_player_id), active_filters)

        return {
            "filters": _filters_dict(active_filters),
            "left_player": left_player,
            "right_player": right_player,
            "left_stats": left_stats,
            "right_stats": right_stats,
            "left_advanced": left_advanced,
            "right_advanced": right_advanced,
            "shared": shared,
        }

    def _shared_summary(self, left_player_id: int, right_player_id: int, filters: StatsFilter) -> Dict[str, Any]:
        engine = self.stats_service._engine()
        matches = engine._apply_match_filters(engine.matches, filters)
        together_wins = together_losses = together_ties = 0
        against_wins = against_losses = against_ties = 0
        shared_count = together_count = opposed_count = mixed_count = 0
        recent_rows: list[dict[str, Any]] = []

        for match in matches:
            left_rows = _eligible_rows(match, left_player_id, filters.include_afk_players)
            right_rows = _eligible_rows(match, right_player_id, filters.include_afk_players)
            if not left_rows or not right_rows:
                continue

            shared_count += 1
            left_teams = {str(row.team or "").casefold() for row in left_rows if (row.team or "").casefold() in {"blue", "orange"}}
            right_teams = {str(row.team or "").casefold() for row in right_rows if (row.team or "").casefold() in {"blue", "orange"}}
            same_team = bool(left_teams & right_teams)
            opposed = bool(
                any(left_team != right_team for left_team in left_teams for right_team in right_teams)
            )

            context = "mixed"
            result_for_left = "unknown"
            if same_team and opposed:
                mixed_count += 1
            elif same_team:
                together_count += 1
                context = "together"
                primary_team = sorted(left_teams & right_teams)[0] if (left_teams & right_teams) else sorted(left_teams or right_teams)[0]
                result_for_left = _team_result(match, primary_team)
                if result_for_left == "win":
                    together_wins += 1
                elif result_for_left == "loss":
                    together_losses += 1
                elif result_for_left == "tie":
                    together_ties += 1
            elif opposed:
                opposed_count += 1
                context = "opposed"
                primary_team = sorted(left_teams)[0] if left_teams else "unknown"
                result_for_left = _team_result(match, primary_team)
                if result_for_left == "win":
                    against_wins += 1
                elif result_for_left == "loss":
                    against_losses += 1
                elif result_for_left == "tie":
                    against_ties += 1
            else:
                mixed_count += 1

            recent_rows.append(
                {
                    "match_id": match.id,
                    "display_name": match.display_name,
                    "context": context,
                    "left_teams": sorted(left_teams),
                    "right_teams": sorted(right_teams),
                    "result_for_left": result_for_left,
                    "match_classification": match.match_classification,
                    "private_match_type": match.private_match_type,
                    "score": f"Blue {match.blue_score if match.blue_score is not None else '?'} - Orange {match.orange_score if match.orange_score is not None else '?'}",
                }
            )

        recent_rows.sort(key=lambda row: row["match_id"], reverse=True)
        return {
            "shared_matches": shared_count,
            "together_matches": together_count,
            "opposed_matches": opposed_count,
            "mixed_team_matches": mixed_count,
            "together_record": {
                "wins": together_wins,
                "losses": together_losses,
                "ties": together_ties,
            },
            "left_vs_right_record": {
                "wins": against_wins,
                "losses": against_losses,
                "ties": against_ties,
            },
            "recent_shared_matches": recent_rows[:10],
        }


def _eligible_rows(match: LoadedMatch, player_id: int, include_afk: bool) -> List[MatchParticipant]:
    rows: list[MatchParticipant] = []
    for participant in match.participants:
        if participant.player_id != int(player_id):
            continue
        if (participant.team or "").casefold() not in {"blue", "orange"}:
            continue
        if participant.afk_suspected and not include_afk:
            continue
        if not participant.meaningful_participation and participant.activity_total <= 0 and not participant.stats:
            continue
        rows.append(participant)
    return rows


def _team_result(match: LoadedMatch, team: str) -> str:
    blue_score = match.blue_score
    orange_score = match.orange_score
    if blue_score is None or orange_score is None:
        return "unknown"
    if int(blue_score) == int(orange_score):
        return "tie"
    winner = "blue" if int(blue_score) > int(orange_score) else "orange"
    return "win" if str(team).casefold() == winner else "loss"


def _filters_dict(filters: StatsFilter) -> Dict[str, Any]:
    return {
        "competitive_only": filters.competitive_only,
        "include_public": filters.include_public,
        "include_private": filters.include_private,
        "include_tournament": filters.include_tournament,
        "include_unknown": filters.include_unknown,
        "include_afk_players": filters.include_afk_players,
        "include_guest_players": filters.include_guest_players,
        "private_match_type": filters.private_match_type,
        "private_match_types": list(filters.selected_private_match_types()),
        "last_n": filters.last_n,
    }
