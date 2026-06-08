import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from arena_coach.log_importer import import_raw_log
from arena_coach.parsing.afk_detector import detect_afk_players
from arena_coach.parsing.raw_log_reader import RawSnapshotRecord


class AfkDetectionTests(unittest.TestCase):
    def test_detects_player_with_no_stats_and_no_movement(self):
        records = [
            _record(sequence, _snapshot(sequence, afk_position=[0, 0, 0], active_position=[sequence * 2, 0, 0]))
            for sequence in range(1, 5)
        ]

        assessments = detect_afk_players(records, minimum_live_samples=3)
        by_name = {assessment["name"]: assessment for assessment in assessments.values()}

        self.assertTrue(by_name["StillPlayer"]["suspected"])
        self.assertIn("no_stats_or_possession", by_name["StillPlayer"]["reasons"])
        self.assertFalse(by_name["ActivePlayer"]["suspected"])

    def test_import_stores_afk_metadata_on_match_player_stats(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            raw_log_path = temp_path / "afk_match.jsonl"
            database_path = temp_path / "arena_coach.db"
            lines = []
            for sequence in range(1, 122):
                payload = {
                    "sequence": sequence,
                    "captured_at": f"2026-01-01T00:00:{sequence % 60:02d}+00:00",
                    "source": "mock",
                    "snapshot": _snapshot(sequence, afk_position=[0, 0, 0], active_position=[sequence * 0.5, 0, 0]),
                }
                lines.append(json.dumps(payload))
            raw_log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            result = import_raw_log(raw_log_path, database_path)

            connection = sqlite3.connect(database_path)
            try:
                row = connection.execute(
                    """
                    SELECT metadata_json
                    FROM match_player_stats
                    WHERE match_id = ? AND match_alias = 'StillPlayer'
                    """,
                    (result.match_id,),
                ).fetchone()
            finally:
                connection.close()

            metadata = json.loads(row[0])
            self.assertTrue(metadata["afk_detection"]["suspected"])


def _record(sequence, snapshot):
    return RawSnapshotRecord(
        line_number=sequence,
        sequence=sequence,
        captured_at=f"2026-01-01T00:00:{sequence:02d}+00:00",
        source="mock",
        snapshot=snapshot,
        raw_line="{}",
    )


def _snapshot(sequence, afk_position, active_position):
    return {
        "game_status": "playing",
        "game_clock": 300 - sequence,
        "blue_points": 0,
        "orange_points": 0,
        "teams": [
            {
                "team": "BLUE TEAM",
                "players": [
                    _player("StillPlayer", "u-afk", "0", afk_position, _zero_stats()),
                    _player(
                        "ActivePlayer",
                        "u-active",
                        "1",
                        active_position,
                        {
                            **_zero_stats(),
                            "passes": 1,
                            "catches": 1,
                            "possession_time": 2.0,
                        },
                    ),
                ],
            },
            {"team": "ORANGE TEAM", "players": []},
        ],
    }


def _player(name, userid, playerid, position, stats):
    transform = {"position": position}
    return {
        "name": name,
        "userid": userid,
        "playerid": playerid,
        "velocity": [0, 0, 0],
        "body": transform,
        "head": transform,
        "stats": stats,
    }


def _zero_stats():
    return {
        "possession_time": 0,
        "points": 0,
        "saves": 0,
        "goals": 0,
        "stuns": 0,
        "passes": 0,
        "catches": 0,
        "steals": 0,
        "blocks": 0,
        "interceptions": 0,
        "assists": 0,
        "shots_taken": 0,
    }


if __name__ == "__main__":
    unittest.main()
