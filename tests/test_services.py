from pathlib import Path
import json
import tempfile
import unittest

from arena_coach.database import connect_database, initialize_database
from arena_coach.inference.spatial_models import AdvancedEvent
from arena_coach.log_importer import import_raw_log
from arena_coach.repositories import advanced_events_repo, advanced_player_metrics_repo, matches_repo, players_repo
from arena_coach.services.advanced_analysis_service import AdvancedAnalysisService
from arena_coach.services.import_service import ImportService
from arena_coach.services.match_service import MatchService
from arena_coach.services.player_comparison_service import PlayerComparisonService
from arena_coach.services.player_service import PlayerService
from arena_coach.services.profile_service import ProfileService
from arena_coach.services.settings_service import SettingsService
from arena_coach.services.stats_preview_service import StatsPreviewService
from arena_coach.stats.stat_filters import StatsFilter
from arena_coach.config import load_config


FIXTURES = Path(__file__).parent / "fixtures"


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "arena_coach.db"
        self.raw_log_dir = self.root / "raw"
        self.raw_log_dir.mkdir()
        initialize_database(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_profile_player_match_and_finalize_services(self):
        profile_service = ProfileService(self.database_path)
        player_service = PlayerService(self.database_path)
        match_service = MatchService(self.database_path)

        profile_id = profile_service.create_profile("Alice Coach", "Alice")
        profile_service.set_active_profile(profile_id)
        self.assertEqual(profile_service.get_active_profile()["primary_echo_name"], "Alice")

        result = import_raw_log(FIXTURES / "simple_match.jsonl", self.database_path)
        player_id = player_service.create_player("Alice Canonical")
        player_service.add_alias(player_id, "Alice", userid="1", playerid="0")

        review = match_service.get_review_data(result.match_id)
        self.assertEqual(len(review["players"]), 2)
        self.assertFalse(review["validation"]["can_finalize"])

        match_service.map_player(result.match_id, "Alice", player_id)
        match_service.confirm_guest(result.match_id, "Bob")
        match_service.mark_self(result.match_id, "Alice")
        finalize = match_service.finalize_match(result.match_id)
        self.assertEqual(finalize["result"], "win")

        detail = match_service.get_match_detail(result.match_id)
        self.assertTrue(detail["match"]["finalized"])
        self.assertEqual(detail["players"][0]["match_alias"], "Alice")

    def test_import_service_parse_and_duplicate_lookup(self):
        import_service = ImportService(self.database_path, self.raw_log_dir)
        match_service = MatchService(self.database_path)

        preview = import_service.parse_log(FIXTURES / "simple_match.jsonl")
        self.assertEqual(preview["valid_snapshots"], 3)
        self.assertEqual(preview["blue_score"], 3)

        result = import_service.import_log(FIXTURES / "simple_match.jsonl")
        imported = match_service.raw_log_imported(FIXTURES / "simple_match.jsonl")
        self.assertEqual(imported["id"], result["match_id"])

    def test_stats_preview_and_settings_validation(self):
        config_path = self.root / "config.json"
        config_path.write_text(
            '{"database_path":"%s","raw_log_dir":"%s"}'
            % (str(self.database_path).replace("\\", "\\\\"), str(self.raw_log_dir).replace("\\", "\\\\")),
            encoding="utf-8",
        )
        config = load_config(config_path)
        settings = SettingsService(config)
        values = settings.current_values()
        values["echo_api_port"] = 6721
        saved = settings.save(values)
        self.assertEqual(saved.echo_api_port, 6721)

        stats = StatsPreviewService(self.database_path).summary()
        self.assertEqual(stats["total_finalized_matches"], 0)

    def test_private_match_type_service_updates_display_name(self):
        result = import_raw_log(FIXTURES / "simple_match.jsonl", self.database_path)
        service = MatchService(self.database_path)
        connection = connect_database(self.database_path)
        try:
            with connection:
                connection.execute(
                    "UPDATE matches SET match_classification = 'Private', display_name = NULL WHERE id = ?",
                    (result.match_id,),
                )
        finally:
            connection.close()

        service.set_private_match_type(result.match_id, "PUG")
        detail = service.get_match_detail(result.match_id)

        self.assertEqual(detail["match"]["private_match_type"], "PUG")
        self.assertIn("Private PUG", detail["match"]["display_name"])

    def test_match_detail_scoreboards_include_advanced_stats_and_round_details(self):
        result = import_raw_log(FIXTURES / "simple_match.jsonl", self.database_path)
        service = MatchService(self.database_path)
        connection = connect_database(self.database_path)
        try:
            with connection:
                connection.execute(
                    "UPDATE match_players SET team = 'orange' WHERE match_id = ? AND match_alias = 'Bob'",
                    (result.match_id,),
                )
                connection.execute(
                    "UPDATE match_player_stats SET team = 'orange' WHERE match_id = ? AND match_alias = 'Bob'",
                    (result.match_id,),
                )
                matches_repo.update_match_context(
                    connection,
                    result.match_id,
                    round_summary=[
                        {"round": 1, "blue_points": 14, "orange_points": 10, "winner": "blue", "confidence": "derived"},
                        {"round": 2, "blue_points": 15, "orange_points": 12, "winner": "blue", "confidence": "derived"},
                    ],
                    blue_round_wins=2,
                    orange_round_wins=0,
                    total_rounds_played=2,
                )
                advanced_events_repo.add_advanced_event(
                    connection,
                    result.match_id,
                    AdvancedEvent(event_type="clear", actor_alias="Alice", team="blue"),
                )
                advanced_events_repo.add_advanced_event(
                    connection,
                    result.match_id,
                    AdvancedEvent(event_type="missed_shot", actor_alias="Alice", team="blue"),
                )
                advanced_events_repo.add_advanced_event(
                    connection,
                    result.match_id,
                    AdvancedEvent(event_type="shot_saved", actor_alias="Alice", team="blue"),
                )
                advanced_events_repo.add_advanced_event(
                    connection,
                    result.match_id,
                    AdvancedEvent(event_type="turnover", actor_alias="Alice", target_alias="Bob", team="blue"),
                )
                advanced_events_repo.add_advanced_event(
                    connection,
                    result.match_id,
                    AdvancedEvent(event_type="offensive_transition_time", actor_alias="Alice", team="blue", value=2.0),
                )
                advanced_events_repo.add_advanced_event(
                    connection,
                    result.match_id,
                    AdvancedEvent(event_type="defensive_transition_time", actor_alias="Alice", team="blue", value=3.0),
                )
        finally:
            connection.close()

        detail = service.get_match_detail(result.match_id)
        alice = detail["scoreboards"]["blue"][0]
        self.assertEqual(alice["advanced_stats"]["clears"], 1)
        self.assertEqual(alice["advanced_stats"]["missed_shots"], 1)
        self.assertEqual(alice["advanced_stats"]["shots_saved"], 1)
        self.assertEqual(alice["advanced_stats"]["turnovers"], 1)
        self.assertEqual(alice["advanced_stats"]["avg_time_to_offense"], 2.0)
        self.assertEqual(alice["advanced_stats"]["avg_time_to_defense"], 3.0)
        self.assertEqual(alice["advanced_stats"]["shooting_percentage"], round((alice["goals"] / 2.0) * 100.0, 1))
        bob = detail["scoreboards"]["orange"][0]
        self.assertEqual(bob["advanced_stats"]["interceptions"], 1)
        self.assertEqual(detail["scoreboards"]["header_totals"]["blue"], 2)
        self.assertEqual(detail["scoreboards"]["header_totals"]["orange"], 0)
        self.assertIn("R1 14", detail["scoreboards"]["round_details"]["blue"])
        self.assertIn("R2 15", detail["scoreboards"]["round_details"]["blue"])

    def test_match_level_advanced_player_breakdown_separates_turnovers_and_interceptions(self):
        result = import_raw_log(FIXTURES / "simple_match.jsonl", self.database_path)
        connection = connect_database(self.database_path)
        try:
            with connection:
                connection.execute(
                    "UPDATE match_players SET team = 'orange' WHERE match_id = ? AND match_alias = 'Bob'",
                    (result.match_id,),
                )
                connection.execute(
                    "UPDATE match_player_stats SET team = 'orange' WHERE match_id = ? AND match_alias = 'Bob'",
                    (result.match_id,),
                )
                advanced_events_repo.add_advanced_event(
                    connection,
                    result.match_id,
                    AdvancedEvent(event_type="turnover", actor_alias="Alice", target_alias="Bob", team="blue"),
                )
        finally:
            connection.close()

        service = AdvancedAnalysisService(self.database_path)
        payload = service.summary(result.match_id, confidence_levels=["high", "medium", "low"], include_low_confidence=True)
        breakdown = {row["alias"]: row["counts"] for row in payload["player_breakdown"]}

        self.assertEqual(breakdown["Alice"]["turnover"], 1)
        self.assertEqual(breakdown["Bob"]["interception"], 1)

    def test_match_detail_exposes_persisted_advanced_player_metrics(self):
        result = import_raw_log(FIXTURES / "simple_match.jsonl", self.database_path)
        service = MatchService(self.database_path)
        connection = connect_database(self.database_path)
        try:
            with connection:
                advanced_player_metrics_repo.add_metric_rows(
                    connection,
                    [
                        {
                            "match_id": result.match_id,
                            "match_alias": "Alice",
                            "team": "blue",
                            "completed_passes": 3,
                            "open_for_pass_samples": 2,
                            "lane_blocked_samples": 1,
                            "goals_2_guarded": 1,
                            "metadata": {
                                "average_time_to_offense": 1.75,
                                "average_time_to_defense": 2.5,
                                "shooting_percentage": 50.0,
                                "open_for_pass_rate": 0.667,
                            },
                        }
                    ],
                )
        finally:
            connection.close()

        detail = service.get_match_detail(result.match_id)
        self.assertEqual(len(detail["advanced_player_metrics"]), 1)
        alice = detail["scoreboards"]["blue"][0]
        self.assertEqual(alice["advanced_stats"]["completed_passes"], 3)
        self.assertEqual(alice["advanced_stats"]["open_for_pass_samples"], 2)
        self.assertEqual(alice["advanced_stats"]["goals_2_guarded"], 1)
        self.assertEqual(alice["advanced_stats"]["avg_time_to_offense"], 1.75)
        self.assertEqual(alice["advanced_stats"]["shooting_percentage"], 50.0)

    def test_advanced_summary_service_uses_toggleable_confidence_levels(self):
        profile_service = ProfileService(self.database_path)
        profile_id = profile_service.create_profile("Alice Coach", "Alice")
        profile_service.set_active_profile(profile_id)

        connection = connect_database(self.database_path)
        try:
            with connection:
                player_id = players_repo.create_player(connection, "Alice Canonical")
                match_id = matches_repo.create_match(
                    connection,
                    user_profile_id=profile_id,
                    display_name="Advanced Match",
                    started_at="2026-05-31T20:00:00+00:00",
                    finalized=True,
                    match_classification="Public",
                    total_rounds_played=2,
                    blue_score=10,
                    orange_score=8,
                    result="win",
                )
                matches_repo.add_match_player(
                    connection,
                    match_id=match_id,
                    match_alias="Alice",
                    player_id=player_id,
                    userid="u-alice",
                    team="blue",
                    is_user=True,
                    confirmed=True,
                )
                advanced_events_repo.add_advanced_event(
                    connection,
                    match_id,
                    AdvancedEvent(
                        event_type="turnover",
                        actor_player_id=player_id,
                        actor_alias="Alice",
                        confidence="high",
                        value=1.0,
                    ),
                )
                advanced_events_repo.add_advanced_event(
                    connection,
                    match_id,
                    AdvancedEvent(
                        event_type="intercepted_pass",
                        actor_alias="EnemyPasser",
                        actor_player_id=999,
                        target_alias="Alice",
                        target_player_id=player_id,
                        confidence="high",
                        start_sequence=12,
                        end_sequence=13,
                    ),
                )
                advanced_events_repo.add_advanced_event(
                    connection,
                    match_id,
                    AdvancedEvent(
                        event_type="offensive_transition_time",
                        actor_player_id=player_id,
                        actor_alias="Alice",
                        confidence="medium",
                        value=2.5,
                    ),
                )
                advanced_events_repo.add_advanced_event(
                    connection,
                    match_id,
                    AdvancedEvent(
                        event_type="defensive_transition_time",
                        actor_player_id=player_id,
                        actor_alias="Alice",
                        confidence="low",
                        value=4.0,
                    ),
                )
                advanced_events_repo.add_advanced_event(
                    connection,
                    match_id,
                    AdvancedEvent(
                        event_type="turnover",
                        actor_player_id=player_id,
                        actor_alias="Alice",
                        confidence="low",
                        value=1.0,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO advanced_player_metrics (
                        match_id, player_id, match_alias, userid, team,
                        completed_passes, inferred_catches, initiators,
                        open_for_pass_samples, lane_blocked_samples, lane_blocks,
                        tight_man_coverage_samples, loose_man_coverage_samples, no_man_coverage_samples, goalie_coverage_samples,
                        clear_attempts, successful_clears, failed_clears,
                        inferred_turnovers, inferred_interceptions, steal_takeaways, stun_takeaways,
                        missed_shots, shots_saved_against, blocked_shots, stuffed_shots,
                        offensive_transition_count, offensive_transition_total,
                        defensive_transition_count, defensive_transition_total,
                        goals_2_open_net, goals_2_guarded, goals_3_open_net, goals_3_guarded,
                        metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        match_id,
                        player_id,
                        "Alice",
                        "u-alice",
                        "blue",
                        4,
                        3,
                        1,
                        5,
                        1,
                        2,
                        3,
                        4,
                        1,
                        2,
                        3,
                        2,
                        1,
                        2,
                        5,
                        1,
                        1,
                        2,
                        1,
                        0,
                        0,
                        1,
                        2.5,
                        1,
                        4.0,
                        1,
                        2,
                        1,
                        1,
                        json.dumps(
                            {
                                "active_rounds_estimated": 1.5,
                                "dunk_like_open_2s": 1,
                                "dunk_like_guarded_2s": 0,
                            },
                            sort_keys=True,
                        ),
                    ),
                )
        finally:
            connection.close()

        service = AdvancedAnalysisService(self.database_path)
        medium_high = service.local_user_summary(confidence_levels=["high", "medium"])
        self.assertEqual(medium_high["event_counts"]["turnover"], 1)
        self.assertEqual(medium_high["total_rounds_considered"], 1.5)
        self.assertEqual(medium_high["metric_rounds_considered"], 1.5)
        self.assertEqual(medium_high["event_averages_per_round"]["turnover"], 0.667)
        self.assertEqual(medium_high["display_event_totals"]["interception"], 1)
        self.assertEqual(medium_high["transitions"]["average_time_to_offense"], 2.5)
        self.assertIsNone(medium_high["transitions"]["average_time_to_defense"])
        shooting = medium_high["category_breakdown"]["shooting"]
        self.assertIsNotNone(shooting["overall_score"])
        self.assertGreater(float(shooting["overall_score"]), 0.0)
        shooting_metrics = {row["label"]: row["value"] for row in shooting["metrics"]}
        self.assertEqual(shooting_metrics["Guarded 2s"], "2")
        self.assertEqual(shooting_metrics["Open 3s"], "1")
        self.assertEqual(shooting_metrics["Possible dunk-like open 2s"], "1")
        self.assertEqual(shooting_metrics["Actual scoreboard points / round"], "8.00")
        self.assertEqual(shooting_metrics["Shot-type bonus / round"], "3.33")
        self.assertEqual(shooting_metrics["Miss/save penalty / round"], "1.33")
        self.assertEqual(shooting_metrics["Effective shooting points / round"], "10.00")
        self.assertIsNotNone(medium_high["category_breakdown"]["speed"]["overall_score"])
        self.assertIsNotNone(medium_high["category_breakdown"]["possession"]["overall_score"])
        self.assertIsNotNone(medium_high["category_breakdown"]["offense"]["overall_score"])
        self.assertIsNotNone(medium_high["category_breakdown"]["defense"]["overall_score"])
        self.assertIsNotNone(medium_high["category_breakdown"]["passing"]["overall_score"])

        player_summary = service.player_metric_summary(player_id)
        self.assertEqual(
            player_summary["category_breakdown"]["shooting"]["overall_score"],
            medium_high["category_breakdown"]["shooting"]["overall_score"],
        )
        self.assertEqual(
            player_summary["category_breakdown"]["speed"]["overall_score"],
            medium_high["category_breakdown"]["speed"]["overall_score"],
        )
        self.assertEqual(
            player_summary["category_breakdown"]["possession"]["overall_score"],
            medium_high["category_breakdown"]["possession"]["overall_score"],
        )
        self.assertEqual(
            player_summary["category_breakdown"]["offense"]["overall_score"],
            medium_high["category_breakdown"]["offense"]["overall_score"],
        )
        self.assertEqual(
            player_summary["category_breakdown"]["defense"]["overall_score"],
            medium_high["category_breakdown"]["defense"]["overall_score"],
        )
        self.assertEqual(
            player_summary["category_breakdown"]["passing"]["overall_score"],
            medium_high["category_breakdown"]["passing"]["overall_score"],
        )

        all_levels = service.local_user_summary(confidence_levels=["high", "medium", "low"])
        self.assertEqual(all_levels["event_counts"]["turnover"], 2)
        self.assertEqual(all_levels["event_averages_per_round"]["turnover"], 1.333)
        self.assertEqual(all_levels["transitions"]["average_time_to_defense"], 4.0)

    def test_player_comparison_service_compares_two_players_across_shared_matches(self):
        connection = connect_database(self.database_path)
        try:
            with connection:
                alpha_id = players_repo.create_player(connection, "Alpha")
                bravo_id = players_repo.create_player(connection, "Bravo")
                self_id = players_repo.create_player(connection, "Self")

                match_one_id = matches_repo.create_match(
                    connection,
                    display_name="Compare Match One",
                    started_at="2026-06-01T20:00:00+00:00",
                    finalized=True,
                    match_classification="Public",
                    blue_score=6,
                    orange_score=4,
                    user_team="blue",
                    result="win",
                    total_rounds_played=1,
                )
                match_two_id = matches_repo.create_match(
                    connection,
                    display_name="Compare Match Two",
                    started_at="2026-06-02T20:00:00+00:00",
                    finalized=True,
                    match_classification="Private",
                    private_match_type="PUG",
                    blue_score=4,
                    orange_score=6,
                    user_team="blue",
                    result="loss",
                    total_rounds_played=1,
                )

                for match_id, team_map in (
                    (match_one_id, {"Alpha": "blue", "Bravo": "blue", "Self": "blue"}),
                    (match_two_id, {"Alpha": "blue", "Bravo": "orange", "Self": "blue"}),
                ):
                    matches_repo.add_match_player(
                        connection,
                        match_id=match_id,
                        match_alias="Alpha",
                        player_id=alpha_id,
                        userid="u-alpha",
                        team=team_map["Alpha"],
                        confirmed=True,
                    )
                    matches_repo.add_match_player(
                        connection,
                        match_id=match_id,
                        match_alias="Bravo",
                        player_id=bravo_id,
                        userid="u-bravo",
                        team=team_map["Bravo"],
                        confirmed=True,
                    )
                    matches_repo.add_match_player(
                        connection,
                        match_id=match_id,
                        match_alias="Self",
                        player_id=self_id,
                        userid="u-self",
                        team=team_map["Self"],
                        is_user=True,
                        confirmed=True,
                    )

                matches_repo.add_match_player_stat(
                    connection,
                    match_id=match_one_id,
                    match_alias="Alpha",
                    player_id=alpha_id,
                    userid="u-alpha",
                    team="blue",
                    stats={
                        "points": 6,
                        "goals": 2,
                        "assists": 1,
                        "saves": 1,
                        "stuns": 3,
                        "steals": 1,
                        "shots_taken": 3,
                        "passes": 2,
                        "catches": 2,
                        "interceptions": 1,
                        "turnovers": 1,
                        "blocks": 0,
                        "possession_time": 12.0,
                    },
                )
                matches_repo.add_match_player_stat(
                    connection,
                    match_id=match_one_id,
                    match_alias="Bravo",
                    player_id=bravo_id,
                    userid="u-bravo",
                    team="blue",
                    stats={
                        "points": 3,
                        "goals": 1,
                        "assists": 2,
                        "saves": 0,
                        "stuns": 2,
                        "steals": 0,
                        "shots_taken": 2,
                        "passes": 4,
                        "catches": 3,
                        "interceptions": 0,
                        "turnovers": 1,
                        "blocks": 1,
                        "possession_time": 9.0,
                    },
                )
                matches_repo.add_match_player_stat(
                    connection,
                    match_id=match_one_id,
                    match_alias="Self",
                    player_id=self_id,
                    userid="u-self",
                    team="blue",
                    stats={"points": 2, "goals": 1, "shots_taken": 2, "passes": 1, "catches": 1},
                )

                matches_repo.add_match_player_stat(
                    connection,
                    match_id=match_two_id,
                    match_alias="Alpha",
                    player_id=alpha_id,
                    userid="u-alpha",
                    team="blue",
                    stats={
                        "points": 2,
                        "goals": 1,
                        "assists": 0,
                        "saves": 0,
                        "stuns": 1,
                        "steals": 0,
                        "shots_taken": 2,
                        "passes": 1,
                        "catches": 1,
                        "interceptions": 0,
                        "turnovers": 1,
                        "blocks": 0,
                        "possession_time": 7.0,
                    },
                )
                matches_repo.add_match_player_stat(
                    connection,
                    match_id=match_two_id,
                    match_alias="Bravo",
                    player_id=bravo_id,
                    userid="u-bravo",
                    team="orange",
                    stats={
                        "points": 6,
                        "goals": 2,
                        "assists": 1,
                        "saves": 1,
                        "stuns": 4,
                        "steals": 1,
                        "shots_taken": 3,
                        "passes": 2,
                        "catches": 2,
                        "interceptions": 1,
                        "turnovers": 0,
                        "blocks": 1,
                        "possession_time": 11.0,
                    },
                )
                matches_repo.add_match_player_stat(
                    connection,
                    match_id=match_two_id,
                    match_alias="Self",
                    player_id=self_id,
                    userid="u-self",
                    team="blue",
                    stats={"points": 1, "goals": 0, "shots_taken": 1, "passes": 1, "catches": 1},
                )

                advanced_player_metrics_repo.add_metric_rows(
                    connection,
                    [
                        {
                            "match_id": match_one_id,
                            "player_id": alpha_id,
                            "match_alias": "Alpha",
                            "userid": "u-alpha",
                            "team": "blue",
                            "completed_passes": 2,
                            "inferred_catches": 2,
                            "initiators": 1,
                            "open_for_pass_samples": 3,
                            "lane_blocks": 1,
                            "successful_clears": 1,
                            "goals_2_guarded": 1,
                            "goals_3_open_net": 1,
                            "metadata": {"passes_to_open_receiver": 2, "catches_open": 2},
                        },
                        {
                            "match_id": match_one_id,
                            "player_id": bravo_id,
                            "match_alias": "Bravo",
                            "userid": "u-bravo",
                            "team": "blue",
                            "completed_passes": 4,
                            "inferred_catches": 3,
                            "initiators": 1,
                            "open_for_pass_samples": 2,
                            "lane_blocks": 0,
                            "successful_clears": 1,
                            "goals_2_open_net": 1,
                            "metadata": {"passes_to_open_receiver": 3, "catches_open": 2},
                        },
                        {
                            "match_id": match_two_id,
                            "player_id": alpha_id,
                            "match_alias": "Alpha",
                            "userid": "u-alpha",
                            "team": "blue",
                            "completed_passes": 1,
                            "inferred_catches": 1,
                            "goals_2_open_net": 1,
                            "missed_shots": 1,
                            "metadata": {"passes_to_open_receiver": 1},
                        },
                        {
                            "match_id": match_two_id,
                            "player_id": bravo_id,
                            "match_alias": "Bravo",
                            "userid": "u-bravo",
                            "team": "orange",
                            "completed_passes": 2,
                            "inferred_catches": 2,
                            "goals_2_guarded": 1,
                            "goals_2_open_net": 1,
                            "metadata": {"passes_to_open_receiver": 1, "catches_open": 1},
                        },
                    ],
                )
        finally:
            connection.close()

        service = PlayerComparisonService(self.database_path)
        payload = service.compare(alpha_id, bravo_id)

        self.assertEqual(payload["left_player"]["canonical_name"], "Alpha")
        self.assertEqual(payload["right_player"]["canonical_name"], "Bravo")
        self.assertEqual(payload["shared"]["shared_matches"], 2)
        self.assertEqual(payload["shared"]["together_matches"], 1)
        self.assertEqual(payload["shared"]["opposed_matches"], 1)
        self.assertEqual(payload["left_stats"]["matches"], 2)
        self.assertEqual(payload["right_stats"]["matches"], 2)
        self.assertIn("shooting", payload["left_advanced"]["category_breakdown"])
        contexts = {row["context"] for row in payload["shared"]["recent_shared_matches"]}
        self.assertIn("together", contexts)
        self.assertIn("opposed", contexts)

        public_only = service.compare(
            alpha_id,
            bravo_id,
            filters=StatsFilter(
                include_public=True,
                include_private=False,
                include_tournament=False,
                include_unknown=False,
            ),
        )
        self.assertEqual(public_only["shared"]["shared_matches"], 1)
        self.assertEqual(public_only["shared"]["together_matches"], 1)

        private_scopes = service.compare(
            alpha_id,
            bravo_id,
            filters=StatsFilter(
                include_public=False,
                include_private=True,
                include_tournament=False,
                include_unknown=False,
                private_match_types=("PUG", "Scrimmage"),
            ),
        )
        self.assertEqual(private_scopes["filters"]["private_match_types"], ["PUG", "Scrimmage"])
        self.assertEqual(private_scopes["shared"]["shared_matches"], 1)


if __name__ == "__main__":
    unittest.main()
