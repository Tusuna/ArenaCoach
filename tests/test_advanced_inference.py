from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from arena_coach.database import connect_database, initialize_database
from arena_coach.inference import AdvancedInferenceService
from arena_coach.parsing.normalized_event import NormalizedEvent
from arena_coach.repositories import advanced_player_metrics_repo, events_repo, matches_repo, players_repo


class AdvancedInferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "arena_coach.db"
        initialize_database(self.database_path)
        self.service = AdvancedInferenceService(self.database_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_turnover_inference_from_possession_change(self):
        match_id = self._create_match_with_log(
            "turnover.jsonl",
            [
                _snapshot(sequence=1, blue_players=[_player("BlueA", "u1", "1", True, z=-30)], orange_players=[_player("OrangeB", "u2", "2", False, z=30)]),
                _snapshot(sequence=2, blue_players=[_player("BlueA", "u1", "1", False, z=-28)], orange_players=[_player("OrangeB", "u2", "2", True, z=28)]),
            ],
            [
                _event("possession_change", 2, actor="OrangeB", target="BlueA", team="orange"),
            ],
        )
        result = self.service.infer_match(match_id, force=True)
        self.assertIn("turnover", result.event_counts)

    def test_intercepted_pass_uses_interception_delta(self):
        match_id = self._create_match_with_log(
            "intercept.jsonl",
            [
                _snapshot(sequence=1, blue_players=[_player("Passer", "u1", "1", True, z=-20), _player("Receiver", "u2", "2", False, z=-5)], orange_players=[_player("Defender", "u3", "3", False, z=0)], disc_z=-18),
                _snapshot(sequence=2, blue_players=[_player("Passer", "u1", "1", False, z=-20), _player("Receiver", "u2", "2", False, z=-4)], orange_players=[_player("Defender", "u3", "3", True, z=-2)], disc_z=-2),
            ],
            [
                _event("pass", 1, actor="Passer", team="blue"),
                _event("interception", 2, actor="Defender", team="orange"),
                _event("possession_change", 2, actor="Defender", target="Passer", team="orange"),
            ],
        )
        self.service.infer_match(match_id, force=True)
        summary = self.service.summary(match_id, min_confidence="low", include_low_confidence=True)
        self.assertEqual(summary["counts"].get("intercepted_pass"), 1)

    def test_missed_shot_and_shot_saved_are_inferred(self):
        miss_match = self._create_match_with_log(
            "missed_shot.jsonl",
            [
                _snapshot(sequence=1, blue_players=[_player("Shooter", "u1", "1", True, z=20)], orange_players=[_player("Goalie", "u2", "2", False, z=45)], disc_z=30),
                _snapshot(sequence=2, blue_players=[_player("Shooter", "u1", "1", False, z=20)], orange_players=[_player("Goalie", "u2", "2", False, z=45)], disc_z=48),
            ],
            [
                _event("shot", 1, actor="Shooter", team="blue"),
            ],
        )
        save_match = self._create_match_with_log(
            "saved_shot.jsonl",
            [
                _snapshot(sequence=1, blue_players=[_player("Shooter", "u1", "1", True, z=25)], orange_players=[_player("Goalie", "u2", "2", False, z=43)], disc_z=32),
                _snapshot(sequence=2, blue_players=[_player("Shooter", "u1", "1", False, z=25)], orange_players=[_player("Goalie", "u2", "2", True, z=42)], disc_z=42),
            ],
            [
                _event("shot", 1, actor="Shooter", team="blue"),
                _event("save", 2, actor="Goalie", team="orange"),
            ],
        )
        self.service.infer_match(miss_match, force=True)
        self.service.infer_match(save_match, force=True)
        miss_summary = self.service.summary(miss_match, min_confidence="low", include_low_confidence=True)
        save_summary = self.service.summary(save_match, min_confidence="low", include_low_confidence=True)
        self.assertEqual(miss_summary["counts"].get("missed_shot"), 1)
        self.assertEqual(save_summary["counts"].get("shot_saved"), 1)

    def test_blocked_shot_does_not_count_as_goalie_save(self):
        block_match = self._create_match_with_log(
            "blocked_shot.jsonl",
            [
                _snapshot(sequence=1, blue_players=[_player("Shooter", "u1", "1", True, z=24)], orange_players=[_player("Blocker", "u2", "2", False, z=26)], disc_z=25),
                _snapshot(sequence=2, blue_players=[_player("Shooter", "u1", "1", False, z=24)], orange_players=[_player("Blocker", "u2", "2", True, z=26)], disc_z=26),
            ],
            [
                _event("shot", 1, actor="Shooter", team="blue"),
                _event("block", 2, actor="Blocker", team="orange"),
            ],
        )
        self.service.infer_match(block_match, force=True)
        summary = self.service.summary(block_match, min_confidence="low", include_low_confidence=True)
        self.assertEqual(summary["counts"].get("shot_saved", 0), 0)
        self.assertEqual(summary["counts"].get("blocked_shot", 0), 1)

    def test_initiator_and_pass_to_covered_teammate(self):
        match_id = self._create_match_with_log(
            "initiator.jsonl",
            [
                _snapshot(
                    sequence=1,
                    blue_players=[
                        _player("Initiator", "u1", "1", True, z=-25),
                        _player("Assister", "u2", "2", False, z=-8),
                    ],
                    orange_players=[_player("Defender", "u3", "3", False, z=-7)],
                    disc_z=-20,
                ),
                _snapshot(
                    sequence=2,
                    blue_players=[
                        _player("Initiator", "u1", "1", False, z=-24),
                        _player("Assister", "u2", "2", True, z=-8),
                        _player("Scorer", "u4", "4", False, z=25),
                    ],
                    orange_players=[_player("Defender", "u3", "3", False, z=-7)],
                    disc_z=-8,
                ),
                _snapshot(
                    sequence=3,
                    blue_players=[
                        _player("Assister", "u2", "2", False, z=0),
                        _player("Scorer", "u4", "4", True, z=25),
                    ],
                    orange_players=[_player("Defender", "u3", "3", False, z=22)],
                    disc_z=24,
                ),
                _snapshot(
                    sequence=4,
                    blue_players=[
                        _player("Receiver", "u5", "5", True, z=10),
                        _player("Passer", "u6", "6", False, z=5),
                    ],
                    orange_players=[_player("Pressure", "u7", "7", False, z=10)],
                    disc_z=10,
                ),
                _snapshot(
                    sequence=5,
                    blue_players=[
                        _player("Receiver", "u5", "5", False, z=10),
                        _player("Passer", "u6", "6", False, z=5),
                    ],
                    orange_players=[_player("Pressure", "u7", "7", True, z=10)],
                    disc_z=10,
                ),
            ],
            [
                _event("pass", 1, actor="Initiator", team="blue"),
                _event("catch", 2, actor="Assister", team="blue"),
                _event("assist", 3, actor="Assister", target="Scorer", team="blue"),
                _event("goal", 3, actor="Scorer", assist="Assister", team="blue"),
                _event("pass", 4, actor="Passer", team="blue"),
                _event("catch", 4, actor="Receiver", team="blue"),
                _event("stun", 5, actor="Pressure", team="orange"),
                _event("possession_change", 5, actor="Pressure", target="Receiver", team="orange"),
            ],
        )
        self.service.infer_match(match_id, force=True)
        summary = self.service.summary(match_id, min_confidence="low", include_low_confidence=True)
        self.assertEqual(summary["counts"].get("initiator"), 1)
        self.assertEqual(summary["counts"].get("pass_to_covered_teammate"), 1)

    def test_clear_and_transition_handle_coordinates(self):
        match_id = self._create_match_with_log(
            "clear_transition.jsonl",
            [
                _snapshot(sequence=1, blue_players=[_player("BlueClear", "u1", "1", True, z=-48), _player("BlueWing", "u2", "2", False, z=-40)], orange_players=[_player("OrangeA", "u3", "3", False, z=35)], disc_z=-46),
                _snapshot(sequence=2, blue_players=[_player("BlueClear", "u1", "1", True, z=-10), _player("BlueWing", "u2", "2", False, z=-2)], orange_players=[_player("OrangeA", "u3", "3", False, z=28)], disc_z=-5),
                _snapshot(sequence=3, blue_players=[_player("BlueClear", "u1", "1", True, z=15), _player("BlueWing", "u2", "2", False, z=8)], orange_players=[_player("OrangeA", "u3", "3", False, z=20)], disc_z=18),
                _snapshot(sequence=4, blue_players=[_player("BlueClear", "u1", "1", False, z=10), _player("BlueWing", "u2", "2", False, z=5)], orange_players=[_player("OrangeA", "u3", "3", True, z=18)], disc_z=18),
                _snapshot(sequence=5, blue_players=[_player("BlueClear", "u1", "1", False, z=2), _player("BlueWing", "u2", "2", False, z=0)], orange_players=[_player("OrangeA", "u3", "3", True, z=-6)], disc_z=-6),
            ],
            [
                _event("possession_change", 4, actor="OrangeA", target="BlueClear", team="orange"),
            ],
        )
        self.service.infer_match(match_id, force=True)
        summary = self.service.summary(match_id, min_confidence="low", include_low_confidence=True)
        self.assertGreaterEqual(summary["counts"].get("clear", 0), 1)
        self.assertGreaterEqual(summary["counts"].get("offensive_transition_time", 0), 1)

    def test_coverage_gap_requires_coordinates_and_missing_raw_log_does_not_crash(self):
        no_coord_match = self._create_match_with_log(
            "no_coords.jsonl",
            [
                _snapshot(sequence=1, blue_players=[_player("Scorer", "u1", "1", True, z=None, include_coords=False)], orange_players=[_player("Defender", "u2", "2", False, z=None, include_coords=False)], include_disc=False),
            ],
            [
                _event("goal", 1, actor="Scorer", team="blue"),
            ],
        )
        missing_raw_match = self._create_match_without_raw_log(
            [
                _event("shot", 1, actor="Shooter", team="blue"),
                _event("save", 2, actor="Goalie", team="orange"),
            ]
        )
        self.service.infer_match(no_coord_match, force=True)
        summary = self.service.summary(no_coord_match, min_confidence="low", include_low_confidence=True)
        self.assertNotIn("shooter_uncovered", summary["counts"])

        result = self.service.infer_match(missing_raw_match, force=True)
        self.assertGreaterEqual(result.advanced_events_saved, 1)

    def test_persistence_and_evidence_fields_are_present(self):
        match_id = self._create_match_with_log(
            "persist.jsonl",
            [
                _snapshot(sequence=1, blue_players=[_player("Shooter", "u1", "1", True, z=20)], orange_players=[_player("Goalie", "u2", "2", False, z=42)], disc_z=28),
                _snapshot(sequence=2, blue_players=[_player("Shooter", "u1", "1", False, z=20)], orange_players=[_player("Goalie", "u2", "2", True, z=42)], disc_z=42),
            ],
            [
                _event("shot", 1, actor="Shooter", team="blue"),
                _event("save", 2, actor="Goalie", team="orange"),
            ],
        )
        self.service.infer_match(match_id, force=True)
        timeline = self.service.timeline(match_id, min_confidence="low", include_low_confidence=True)
        self.assertTrue(timeline)
        first = timeline[0]
        self.assertIn("confidence", first)
        self.assertIn("directness", first)
        self.assertIn("evidence", first)
        self.assertIn("reason", first["evidence"])

    def test_player_metrics_persist_observer_style_pass_and_coverage_signals(self):
        match_id = self._create_match_with_log(
            "observer_style_metrics.jsonl",
            [
                _snapshot(
                    sequence=1,
                    blue_players=[
                        _player("Passer", "u1", "1", True, x=0.0, z=-25),
                        _player("ReceiverOpen", "u2", "2", False, x=8.0, z=-5),
                        _player("ReceiverBlocked", "u3", "3", False, x=0.0, z=-5),
                    ],
                    orange_players=[
                        _player("OrangeDefender", "u4", "4", False, x=0.0, z=-12),
                    ],
                    disc_z=-25,
                ),
                _snapshot(
                    sequence=2,
                    blue_players=[
                        _player("Passer", "u1", "1", False, x=0.0, z=-24),
                        _player("ReceiverOpen", "u2", "2", True, x=8.0, z=-5),
                        _player("ReceiverBlocked", "u3", "3", False, x=0.0, z=-5),
                    ],
                    orange_players=[
                        _player("OrangeDefender", "u4", "4", False, x=0.0, z=-11),
                    ],
                    disc_z=-6,
                ),
            ],
            [],
        )
        result = self.service.infer_match(match_id, force=True)
        self.assertGreaterEqual(result.advanced_player_metrics_saved, 4)

        connection = connect_database(self.database_path)
        try:
            rows = advanced_player_metrics_repo.get_match_metrics(connection, match_id)
        finally:
            connection.close()

        by_key = {(str(row["match_alias"]), str(row["team"])): row for row in rows}
        passer = by_key[("Passer", "blue")]
        receiver_open = by_key[("ReceiverOpen", "blue")]
        receiver_blocked = by_key[("ReceiverBlocked", "blue")]
        defender = by_key[("OrangeDefender", "orange")]

        self.assertEqual(int(passer["completed_passes"]), 1)
        self.assertEqual(int(receiver_open["inferred_catches"]), 1)
        self.assertGreaterEqual(int(receiver_open["open_for_pass_samples"]), 1)
        self.assertGreaterEqual(int(receiver_blocked["lane_blocked_samples"]), 1)
        self.assertGreaterEqual(int(defender["lane_blocks"]), 1)

    def test_player_metrics_store_goal_context_for_open_and_guarded_scores(self):
        guarded_match = self._create_match_with_log(
            "guarded_goal.jsonl",
            [
                _snapshot(
                    sequence=1,
                    blue_players=[_player("Shooter", "u1", "1", True, x=0.0, z=22)],
                    orange_players=[_player("OrangeGoalie", "u2", "2", False, x=0.0, y=0.6, z=35.8)],
                    disc_z=24,
                ),
            ],
            [
                _event("goal", 1, actor="Shooter", team="blue", value=2, metadata={"goal_type": "INSIDE SHOT"}),
            ],
        )
        open_match = self._create_match_with_log(
            "open_goal.jsonl",
            [
                _snapshot(
                    sequence=1,
                    blue_players=[_player("Shooter", "u1", "1", True, x=0.0, z=18)],
                    orange_players=[_player("OrangeDefender", "u2", "2", False, x=8.0, z=15)],
                    disc_z=20,
                ),
            ],
            [
                _event("goal", 1, actor="Shooter", team="blue", value=3, metadata={"goal_type": "LONG SHOT"}),
            ],
        )

        self.service.infer_match(guarded_match, force=True)
        self.service.infer_match(open_match, force=True)

        connection = connect_database(self.database_path)
        try:
            guarded_rows = advanced_player_metrics_repo.get_match_metrics(connection, guarded_match)
            open_rows = advanced_player_metrics_repo.get_match_metrics(connection, open_match)
        finally:
            connection.close()

        guarded_metric = next(row for row in guarded_rows if str(row["match_alias"]) == "Shooter")
        open_metric = next(row for row in open_rows if str(row["match_alias"]) == "Shooter")
        self.assertEqual(int(guarded_metric["goals_2_guarded"]), 1)
        self.assertEqual(int(guarded_metric["goals_2_open_net"]), 0)
        self.assertEqual(int(open_metric["goals_3_open_net"]), 1)
        self.assertEqual(int(open_metric["goals_3_guarded"]), 0)

    def _create_match_with_log(self, filename: str, snapshots: list[dict], events: list[NormalizedEvent]) -> int:
        raw_log_path = self.root / filename
        _write_raw_log(raw_log_path, snapshots)
        return self._create_match(raw_log_path, events)

    def _create_match_without_raw_log(self, events: list[NormalizedEvent]) -> int:
        return self._create_match(self.root / "missing.jsonl", events)

    def _create_match(self, raw_log_path: Path, events: list[NormalizedEvent]) -> int:
        connection = connect_database(self.database_path)
        try:
            with connection:
                player_ids = {}
                aliases = sorted(
                    {
                        alias
                        for event in events
                        for alias in (event.actor_name, event.target_name, event.assist_name)
                        if alias
                    }
                )
                for alias in aliases:
                    player_ids[alias] = players_repo.create_player(connection, alias)
                match_id = matches_repo.create_match(
                    connection,
                    display_name=f"Test Match {raw_log_path.name}",
                    started_at="2026-05-31T20:00:00+00:00",
                    raw_log_path=str(raw_log_path),
                    finalized=True,
                    match_classification="Private",
                    private_match_type="PUG",
                    blue_score=0,
                    orange_score=0,
                )
                for alias, player_id in player_ids.items():
                    team = "blue" if alias.lower().startswith(("blue", "shooter", "passer", "receiver", "initiator", "assister", "scorer")) else "orange"
                    matches_repo.add_match_player(
                        connection,
                        match_id=match_id,
                        match_alias=alias,
                        player_id=player_id,
                        userid=f"user-{alias}",
                        team=team,
                        confirmed=True,
                    )
                    matches_repo.add_match_player_stat(
                        connection,
                        match_id=match_id,
                        match_alias=alias,
                        player_id=player_id,
                        userid=f"user-{alias}",
                        team=team,
                        stats={},
                        metadata={},
                    )
                events_repo.add_events(connection, match_id, events)
        finally:
            connection.close()
        return match_id


def _event(
    event_type: str,
    sequence: int,
    *,
    actor: str | None = None,
    target: str | None = None,
    assist: str | None = None,
    team: str | None = None,
    value: float | None = None,
    metadata: dict | None = None,
) -> NormalizedEvent:
    captured_at = datetime(2026, 5, 31, 20, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=sequence)
    return NormalizedEvent(
        event_type=event_type,
        sequence=sequence,
        captured_at=captured_at.isoformat(),
        game_clock=300.0 - sequence,
        actor_name=actor,
        target_name=target,
        assist_name=assist,
        team=team,
        value=value,
        metadata=metadata or {},
    )


def _snapshot(
    *,
    sequence: int,
    blue_players: list[dict],
    orange_players: list[dict],
    disc_z: float | None = None,
    include_disc: bool = True,
) -> dict:
    snapshot = {
        "game_status": "playing",
        "game_clock": 300.0 - sequence,
        "game_clock_display": f"{5 - (sequence // 60)}:{59 - (sequence % 60):02d}",
        "blue_points": 0,
        "orange_points": 0,
        "teams": [
            {"team": "BLUE TEAM", "possession": any(player.get("possession") for player in blue_players), "players": blue_players},
            {"team": "ORANGE TEAM", "possession": any(player.get("possession") for player in orange_players), "players": orange_players},
        ],
        "possession": [0, 0],
        "sessionid": "test-session",
    }
    if include_disc:
        snapshot["disc"] = {
            "position": [0.0, 4.5, float(disc_z or 0.0)],
            "velocity": [0.0, 0.0, 0.0],
            "forward": [0.0, 0.0, 1.0],
            "left": [1.0, 0.0, 0.0],
            "up": [0.0, 1.0, 0.0],
            "bounce_count": 0,
        }
    return snapshot


def _player(
    name: str,
    userid: str,
    playerid: str,
    possession: bool,
    *,
    x: float = 0.0,
    y: float = 4.3,
    z: float | None,
    include_coords: bool = True,
) -> dict:
    player = {
        "name": name,
        "userid": userid,
        "playerid": playerid,
        "possession": possession,
        "_x": x,
        "stunned": False,
        "blocking": False,
        "holding_left": "none",
        "holding_right": "none",
        "velocity": [0.0, 0.0, 0.0],
        "stats": {
            "points": 0,
            "goals": 0,
            "assists": 0,
            "saves": 0,
            "stuns": 0,
            "steals": 0,
            "shots_taken": 0,
            "passes": 0,
            "catches": 0,
            "interceptions": 0,
            "blocks": 0,
            "possession_time": 0.0,
        },
    }
    if include_coords and z is not None:
        x = float(player.pop("_x", 0.0))
        body_y = float(y)
        player["body"] = {"position": [x, body_y, float(z)]}
        player["head"] = {"position": [x, body_y + 0.3, float(z)]}
        player["lhand"] = {"pos": [x - 0.3, body_y - 0.3, float(z)]}
        player["rhand"] = {"pos": [x + 0.3, body_y - 0.3, float(z)]}
    return player


def _write_raw_log(path: Path, snapshots: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    start = datetime(2026, 5, 31, 20, 0, 0, tzinfo=timezone.utc)
    with path.open("w", encoding="utf-8") as handle:
        for index, snapshot in enumerate(snapshots, start=1):
            captured_at = start + timedelta(seconds=index)
            payload = {
                "sequence": snapshot.pop("sequence", index) if "sequence" in snapshot else index,
                "captured_at": captured_at.isoformat(),
                "source": "mock",
                "snapshot": snapshot,
            }
            handle.write(json.dumps(payload) + "\n")


if __name__ == "__main__":
    unittest.main()
