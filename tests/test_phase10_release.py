from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt

from arena_coach.config import AppConfig
from arena_coach.database import connect_database, initialize_database
from arena_coach.gui.app import create_application
from arena_coach.gui.widgets.match_history_panel import _scoreboard_items
from arena_coach.gui.main_window import MainWindow
from arena_coach.main import main as cli_main
from arena_coach.services.data_exchange_service import DataExchangeService
from arena_coach.services.profile_service import ProfileService


class Phase10ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.raw_log_dir = self.root / "logs" / "raw"
        self.raw_log_dir.mkdir(parents=True)
        self.database_path = self.root / "data" / "arena_coach.db"
        initialize_database(self.database_path)
        self.config = AppConfig(
            project_root=self.root,
            config_path=self.root / "arena_coach_config.json",
            echo_api_host="127.0.0.1",
            echo_api_port=6721,
            echo_api_path="/session",
            poll_interval_seconds=0.5,
            request_timeout_seconds=1.0,
            raw_log_dir=self.raw_log_dir,
            database_path=self.database_path,
            use_guided_match_review=True,
        )
        for path in (self.config.exports_dir, self.config.imports_dir, self.config.backups_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.profile_service = ProfileService(self.database_path)
        profile_id = self.profile_service.create_profile("Tester", "tester")
        self.profile_service.set_active_profile(profile_id)
        self.service = DataExchangeService(self.config)
        self._seed_match_data()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _seed_match_data(self):
        raw_log_path = self.raw_log_dir / "sample_match.jsonl"
        raw_log_path.write_text('{"sequence":1,"captured_at":"2026-06-08T00:00:00+00:00","source":"mock","snapshot":{}}\n', encoding="utf-8")
        metadata_path = self.raw_log_dir / "sample_match.jsonl.metadata.json"
        metadata_path.write_text('{"snapshot_count":1}\n', encoding="utf-8")

        connection = connect_database(self.database_path)
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO matches (
                        id, user_profile_id, display_name, started_at, match_classification, map_name,
                        blue_score, orange_score, raw_log_path, finalized, created_at
                    )
                    VALUES (1, 1, 'Finalized Match', '2026-06-08T00:00:00+00:00', 'Public', 'mpl_arena_a', 6, 4, ?, 1, '2026-06-08T00:00:00+00:00')
                    """,
                    (str(raw_log_path),),
                )
                connection.execute(
                    """
                    INSERT INTO matches (
                        id, user_profile_id, display_name, started_at, match_classification, map_name,
                        blue_score, orange_score, raw_log_path, finalized, created_at
                    )
                    VALUES (2, 1, 'Unfinalized Match', '2026-06-08T01:00:00+00:00', 'Private', 'mpl_arena_a', 2, 1, ?, 0, '2026-06-08T01:00:00+00:00')
                    """,
                    (str(raw_log_path),),
                )
                connection.execute(
                    """
                    INSERT INTO advanced_events (
                        match_id, event_type, actor_alias, team, confidence, directness, created_at
                    )
                    VALUES (1, 'turnover', 'Tester', 'blue', 'high', 'direct', '2026-06-08T00:00:05+00:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO advanced_player_metrics (
                        match_id, match_alias, userid, team, completed_passes, inferred_turnovers, metadata_json
                    )
                    VALUES (1, 'Tester', 'u-tester', 'blue', 2, 1, '{}')
                    """
                )
        finally:
            connection.close()

    def test_export_zip_contains_manifest_and_filtered_database(self):
        result = self.service.export_data(
            include_raw_logs=False,
            include_debug_logs=False,
            include_unfinalized_matches=False,
            include_advanced_events=False,
        )
        export_path = Path(result["export_path"])
        self.assertTrue(export_path.exists())
        self.assertIn("ArenaCoach_Export_Tester_tester_", export_path.name)
        self.assertTrue(export_path.name.endswith(".zip"))

        with ZipFile(export_path, "r") as archive:
            names = set(archive.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("arena_coach.db", names)
            self.assertIn("README_EXPORT.txt", names)
            self.assertNotIn("raw_logs/sample_match.jsonl", names)
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            self.assertFalse(manifest["raw_logs_included"])
            self.assertFalse(manifest["include_unfinalized_matches"])
            self.assertFalse(manifest["include_advanced_events"])
            extract_dir = self.root / "extract_one"
            extract_dir.mkdir()
            archive.extract("arena_coach.db", extract_dir)

        exported_db = extract_dir / "arena_coach.db"
        connection = connect_database(exported_db)
        try:
            match_count = connection.execute("SELECT COUNT(*) AS count FROM matches").fetchone()["count"]
            advanced_count = connection.execute("SELECT COUNT(*) AS count FROM advanced_events").fetchone()["count"]
            self.assertEqual(match_count, 1)
            self.assertEqual(advanced_count, 0)
        finally:
            connection.close()

    def test_export_can_include_raw_logs(self):
        result = self.service.export_data(include_raw_logs=True)
        with ZipFile(result["export_path"], "r") as archive:
            names = set(archive.namelist())
            self.assertIn("raw_logs/sample_match.jsonl", names)
            self.assertIn("raw_logs/sample_match.jsonl.metadata.json", names)

    def test_import_validates_manifest_and_extracts_to_imports(self):
        export_result = self.service.export_data()
        imported = self.service.import_data(export_result["export_path"])
        self.assertTrue(Path(imported["import_dir"]).exists())
        self.assertTrue(Path(imported["database_path"]).exists())
        listed = self.service.list_imports()
        self.assertTrue(listed)

    def test_import_rejects_zip_without_manifest(self):
        bad_zip = self.root / "bad_export.zip"
        with ZipFile(bad_zip, "w") as archive:
            archive.writestr("not_manifest.txt", "nope")
        with self.assertRaises(ValueError):
            self.service.import_data(bad_zip)

    def test_backup_database_creation(self):
        result = self.service.backup_database(reason="manual_test")
        backup_path = Path(result["backup_path"])
        self.assertTrue(backup_path.exists())
        self.assertGreater(result["size_bytes"], 0)

    def test_setup_and_packaging_scripts_include_safe_guards(self):
        zip_script = Path("scripts/create_tester_zip.ps1").read_text(encoding="utf-8")
        for token in (".venv", "data", "logs", "exports", "imports", "arena_coach_config.json", ".git", ".pytest_cache"):
            self.assertIn(token, zip_script)

        setup_script = Path("scripts/setup_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("Setup complete. Launch with run_arena_coach.pyw", setup_script)
        self.assertNotIn("Remove-Item", setup_script)

        launcher_script = Path("run_arena_coach.pyw").read_text(encoding="utf-8")
        self.assertIn("subprocess.run", launcher_script)
        self.assertIn("CREATE_NO_WINDOW", launcher_script)
        self.assertIn("arena_coach_launcher.log", launcher_script)

        tutorial_doc = Path("docs/user_tutorial.md")
        self.assertTrue(tutorial_doc.exists())

    def test_create_application_reuses_single_qapplication_instance(self):
        app_one = create_application([])
        app_two = create_application([])
        self.assertIs(app_one, app_two)

    def test_layout_refresh_methods_do_not_crash(self):
        app = create_application([])
        window = MainWindow(self.config)
        try:
            window.refresh_current_tab_layout()
            window.refresh_all_layouts()
            window._apply_zoom(1.2, announce=False)
            self.assertEqual(app.property("arena_coach_zoom"), 1.2)
            self.assertEqual(window.zoom_in_action.shortcutContext(), Qt.ApplicationShortcut)
            self.assertEqual(window.zoom_out_action.shortcutContext(), Qt.ApplicationShortcut)
            self.assertEqual(window.zoom_reset_action.shortcutContext(), Qt.ApplicationShortcut)
            window._apply_zoom(1.0, announce=False)
        finally:
            window.close()
            app.processEvents()

    def test_advanced_views_default_to_public_off(self):
        app = create_application([])
        window = MainWindow(self.config)
        try:
            self.assertNotIn("public", window.advanced_summary_panel.classification_filter.selected_values())
            self.assertNotIn("public", window.comparison_panel.classification_filter.selected_values())
            self.assertIn("private", window.advanced_summary_panel.classification_filter.selected_values())
            self.assertIn("private", window.comparison_panel.classification_filter.selected_values())
            self.assertEqual(window.advanced_summary_panel.scoring_mode.currentData(), "mistake_adjusted")
            self.assertEqual(window.comparison_panel.scoring_mode.currentData(), "mistake_adjusted")
            self.assertEqual(window.stats_panel.scoring_mode.currentData(), "mistake_adjusted")
        finally:
            window.close()
            app.processEvents()

    def test_scoreboard_hides_low_activity_rows_by_default(self):
        items = _scoreboard_items(
            [
                {
                    "display_name": "Ghost Row",
                    "canonical_name": None,
                    "match_alias": "Ghost Row",
                    "userid": "u-ghost",
                    "player_id": None,
                    "team": "blue",
                    "points": 0,
                    "goals": 0,
                    "assists": 0,
                    "saves": 0,
                    "stuns": 0,
                    "steals": 0,
                    "shots": 0,
                    "passes": 0,
                    "catches": 0,
                    "turnovers": 0,
                    "interceptions": 0,
                    "blocks": 0,
                    "possession_time": 0.0,
                    "afk_suspected": False,
                    "suppressed_default": False,
                    "advanced_stats": {
                        "completed_passes": 0,
                        "inferred_catches": 0,
                        "clears": 0,
                        "turnovers": 0,
                        "interceptions": 0,
                        "missed_shots": 0,
                        "shots_saved": 0,
                        "blocked_shots": 0,
                        "stuffed_shots": 0,
                        "active_seconds_observed": 12.0,
                        "active_rounds_estimated": 0.02,
                        "movement_distance_observed": 1.0,
                        "active_signal_samples": 1,
                        "detail_tooltips": {},
                        "category_breakdown": {},
                    },
                }
            ],
            show_roster_rows=False,
        )
        self.assertEqual(items, [])
        visible = _scoreboard_items(
            [
                {
                    "display_name": "Ghost Row",
                    "canonical_name": None,
                    "match_alias": "Ghost Row",
                    "userid": "u-ghost",
                    "player_id": None,
                    "team": "blue",
                    "points": 0,
                    "goals": 0,
                    "assists": 0,
                    "saves": 0,
                    "stuns": 0,
                    "steals": 0,
                    "shots": 0,
                    "passes": 0,
                    "catches": 0,
                    "turnovers": 0,
                    "interceptions": 0,
                    "blocks": 0,
                    "possession_time": 0.0,
                    "afk_suspected": False,
                    "suppressed_default": False,
                    "advanced_stats": {
                        "completed_passes": 0,
                        "inferred_catches": 0,
                        "clears": 0,
                        "turnovers": 0,
                        "interceptions": 0,
                        "missed_shots": 0,
                        "shots_saved": 0,
                        "blocked_shots": 0,
                        "stuffed_shots": 0,
                        "active_seconds_observed": 12.0,
                        "active_rounds_estimated": 0.02,
                        "movement_distance_observed": 1.0,
                        "active_signal_samples": 1,
                        "detail_tooltips": {},
                        "category_breakdown": {},
                    },
                }
            ],
            show_roster_rows=True,
        )
        self.assertEqual(len(visible), 1)

    def test_data_cli_commands_smoke(self):
        config_path = self.root / "config.json"
        config_path.write_text(
            json.dumps(
                {
                    "database_path": str(self.database_path),
                    "raw_log_dir": str(self.raw_log_dir),
                }
            ),
            encoding="utf-8",
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = cli_main(["--config", str(config_path), "data", "export"])
        self.assertEqual(exit_code, 0)
        self.assertIn("export created:", stdout.getvalue())

        exports = sorted(self.config.exports_dir.glob("*.zip"))
        self.assertTrue(exports)

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = cli_main(["--config", str(config_path), "data", "import", str(exports[-1])])
        self.assertEqual(exit_code, 0)
        self.assertIn("imported to:", stdout.getvalue())

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = cli_main(["--config", str(config_path), "data", "list-imports"])
        self.assertEqual(exit_code, 0)
        self.assertIn("imports:", stdout.getvalue())

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = cli_main(["--config", str(config_path), "data", "backup"])
        self.assertEqual(exit_code, 0)
        self.assertIn("backup created:", stdout.getvalue())

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = cli_main(["--config", str(config_path), "stats", "categories"])
        self.assertEqual(exit_code, 0)
        self.assertIn("Category Scores", stdout.getvalue())

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            exit_code = cli_main(["--config", str(config_path), "stats", "export-metrics"])
        self.assertEqual(exit_code, 0)
        self.assertIn("metric report created:", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
