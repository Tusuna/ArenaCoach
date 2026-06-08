from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from arena_coach.database import connect_database, initialize_database
from arena_coach.log_importer import import_raw_log
from arena_coach.main import main as cli_main
from arena_coach.repositories import players_repo, profiles_repo
from arena_coach.services.match_service import MatchService
from arena_coach.stats.stat_filters import StatsFilter
from arena_coach.stats.stat_models import LoadedMatch, MatchParticipant
from arena_coach.stats.stats_service import StatsEngine


FIXTURES = Path(__file__).parent / "fixtures"


class StatsEngineTests(unittest.TestCase):
    def setUp(self):
        self.matches = _build_matches()
        self.engine = StatsEngine(
            self.matches,
            active_profile={"id": 1, "display_name": "Peef", "primary_echo_name": "peef"},
        )

    def test_match_quality_classifier_and_labels(self):
        summary = self.engine.quality_summary(StatsFilter(finalized_only=False))

        self.assertEqual(summary["counts"]["Competitive Eligible"], 10)
        self.assertEqual(summary["counts"]["AFK Affected"], 1)
        self.assertEqual(summary["counts"]["Low Quality"], 2)
        self.assertEqual(summary["counts"]["Unreviewed"], 1)

        low_quality = self.engine.quality_for_match(103)
        self.assertTrue(low_quality["is_low_quality"])
        self.assertIn("fewer_than_6_active_non_afk_players", low_quality["quality_reasons"])

    def test_profile_summary_defaults_to_finalized_matches(self):
        summary = self.engine.profile_summary()

        self.assertEqual(summary["matches_played"], 13)
        self.assertEqual(summary["competitive_eligible_matches"], 11)
        self.assertEqual(summary["wins"], 5)
        self.assertEqual(summary["losses"], 8)
        self.assertEqual(summary["breakdowns"]["all_finalized"]["matches_played"], 13)

    def test_public_private_filters_and_guest_handling(self):
        public_only = StatsFilter(
            include_public=True,
            include_private=False,
            include_tournament=False,
            include_unknown=False,
        )
        private_only = StatsFilter(
            include_public=False,
            include_private=True,
            include_tournament=False,
            include_unknown=False,
        )

        self.assertEqual(self.engine.profile_summary(public_only)["matches_played"], 12)
        self.assertEqual(self.engine.profile_summary(private_only)["matches_played"], 1)

        default_matchups = self.engine.matchups(StatsFilter(competitive_only=True, include_low_quality=False))
        guest_matchups = self.engine.matchups(
            StatsFilter(competitive_only=True, include_low_quality=False, include_guest_players=True)
        )

        default_names = [row["display_name"] for row in default_matchups["rows"]]
        guest_names = [row["display_name"] for row in guest_matchups["rows"]]
        self.assertNotIn("Mystery (guest)", default_names)
        self.assertIn("Mystery (guest)", guest_names)

    def test_afk_exclusion_and_low_quality_rules(self):
        teammates = self.engine.teammates(StatsFilter(competitive_only=True, include_low_quality=False))
        teammate_names = [row["display_name"] for row in teammates["rows"]]
        self.assertNotIn("Sleepy", teammate_names)

        teammates_with_afk = self.engine.teammates(
            StatsFilter(competitive_only=True, include_low_quality=False, include_afk_players=True)
        )
        names_with_afk = [row["display_name"] for row in teammates_with_afk["rows"]]
        self.assertIn("Sleepy", names_with_afk)

        low_quality = self.engine.quality_for_match(101)
        self.assertTrue(low_quality["is_low_quality"])
        self.assertEqual(low_quality["quality_label"], "Low Quality")

    def test_trends_and_playstyle(self):
        filters = StatsFilter(competitive_only=True, include_low_quality=False)
        trends = self.engine.trends(filters)
        by_name = {row["stat_name"]: row for row in trends["metrics"]}
        goals = by_name["goals"]

        self.assertEqual(goals["previous_average"], 1.0)
        self.assertEqual(goals["last_average"], 2.0)
        self.assertEqual(goals["delta"], 1.0)
        self.assertEqual(goals["direction"], "up")

        playstyle = self.engine.playstyle(filters)
        self.assertEqual(playstyle["label"], "Scorer")

    def test_matchups_and_teammate_summaries(self):
        filters = StatsFilter(competitive_only=True, include_low_quality=False)
        matchups = self.engine.matchups(filters)
        rival = next(row for row in matchups["rows"] if row["display_name"] == "Rival")

        self.assertEqual(rival["matches_against"], 10)
        self.assertEqual(rival["wins_against"], 3)
        self.assertEqual(rival["direct_stuns_against_user"], 10)
        self.assertEqual(matchups["top_rivals"][0]["display_name"], "Rival")

        teammates = self.engine.teammates(filters)
        bravo = next(row for row in teammates["rows"] if row["display_name"] == "Bravo")
        self.assertEqual(bravo["matches_together"], 4)
        self.assertEqual(bravo["confidence"], "Low sample")
        self.assertEqual(teammates["best_teammates"][0]["display_name"], "Bravo")

    def test_private_subtype_filter_and_team_switch_quality(self):
        matches = [
            _match(
                201,
                participants=[
                    _participant(201, "peef", 1, "blue", canonical_name="Peef", userid="u-self", is_user=True, stats=_stats(points=2, goals=1)),
                    _participant(201, "Bravo", 3, "blue", stats=_stats(points=2, goals=1)),
                    _participant(201, "Rival", 5, "orange", stats=_stats(points=1, goals=0, stuns=2)),
                    _participant(201, "Shadow", 9, "orange", stats=_stats(points=1, goals=0)),
                    _participant(201, "Nova", 11, "orange", stats=_stats(points=1, goals=0)),
                    _participant(201, "Alpha", 2, "blue", stats=_stats(points=1, goals=0)),
                ],
                started_at="2026-02-01T20:00:00+00:00",
                match_classification="Private",
                private_match_type="PUG",
                result="win",
                blue_score=8,
                orange_score=5,
            ),
            _match(
                202,
                participants=[
                    _participant(202, "peef", 1, "blue", canonical_name="Peef", userid="u-self", is_user=True, stats=_stats(points=1, goals=0)),
                    _participant(202, "peef", 1, "orange", canonical_name="Peef", userid="u-self", is_user=True, stats=_stats(points=2, goals=1)),
                    _participant(202, "Bravo", 3, "blue", stats=_stats(points=1, goals=0)),
                    _participant(202, "Rival", 5, "orange", stats=_stats(points=2, goals=1)),
                    _participant(202, "Shadow", 9, "orange", stats=_stats(points=1, goals=0)),
                    _participant(202, "Nova", 11, "orange", stats=_stats(points=1, goals=0)),
                    _participant(202, "Alpha", 2, "blue", stats=_stats(points=1, goals=0)),
                    _participant(202, "Charlie", 4, "blue", stats=_stats(points=1, goals=0)),
                ],
                started_at="2026-02-02T20:00:00+00:00",
                match_classification="Private",
                private_match_type="Scrimmage",
                result="loss",
                blue_score=6,
                orange_score=8,
            ),
        ]
        engine = StatsEngine(matches, active_profile={"id": 1, "display_name": "Peef", "primary_echo_name": "peef"})

        pug_only = engine.profile_summary(StatsFilter(private_match_type="PUG"))
        self.assertEqual(pug_only["matches_played"], 1)
        multi_private = engine.profile_summary(StatsFilter(private_match_types=("PUG", "Scrimmage")))
        self.assertEqual(multi_private["matches_played"], 2)

        scrim_quality = engine.quality_for_match(202)
        self.assertTrue(scrim_quality["team_switch_affected"])
        self.assertIn("team_switch_affected", scrim_quality["quality_reasons"])

    def test_zero_stat_duplicate_rows_do_not_distort_teammates_or_matchups(self):
        matches = [
            _match(
                301,
                participants=[
                    _participant(301, "peef", 1, "blue", canonical_name="Peef", userid="u-self", is_user=True, stats=_stats(points=2, goals=1)),
                    _participant(301, "Bravo", 3, "blue", stats=_stats(points=1, goals=0)),
                    _participant(301, "Bravo", 3, "orange", stats=_stats()),
                    _participant(301, "Rival", 5, "orange", stats=_stats(points=3, goals=1, stuns=2)),
                    _participant(301, "Rival", 5, "blue", stats=_stats()),
                    _participant(301, "Alpha", 2, "blue", stats=_stats(points=1, goals=0)),
                    _participant(301, "Shadow", 9, "orange", stats=_stats(points=1, goals=0)),
                    _participant(301, "Nova", 11, "orange", stats=_stats(points=1, goals=0)),
                ],
                started_at="2026-02-03T20:00:00+00:00",
                match_classification="Public",
                result="loss",
                blue_score=5,
                orange_score=7,
            )
        ]
        engine = StatsEngine(matches, active_profile={"id": 1, "display_name": "Peef", "primary_echo_name": "peef"})
        filters = StatsFilter(competitive_only=False, include_low_quality=True)

        teammates = engine.teammates(filters)
        bravo = next(row for row in teammates["rows"] if row["display_name"] == "Bravo")
        self.assertEqual(bravo["matches_together"], 1)

        matchups = engine.matchups(filters)
        rival = next(row for row in matchups["rows"] if row["display_name"] == "Rival")
        self.assertEqual(rival["matches_against"], 1)
        self.assertEqual(rival["direct_stuns_against_user"], 0)


class StatsCliSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "arena_coach.db"
        self.raw_log_dir = self.root / "raw"
        self.raw_log_dir.mkdir()
        self.config_path = self.root / "config.json"
        initialize_database(self.database_path)
        self._seed_minimal_profile_match()
        self.config_path.write_text(
            json.dumps(
                {
                    "database_path": str(self.database_path),
                    "raw_log_dir": str(self.raw_log_dir),
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_stats_commands_smoke(self):
        commands = [
            (["--config", str(self.config_path), "stats", "summary"], "Stats Summary"),
            (["--config", str(self.config_path), "stats", "summary", "--private-type", "Unknown"], "Stats Summary"),
            (["--config", str(self.config_path), "stats", "trends"], "Trend Stats"),
            (["--config", str(self.config_path), "stats", "matchups"], "Matchups"),
            (["--config", str(self.config_path), "stats", "teammates"], "Teammates"),
            (["--config", str(self.config_path), "stats", "quality"], "Match Quality"),
            (["--config", str(self.config_path), "stats", "player", "1"], "Player Summary"),
        ]
        for argv, expected in commands:
            with self.subTest(argv=argv):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = cli_main(argv)
                self.assertEqual(code, 0)
                self.assertIn(expected, stdout.getvalue())

    def _seed_minimal_profile_match(self):
        result = import_raw_log(FIXTURES / "simple_match.jsonl", self.database_path)
        connection = connect_database(self.database_path)
        try:
            with connection:
                profile_id = profiles_repo.create_profile(connection, "Alice Coach", "Alice")
                profiles_repo.set_active_profile(connection, profile_id)
                player_id = players_repo.create_player(connection, "Alice Canonical")
                players_repo.add_alias(connection, player_id, "Alice", userid="1", playerid="0")
        finally:
            connection.close()

        service = MatchService(self.database_path)
        service.map_player(result.match_id, "Alice", 1)
        service.confirm_guest(result.match_id, "Bob")
        service.mark_self(result.match_id, "Alice")
        service.finalize_match(result.match_id)


def _build_matches():
    matches = []
    for index in range(1, 11):
        high = index >= 6
        result = "win" if index <= 3 else "loss"
        third_name = "Bravo" if index <= 4 else "Charlie"
        third_id = 3 if index <= 4 else 4
        orange_third = _participant(
            index,
            "Mystery",
            None,
            "orange",
            canonical_name=None,
            userid="guest-mystery",
            stats=_stats(points=1, goals=0, assists=0, saves=0, stuns=1, steals=0, shots=1, passes=0, catches=1),
            confirmed=True,
        ) if index == 4 else _participant(
            index,
            "Opp3",
            7,
            "orange",
            stats=_stats(points=1, goals=0, assists=1, saves=0, stuns=1, steals=0, shots=1, passes=1, catches=1),
        )
        participants = [
            _participant(
                index,
                "peef",
                1,
                "blue",
                canonical_name="Peef",
                userid="u-self",
                is_user=True,
                stats=_stats(
                    points=4 if high else 2,
                    goals=2 if high else 1,
                    assists=1 if high else 0,
                    saves=0 if high else 1,
                    stuns=3 if high else 2,
                    steals=1,
                    shots=5 if high else 3,
                    passes=3 if high else 1,
                    catches=2 if high else 1,
                    interceptions=1 if high else 0,
                    blocks=1 if high else 0,
                    possession_time=35 if high else 20,
                ),
            ),
            _participant(
                index,
                "Alpha",
                2,
                "blue",
                stats=_stats(points=3, goals=1, assists=1, saves=1, stuns=2, steals=0, shots=2, passes=2, catches=2),
            ),
            _participant(
                index,
                third_name,
                third_id,
                "blue",
                stats=_stats(points=2 if third_name == "Bravo" else 1, goals=1, assists=1, saves=0, stuns=1, steals=0, shots=2, passes=2, catches=1),
            ),
            _participant(
                index,
                "Rival",
                5,
                "orange",
                stats=_stats(points=6, goals=2, assists=1, saves=1, stuns=4, steals=2, shots=4, passes=2, catches=2),
            ),
            _participant(
                index,
                "Opp2",
                6,
                "orange",
                stats=_stats(points=1, goals=1, assists=0, saves=0, stuns=1, steals=0, shots=2, passes=1, catches=1),
            ),
            orange_third,
        ]
        matches.append(
            _match(
                index,
                participants=participants,
                started_at=f"2026-01-{index:02d}T20:00:00+00:00",
                match_classification="Public",
                result=result,
                blue_score=8 if result == "win" else 5,
                orange_score=5 if result == "win" else 8,
                events=[
                    {"event_type": "stun", "actor_player_id": 5, "target_player_id": 1},
                    {"event_type": "steal", "actor_player_id": 5, "target_player_id": 1} if index % 2 == 0 else {"event_type": "pass", "actor_player_id": 2, "target_player_id": 3},
                ],
            )
        )

    matches.append(
        _match(
            101,
            participants=[
                _participant(
                    101,
                    "peef",
                    1,
                    "blue",
                    canonical_name="Peef",
                    userid="u-self",
                    is_user=True,
                    stats=_stats(points=6, goals=2, assists=1, saves=0, stuns=1, steals=0, shots=4, passes=2, catches=1),
                )
            ],
            started_at="2025-12-20T20:00:00+00:00",
            match_classification="Private",
            result="win",
            blue_score=6,
            orange_score=0,
        )
    )

    matches.append(
        _match(
            102,
            participants=[
                _participant(102, "peef", 1, "blue", canonical_name="Peef", userid="u-self", is_user=True, stats=_stats(points=3, goals=1, assists=1, saves=1, stuns=2, steals=1, shots=3, passes=2, catches=2)),
                _participant(102, "Alpha", 2, "blue", stats=_stats(points=2, goals=1, assists=1, saves=0, stuns=2, steals=0, shots=2, passes=2, catches=2)),
                _participant(102, "Charlie", 4, "blue", stats=_stats(points=2, goals=1, assists=0, saves=0, stuns=1, steals=0, shots=2, passes=1, catches=1)),
                _participant(102, "Sleepy", 8, "blue", stats=_stats(), afk_suspected=True),
                _participant(102, "Shadow", 9, "orange", stats=_stats(points=2, goals=1, assists=0, saves=0, stuns=1, steals=0, shots=2, passes=1, catches=1)),
                _participant(102, "Blitz", 10, "orange", stats=_stats(points=1, goals=0, assists=1, saves=0, stuns=1, steals=0, shots=1, passes=1, catches=1)),
                _participant(102, "Nova", 11, "orange", stats=_stats(points=1, goals=0, assists=0, saves=0, stuns=1, steals=0, shots=1, passes=1, catches=1)),
            ],
            started_at="2025-12-19T20:00:00+00:00",
            match_classification="Public",
            result="win",
            blue_score=7,
            orange_score=3,
        )
    )

    matches.append(
        _match(
            103,
            participants=[
                _participant(103, "peef", 1, "blue", canonical_name="Peef", userid="u-self", is_user=True, stats=_stats(points=1, goals=0, assists=0, saves=0, stuns=1, steals=0, shots=1, passes=1, catches=1)),
                _participant(103, "Alpha", 2, "blue", stats=_stats(points=1, goals=0, assists=1, saves=0, stuns=1, steals=0, shots=1, passes=1, catches=1)),
                _participant(103, "Sleepy", 8, "blue", stats=_stats(), afk_suspected=True),
                _participant(103, "Shadow", 9, "orange", stats=_stats(points=2, goals=1, assists=0, saves=0, stuns=1, steals=0, shots=2, passes=1, catches=1)),
                _participant(103, "Blitz", 10, "orange", stats=_stats(points=1, goals=0, assists=1, saves=0, stuns=1, steals=0, shots=1, passes=1, catches=1)),
                _participant(103, "Nova", 11, "orange", stats=_stats(points=1, goals=0, assists=0, saves=0, stuns=1, steals=0, shots=1, passes=1, catches=1)),
            ],
            started_at="2025-12-18T20:00:00+00:00",
            match_classification="Public",
            result="loss",
            blue_score=2,
            orange_score=6,
        )
    )

    matches.append(
        _match(
            104,
            participants=[
                _participant(104, "peef", 1, "blue", canonical_name="Peef", userid="u-self", is_user=True, stats=_stats(points=2, goals=1, assists=0, saves=0, stuns=1, steals=0, shots=2, passes=1, catches=1)),
                _participant(104, "Alpha", 2, "blue", stats=_stats(points=1, goals=0, assists=1, saves=0, stuns=1, steals=0, shots=1, passes=1, catches=1)),
                _participant(104, "Bravo", 3, "blue", stats=_stats(points=1, goals=0, assists=1, saves=0, stuns=1, steals=0, shots=1, passes=1, catches=1)),
                _participant(104, "Shadow", 9, "orange", stats=_stats(points=2, goals=1, assists=0, saves=0, stuns=1, steals=0, shots=2, passes=1, catches=1)),
                _participant(104, "Blitz", 10, "orange", stats=_stats(points=1, goals=0, assists=1, saves=0, stuns=1, steals=0, shots=1, passes=1, catches=1)),
                _participant(104, "Nova", 11, "orange", stats=_stats(points=1, goals=0, assists=0, saves=0, stuns=1, steals=0, shots=1, passes=1, catches=1)),
            ],
            started_at="2025-12-17T20:00:00+00:00",
            match_classification="Public",
            finalized=False,
            result=None,
            blue_score=2,
            orange_score=2,
        )
    )

    return matches


def _match(
    match_id,
    *,
    participants,
    started_at,
    match_classification,
    private_match_type=None,
    result,
    blue_score,
    orange_score,
    blue_round_wins=0,
    orange_round_wins=0,
    total_rounds_played=1,
    finalized=True,
    events=None,
):
    return LoadedMatch(
        id=match_id,
        started_at=started_at,
        ended_at=None,
        created_at=started_at,
        display_name=f"Match {match_id}",
        match_classification=match_classification,
        match_type=f"Echo_Arena_{match_classification}",
        map_name="mpl_arena_a",
        blue_score=blue_score,
        orange_score=orange_score,
        blue_round_wins=blue_round_wins,
        orange_round_wins=orange_round_wins,
        total_rounds_played=total_rounds_played,
        user_team="blue",
        result=result,
        raw_log_path=f"match_{match_id}.jsonl",
        private_match_type=private_match_type,
        finalized=finalized,
        participants=participants,
        events=events or [],
        metadata={},
    )


def _participant(
    match_id,
    alias,
    player_id,
    team,
    *,
    canonical_name=None,
    userid=None,
    is_user=False,
    confirmed=True,
    stats=None,
    afk_suspected=False,
):
    return MatchParticipant(
        match_id=match_id,
        match_alias=alias,
        canonical_name=canonical_name or (alias if player_id is not None else None),
        player_id=player_id,
        userid=userid or f"user-{alias.casefold()}",
        team=team,
        is_user=is_user,
        confirmed=confirmed,
        stats=stats or _stats(),
        metadata={"afk_detection": {"suspected": afk_suspected}},
        afk_suspected=afk_suspected,
        afk_confidence=1.0 if afk_suspected else 0.0,
        afk_reasons=["fixture_afk"] if afk_suspected else [],
        live_samples=0,
        activity_total=sum(float((stats or _stats()).get(key) or 0) for key in ("points", "goals", "assists", "saves", "stuns", "steals", "shots", "passes", "catches", "interceptions", "blocks")),
        meaningful_participation=bool(sum(float((stats or _stats()).get(key) or 0) for key in ("points", "goals", "assists", "saves", "stuns", "steals", "shots", "passes", "catches", "interceptions", "blocks", "possession_time"))),
    )


def _stats(
    *,
    points=0,
    goals=0,
    assists=0,
    saves=0,
    stuns=0,
    steals=0,
    shots=0,
    passes=0,
    catches=0,
    turnovers=0,
    interceptions=0,
    blocks=0,
    possession_time=0.0,
):
    return {
        "points": points,
        "goals": goals,
        "assists": assists,
        "saves": saves,
        "stuns": stuns,
        "steals": steals,
        "shots": shots,
        "passes": passes,
        "catches": catches,
        "turnovers": turnovers,
        "interceptions": interceptions,
        "blocks": blocks,
        "possession_time": possession_time,
    }


if __name__ == "__main__":
    unittest.main()
