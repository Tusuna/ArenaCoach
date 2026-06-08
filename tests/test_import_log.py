from pathlib import Path
import sqlite3
import tempfile
import unittest

from arena_coach.log_importer import import_raw_log


FIXTURES = Path(__file__).parent / "fixtures"


class ImportLogTests(unittest.TestCase):
    def test_import_log_creates_unfinalized_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "arena_coach.db"
            result = import_raw_log(FIXTURES / "simple_match.jsonl", database_path)

            self.assertEqual(result.match_id, 1)
            self.assertFalse(result.finalized)
            self.assertEqual(result.match_players_saved, 2)
            self.assertGreater(result.events_saved, 0)

            connection = sqlite3.connect(database_path)
            try:
                match = connection.execute(
                    "SELECT blue_score, orange_score, finalized FROM matches WHERE id = 1"
                ).fetchone()
                self.assertEqual(match, (3, 0, 0))
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], result.events_saved)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM match_players").fetchone()[0], 2)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
