from pathlib import Path
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDockWidget

from arena_coach.config import AppConfig
from arena_coach.database import connect_database, initialize_database
from arena_coach.gui.main_window import MainWindow
from arena_coach.parsing.match_context import derive_round_context, derive_team_split_stats
from arena_coach.parsing.raw_log_reader import RawSnapshotRecord
from arena_coach.services.layout_service import LayoutService


class MatchContextDerivationTests(unittest.TestCase):
    def test_zero_stat_duplicate_row_is_suppressed(self):
        records = [
            _record(
                1,
                "playing",
                0,
                0,
                blue_players=[_player("Switch", "u-switch", "1", _stats())],
                orange_players=[_player("Switch", "u-switch", "2", _stats())],
            ),
            _record(
                2,
                "playing",
                2,
                0,
                blue_players=[_player("Switch", "u-switch", "1", _stats(points=2, goals=1))],
                orange_players=[_player("Switch", "u-switch", "2", _stats())],
            ),
        ]
        rows = derive_team_split_stats(records, {})

        blue_row = rows["userid:u-switch|blue"]
        orange_row = rows["userid:u-switch|orange"]
        self.assertEqual(int(blue_row["stats"]["points"]), 2)
        self.assertTrue(blue_row["metadata"]["active_participation"])
        self.assertTrue(orange_row["metadata"]["suppressed_default"])
        self.assertFalse(orange_row["metadata"]["active_participation"])

    def test_team_switch_rows_stay_split_when_both_are_meaningful(self):
        records = [
            _record(1, "playing", 0, 0, blue_players=[_player("Switch", "u-switch", "1", _stats())]),
            _record(2, "playing", 2, 0, blue_players=[_player("Switch", "u-switch", "1", _stats(points=2, goals=1))]),
            _record(
                3,
                "playing",
                2,
                1,
                blue_players=[_player("Switch", "u-switch", "1", _stats(points=2, goals=1))],
                orange_players=[_player("Switch", "u-switch", "2", _stats(points=3, goals=1))],
            ),
        ]
        rows = derive_team_split_stats(records, {})

        blue_row = rows["userid:u-switch|blue"]
        orange_row = rows["userid:u-switch|orange"]
        self.assertTrue(blue_row["metadata"]["active_participation"])
        self.assertTrue(orange_row["metadata"]["active_participation"])
        self.assertEqual(int(blue_row["stats"]["points"]), 2)
        self.assertEqual(int(orange_row["stats"]["points"]), 1)

    def test_round_context_derives_resets_and_carry_over(self):
        reset_rounds = derive_round_context(
            [
                _record(1, "playing", 0, 0),
                _record(2, "round_over", 12, 8),
                _record(3, "pre_match", 0, 0),
                _record(4, "playing", 0, 0),
                _record(5, "round_over", 4, 7),
            ],
            4,
            7,
        )
        self.assertEqual(reset_rounds.total_rounds_played, 2)
        self.assertEqual(reset_rounds.blue_round_wins, 1)
        self.assertEqual(reset_rounds.orange_round_wins, 1)
        self.assertFalse(reset_rounds.points_carry_over)

        carry_over_rounds = derive_round_context(
            [
                _record(1, "playing", 0, 0),
                _record(2, "round_over", 12, 8),
                _record(3, "playing", 12, 8),
                _record(4, "round_over", 16, 15),
            ],
            16,
            15,
        )
        self.assertEqual(carry_over_rounds.total_rounds_played, 2)
        self.assertTrue(carry_over_rounds.points_carry_over)
        self.assertEqual(carry_over_rounds.round_summary[1]["blue_points"], 4)
        self.assertEqual(carry_over_rounds.round_summary[1]["orange_points"], 7)

    def test_round_warning_when_points_and_round_winner_differ(self):
        context = derive_round_context(
            [
                _record(1, "playing", 0, 0, blue_round_score=4, orange_round_score=4),
                _record(2, "post_match", 106, 107, blue_round_score=5, orange_round_score=4),
            ],
            106,
            107,
        )
        self.assertEqual(context.warning, "Point winner and round-record winner differ.")


class LayoutServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "arena_coach.db"
        initialize_database(self.database_path)
        self.service = LayoutService(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_card_order_save_load_and_reset(self):
        default_order = ["profile_summary", "match_quality_summary", "playstyle_guess"]
        saved_order = ["playstyle_guess", "profile_summary", "match_quality_summary"]

        self.service.save_card_order("stats_preview", saved_order, profile_id=7)
        payload = self.service.load_card_order("stats_preview", default_order, profile_id=7)
        self.assertEqual(payload, saved_order)

        self.service.reset_card_order("stats_preview", profile_id=7)
        cleared = self.service.load_card_order("stats_preview", default_order, profile_id=7)
        self.assertEqual(cleared, default_order)
        self.assertEqual(self.service.load_card_sizes("stats_preview", profile_id=7), {})

    def test_card_size_save_load(self):
        saved_sizes = {"blue_team": 320, "orange_team": 280}
        self.service.save_card_sizes("match_history", saved_sizes, profile_id=7)
        self.assertEqual(
            self.service.load_card_sizes("match_history", profile_id=7),
            saved_sizes,
        )

    def test_invalid_saved_card_order_falls_back_to_default(self):
        default_order = ["status", "controls", "logs", "preview"]
        connection = connect_database(self.database_path)
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO app_metadata (key, value, updated_at)
                    VALUES ('gui_card_order_live_capture', '["status","preview"]', CURRENT_TIMESTAMP)
                    """
                )
                connection.execute(
                    """
                    INSERT INTO app_metadata (key, value, updated_at)
                    VALUES ('gui_card_sizes_live_capture', '{"status":"bad","preview":20}', CURRENT_TIMESTAMP)
                    """
                )
        finally:
            connection.close()

        payload = self.service.load_card_order("live_capture", default_order)
        self.assertEqual(payload, default_order)
        self.assertEqual(self.service.load_card_sizes("live_capture"), {})

    def test_reset_all_card_orders_clears_multiple_tabs(self):
        stats_default = ["profile_summary", "match_quality_summary", "playstyle_guess"]
        history_default = ["match_list", "match_detail", "blue_team"]
        self.service.save_card_order(
            "stats_preview",
            ["playstyle_guess", "profile_summary", "match_quality_summary"],
            profile_id=7,
        )
        self.service.save_card_order(
            "match_history",
            ["blue_team", "match_list", "match_detail"],
            profile_id=7,
        )

        self.service.reset_all_card_orders(profile_id=7)

        self.assertEqual(
            self.service.load_card_order("stats_preview", stats_default, profile_id=7),
            stats_default,
        )
        self.assertEqual(
            self.service.load_card_order("match_history", history_default, profile_id=7),
            history_default,
        )

    def test_main_window_tabs_stay_fixed_and_non_dockable(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(
            project_root=self.root,
            config_path=self.root / "arena_coach_config.json",
            echo_api_host="127.0.0.1",
            echo_api_port=6721,
            echo_api_path="/session",
            poll_interval_seconds=0.5,
            request_timeout_seconds=0.1,
            raw_log_dir=self.root / "raw",
            database_path=self.database_path,
            use_guided_match_review=True,
        )
        config.raw_log_dir.mkdir(parents=True, exist_ok=True)
        window = MainWindow(config)
        try:
            tabs = [window.tabs.tabText(index) for index in range(window.tabs.count())]
            self.assertEqual(
                tabs,
                [
                    "Live Capture",
                    "Match Review",
                    "Match History",
                    "Players",
                    "Profile",
                    "Stats Preview",
                    "Advanced Summary",
                    "Compare Players",
                    "Settings",
                    "Debug Logs",
                ],
            )
            self.assertEqual(len(window.findChildren(QDockWidget)), 0)
        finally:
            window.close()
            app.processEvents()

    def test_reset_current_tab_layout_restores_default_order(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(
            project_root=self.root,
            config_path=self.root / "arena_coach_config.json",
            echo_api_host="127.0.0.1",
            echo_api_port=6721,
            echo_api_path="/session",
            poll_interval_seconds=0.5,
            request_timeout_seconds=0.1,
            raw_log_dir=self.root / "raw",
            database_path=self.database_path,
            use_guided_match_review=True,
        )
        config.raw_log_dir.mkdir(parents=True, exist_ok=True)
        window = MainWindow(config)
        try:
            window.tabs.setCurrentWidget(window.stats_panel)
            default_order = window.stats_panel.cards.default_order()
            window.stats_panel.cards.move_card("top_rivals", -1)
            window.stats_panel.cards.set_card_height("top_rivals", 310)
            self.assertNotEqual(window.stats_panel.cards.card_order(), default_order)

            window._reset_current_tab_layout()
            self.assertEqual(window.stats_panel.cards.card_order(), default_order)
            self.assertEqual(window.stats_panel.cards.card_sizes(), {})
        finally:
            window.close()
            app.processEvents()

    def test_card_order_persists_after_reopen(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(
            project_root=self.root,
            config_path=self.root / "arena_coach_config.json",
            echo_api_host="127.0.0.1",
            echo_api_port=6721,
            echo_api_path="/session",
            poll_interval_seconds=0.5,
            request_timeout_seconds=0.1,
            raw_log_dir=self.root / "raw",
            database_path=self.database_path,
            use_guided_match_review=True,
        )
        config.raw_log_dir.mkdir(parents=True, exist_ok=True)
        first = MainWindow(config)
        try:
            first.capture_panel.cards.move_card("preview", -1)
            saved_order = first.capture_panel.cards.card_order()
        finally:
            first.close()
            app.processEvents()

        second = MainWindow(config)
        try:
            self.assertEqual(second.capture_panel.cards.card_order(), saved_order)
        finally:
            second.close()
            app.processEvents()

    def test_card_size_persists_after_reopen(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(
            project_root=self.root,
            config_path=self.root / "arena_coach_config.json",
            echo_api_host="127.0.0.1",
            echo_api_port=6721,
            echo_api_path="/session",
            poll_interval_seconds=0.5,
            request_timeout_seconds=0.1,
            raw_log_dir=self.root / "raw",
            database_path=self.database_path,
            use_guided_match_review=True,
        )
        config.raw_log_dir.mkdir(parents=True, exist_ok=True)
        first = MainWindow(config)
        try:
            first.history_panel.cards.set_card_height("blue_team", 360)
            first.history_panel.cards.set_card_height("orange_team", 340)
        finally:
            first.close()
            app.processEvents()

        second = MainWindow(config)
        try:
            sizes = second.history_panel.cards.card_sizes()
            self.assertEqual(sizes.get("blue_team"), 360)
            self.assertEqual(sizes.get("orange_team"), 340)
        finally:
            second.close()
            app.processEvents()

    def test_reset_all_layouts_restores_defaults_for_supported_tabs(self):
        app = QApplication.instance() or QApplication([])
        config = AppConfig(
            project_root=self.root,
            config_path=self.root / "arena_coach_config.json",
            echo_api_host="127.0.0.1",
            echo_api_port=6721,
            echo_api_path="/session",
            poll_interval_seconds=0.5,
            request_timeout_seconds=0.1,
            raw_log_dir=self.root / "raw",
            database_path=self.database_path,
            use_guided_match_review=True,
        )
        config.raw_log_dir.mkdir(parents=True, exist_ok=True)
        window = MainWindow(config)
        try:
            capture_default = window.capture_panel.cards.default_order()
            history_default = window.history_panel.cards.default_order()
            window.capture_panel.cards.move_card("preview", -1)
            window.history_panel.cards.move_card("event_timeline", -1)
            window.history_panel.cards.set_card_height("blue_team", 355)
            self.assertNotEqual(window.capture_panel.cards.card_order(), capture_default)
            self.assertNotEqual(window.history_panel.cards.card_order(), history_default)

            window._reset_all_tab_layouts()
            self.assertEqual(window.capture_panel.cards.card_order(), capture_default)
            self.assertEqual(window.history_panel.cards.card_order(), history_default)
            self.assertEqual(window.history_panel.cards.card_sizes(), {})
        finally:
            window.close()
            app.processEvents()


def _record(
    sequence,
    game_status,
    blue_points,
    orange_points,
    *,
    blue_round_score=None,
    orange_round_score=None,
    blue_players=None,
    orange_players=None,
):
    snapshot = {
        "game_status": game_status,
        "blue_points": blue_points,
        "orange_points": orange_points,
        "blue_round_score": blue_round_score,
        "orange_round_score": orange_round_score,
        "teams": [
            {"team": "BLUE TEAM", "players": blue_players or []},
            {"team": "ORANGE TEAM", "players": orange_players or []},
        ],
    }
    return RawSnapshotRecord(
        line_number=sequence,
        sequence=sequence,
        captured_at=f"2026-05-25T20:00:{sequence:02d}+00:00",
        source="mock",
        snapshot=snapshot,
        raw_line="{}",
    )


def _player(name, userid, playerid, stats):
    return {
        "name": name,
        "userid": userid,
        "playerid": playerid,
        "stats": stats,
    }


def _stats(**values):
    base = {
        "points": 0,
        "goals": 0,
        "assists": 0,
        "saves": 0,
        "stuns": 0,
        "steals": 0,
        "shots_taken": 0,
        "passes": 0,
        "catches": 0,
        "turnovers": 0,
        "interceptions": 0,
        "blocks": 0,
        "possession_time": 0.0,
    }
    base.update(values)
    return base


if __name__ == "__main__":
    unittest.main()
