from pathlib import Path
import unittest

from arena_coach.parsing.event_deriver import derive_events
from arena_coach.parsing.raw_log_reader import RawSnapshotRecord, read_raw_log
from arena_coach.parsing import snapshot_parser as sp


FIXTURES = Path(__file__).parent / "fixtures"


class RawLogReaderTests(unittest.TestCase):
    def test_reads_valid_lines_and_reports_malformed_lines(self):
        result = read_raw_log(FIXTURES / "malformed_lines.jsonl")

        self.assertEqual(result.summary.total_lines, 5)
        self.assertEqual(result.summary.valid_snapshots, 1)
        self.assertEqual(result.summary.invalid_lines, 3)
        self.assertEqual(result.records[0].line_number, 5)


class SnapshotParserTests(unittest.TestCase):
    def test_extracts_player_and_team_fields(self):
        result = read_raw_log(FIXTURES / "simple_match.jsonl")
        snapshot = result.records[0].snapshot
        players = list(sp.iter_players(snapshot))

        self.assertEqual(sp.sessionid(snapshot), "SIMPLE")
        self.assertEqual(sp.map_name(snapshot), "mpl_arena_a")
        self.assertEqual(sp.blue_team(snapshot)["team"], "BLUE TEAM")
        self.assertEqual(players[0]["name"], "Alice")
        self.assertEqual(players[0]["userid"], "1")
        self.assertIn("shots_taken", players[0]["stats"])


class EventDeriverTests(unittest.TestCase):
    def test_score_goal_and_assist_events(self):
        read_result = read_raw_log(FIXTURES / "simple_match.jsonl")
        derived = derive_events(read_result.records)
        event_types = [event.event_type for event in derived.events]

        self.assertIn("score_update", event_types)
        self.assertIn("goal", event_types)
        self.assertIn("assist", event_types)
        self.assertIn("shot", event_types)
        self.assertIn("pass", event_types)

        goal = next(event for event in derived.events if event.event_type == "goal")
        self.assertEqual(goal.actor_name, "Alice")
        self.assertEqual(goal.assist_name, "Bob")
        self.assertEqual(goal.value, 3)

    def test_stat_delta_events(self):
        read_result = read_raw_log(FIXTURES / "stat_deltas.jsonl")
        derived = derive_events(read_result.records)
        event_types = {event.event_type for event in derived.events}

        self.assertTrue({"save", "stun", "steal", "pass", "catch", "shot", "interception", "block", "assist", "goal"}.issubset(event_types))

    def test_join_leave_and_possession_change(self):
        read_result = read_raw_log(FIXTURES / "joins_leaves.jsonl")
        derived = derive_events(read_result.records)
        event_types = [event.event_type for event in derived.events]

        self.assertIn("player_join", event_types)
        self.assertIn("player_leave", event_types)
        self.assertIn("possession_change", event_types)

    def test_missing_fields_do_not_crash(self):
        records = [
            RawSnapshotRecord(
                line_number=1,
                sequence=1,
                captured_at="2026-01-01T00:00:00+00:00",
                source="mock",
                snapshot={},
                raw_line="{}",
            )
        ]
        derived = derive_events(records)
        self.assertEqual(derived.events, [])


if __name__ == "__main__":
    unittest.main()
