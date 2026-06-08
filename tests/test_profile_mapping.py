from pathlib import Path
import tempfile
import unittest

from arena_coach.database import connect_database, initialize_database
from arena_coach.log_importer import import_raw_log
from arena_coach import match_mapping
from arena_coach.repositories import matches_repo, players_repo, profiles_repo


FIXTURES = Path(__file__).parent / "fixtures"


class ProfileAndMappingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "arena_coach.db"
        initialize_database(self.database_path)
        self.connection = connect_database(self.database_path)

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_create_and_set_active_profile(self):
        with self.connection:
            profile_id = profiles_repo.create_profile(self.connection, "Peef", "peef")
            self.assertTrue(profiles_repo.set_active_profile(self.connection, profile_id))

            active = profiles_repo.get_active_profile(self.connection)
            self.assertEqual(active["id"], profile_id)
            self.assertEqual(active["display_name"], "Peef")
            self.assertEqual(active["primary_echo_name"], "peef")

    def test_create_player_and_alias_without_duplicate_alias_rows(self):
        with self.connection:
            player_id = players_repo.create_player(self.connection, "Peef")
            alias_id = players_repo.add_alias(self.connection, player_id, "peef", userid="u1", playerid="p1")
            same_alias_id = players_repo.add_alias(self.connection, player_id, "PEEF", userid="u1", playerid="p1")

            self.assertEqual(alias_id, same_alias_id)
            aliases = players_repo.list_aliases(self.connection, player_id)
            self.assertEqual(len(aliases), 1)
            self.assertEqual(aliases[0]["alias_name"], "peef")

    def test_playerid_alone_does_not_suggest_identity_match(self):
        with self.connection:
            player_id = players_repo.create_player(self.connection, "Peef")
            players_repo.add_alias(self.connection, player_id, "peef", userid="1848", playerid="0")

            suggestions = players_repo.suggest_players_for_alias(
                self.connection,
                "BLAZER-189",
                userid="8642307212476326",
                playerid="0",
            )

            self.assertEqual(suggestions, [])

    def test_mapping_guest_confirmation_and_finalize_updates_player_ids(self):
        result = import_raw_log(FIXTURES / "simple_match.jsonl", self.database_path)

        with self.connection:
            profile_id = profiles_repo.create_profile(self.connection, "Alice Coach", "Alice")
            profiles_repo.set_active_profile(self.connection, profile_id)

            alice_id = players_repo.create_player(self.connection, "Alice Canonical")
            match_mapping.map_match_alias(self.connection, result.match_id, "Alice", alice_id)
            match_mapping.confirm_guest(self.connection, result.match_id, "Bob")
            match_mapping.mark_self(self.connection, result.match_id, "Alice")

            finalize_result = match_mapping.finalize_match(self.connection, result.match_id)

            self.assertEqual(finalize_result.user_profile_id, profile_id)
            self.assertEqual(finalize_result.user_team, "blue")
            self.assertEqual(finalize_result.result, "win")

            match = matches_repo.get_match(self.connection, result.match_id)
            self.assertEqual(match["finalized"], 1)

            stat = self.connection.execute(
                """
                SELECT player_id
                FROM match_player_stats
                WHERE match_id = ? AND match_alias = 'Alice'
                """,
                (result.match_id,),
            ).fetchone()
            self.assertEqual(stat["player_id"], alice_id)

            event = self.connection.execute(
                """
                SELECT actor_player_id
                FROM events
                WHERE match_id = ? AND actor_alias = 'Alice'
                ORDER BY id
                LIMIT 1
                """,
                (result.match_id,),
            ).fetchone()
            self.assertEqual(event["actor_player_id"], alice_id)

            guest_event = self.connection.execute(
                """
                SELECT actor_player_id
                FROM events
                WHERE match_id = ? AND actor_alias = 'Bob'
                ORDER BY id
                LIMIT 1
                """,
                (result.match_id,),
            ).fetchone()
            self.assertIsNone(guest_event["actor_player_id"])

    def test_create_player_from_alias_and_keep_one_self_player(self):
        result = import_raw_log(FIXTURES / "simple_match.jsonl", self.database_path)

        with self.connection:
            alice_id = match_mapping.create_player_from_alias(
                self.connection,
                result.match_id,
                "Alice",
                "Alice Canonical",
            )
            bob_id = match_mapping.create_player_from_alias(
                self.connection,
                result.match_id,
                "Bob",
                "Bob Canonical",
            )

            self.assertIsNotNone(alice_id)
            self.assertIsNotNone(bob_id)

            match_mapping.mark_self(self.connection, result.match_id, "Alice")
            match_mapping.mark_self(self.connection, result.match_id, "Bob")

            self_rows = self.connection.execute(
                "SELECT match_alias FROM match_players WHERE match_id = ? AND is_user = 1",
                (result.match_id,),
            ).fetchall()
            self.assertEqual([row["match_alias"] for row in self_rows], ["Bob"])


if __name__ == "__main__":
    unittest.main()
