from pathlib import Path
import tempfile
import unittest

from arena_coach.database import connect_database, initialize_database
from arena_coach.log_importer import import_raw_log
from arena_coach.repositories import players_repo, profiles_repo
from arena_coach.services.import_service import ImportService
from arena_coach.services.match_display import build_match_display_name
from arena_coach.services.match_service import MatchService
from arena_coach.services.settings_service import SETTINGS_HELP


FIXTURES = Path(__file__).parent / "fixtures"


class Phase7JWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "arena_coach.db"
        self.raw_log_dir = self.root / "raw"
        self.raw_log_dir.mkdir()
        initialize_database(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_known_userid_strongly_suggests_existing_player(self):
        result = import_raw_log(FIXTURES / "simple_match.jsonl", self.database_path)
        connection = connect_database(self.database_path)
        try:
            with connection:
                player_id = players_repo.create_player(connection, "Alice Canonical")
                players_repo.add_userid(connection, player_id, "1", source="test")
        finally:
            connection.close()

        review = MatchService(self.database_path).get_review_data(result.match_id)
        alice = _player(review, "Alice")

        self.assertEqual(alice["suggestions"][0]["player_id"], player_id)
        self.assertEqual(alice["suggestions"][0]["reason"], "same userid")

    def test_unseen_userid_does_not_silently_merge(self):
        result = import_raw_log(FIXTURES / "simple_match.jsonl", self.database_path)
        connection = connect_database(self.database_path)
        try:
            with connection:
                player_id = players_repo.create_player(connection, "Existing Stranger")
                players_repo.add_userid(connection, player_id, "99", source="test")
        finally:
            connection.close()

        review = MatchService(self.database_path).get_review_data(result.match_id)
        alice = _player(review, "Alice")

        self.assertFalse([suggestion for suggestion in alice["suggestions"] if suggestion["player_id"] == player_id])

    def test_userid_cannot_belong_to_two_players(self):
        connection = connect_database(self.database_path)
        try:
            with connection:
                first_id = players_repo.create_player(connection, "First Player")
                second_id = players_repo.create_player(connection, "Second Player")
                players_repo.add_userid(connection, first_id, "unique-userid", source="test")
                with self.assertRaises(ValueError):
                    players_repo.add_userid(connection, second_id, "unique-userid", source="test")
        finally:
            connection.close()

    def test_new_self_userid_is_attached_on_finalize(self):
        result = import_raw_log(FIXTURES / "simple_match.jsonl", self.database_path)
        service = MatchService(self.database_path)
        connection = connect_database(self.database_path)
        try:
            with connection:
                profile_id = profiles_repo.create_profile(connection, "Alice Coach", "Alice")
                profiles_repo.set_active_profile(connection, profile_id)
                player_id = players_repo.create_player(connection, "Alice Canonical")
        finally:
            connection.close()

        service.map_player(result.match_id, "Alice", player_id)
        service.mark_self(result.match_id, "Alice")
        service.confirm_guest(result.match_id, "Bob")
        service.finalize_match(result.match_id)

        connection = connect_database(self.database_path)
        try:
            known = players_repo.find_userid(connection, "1")
        finally:
            connection.close()
        self.assertEqual(known["player_id"], player_id)

    def test_self_and_guest_state_blocks_finalization(self):
        result = import_raw_log(FIXTURES / "simple_match.jsonl", self.database_path)
        service = MatchService(self.database_path)
        connection = connect_database(self.database_path)
        try:
            with connection:
                profile_id = profiles_repo.create_profile(connection, "Alice Coach", "Alice")
                profiles_repo.set_active_profile(connection, profile_id)
        finally:
            connection.close()

        service.confirm_guest(result.match_id, "Alice")
        service.mark_self(result.match_id, "Alice")
        validation = service.validate_finalize(result.match_id)

        self.assertFalse(validation["can_finalize"])
        messages = " ".join(item["message"] for item in validation["items"] if not item["ok"])
        self.assertIn("Self player must be linked", messages)

    def test_player_option_labels_do_not_use_id_prefixes(self):
        connection = connect_database(self.database_path)
        try:
            with connection:
                players_repo.create_player(connection, "Peef")
        finally:
            connection.close()

        options = MatchService(self.database_path).list_player_options()

        self.assertEqual(options[0]["label"], "Peef")
        self.assertFalse(options[0]["label"].startswith("#"))

    def test_process_log_for_review_detects_duplicates(self):
        service = MatchService(self.database_path)
        import_service = ImportService(self.database_path, self.raw_log_dir)

        first = service.process_log_for_review(FIXTURES / "simple_match.jsonl", import_service)
        second = service.process_log_for_review(FIXTURES / "simple_match.jsonl", import_service)

        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "existing")
        self.assertEqual(second["match_id"], first["match_id"])

    def test_settings_help_and_match_display_name(self):
        for key in (
            "use_guided_match_review",
            "echo_api_host",
            "echo_api_port",
            "echo_api_path",
            "poll_interval_seconds",
            "request_timeout_seconds",
            "raw_log_dir",
            "database_path",
        ):
            self.assertIn(key, SETTINGS_HELP)
            self.assertTrue(SETTINGS_HELP[key])

        display_name = build_match_display_name(
            {
                "finalized": True,
                "match_classification": "Public",
                "started_at": "2026-05-24T21:40:35+00:00",
                "blue_score": 15,
                "orange_score": 2,
                "user_team": "blue",
                "result": "win",
            }
        )
        self.assertTrue(display_name.startswith("Finalized Public 2026-05-24"))
        self.assertIn("Win 15-2", display_name)


def _player(review, alias):
    return next(player for player in review["players"] if player["match_alias"] == alias)


if __name__ == "__main__":
    unittest.main()
