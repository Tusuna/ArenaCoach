"""Main desktop window."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any, Callable, Optional

from PySide6.QtCore import QThreadPool, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from arena_coach.config import AppConfig
from arena_coach.gui.widgets.capture_panel import CapturePanel
from arena_coach.gui.widgets.match_history_panel import MatchHistoryPanel
from arena_coach.gui.widgets.advanced_summary_panel import AdvancedSummaryPanel
from arena_coach.gui.widgets.player_comparison_panel import PlayerComparisonPanel
from arena_coach.gui.widgets.players_panel import PlayersPanel
from arena_coach.gui.widgets.profile_panel import ProfilePanel
from arena_coach.gui.widgets.review_workspace import ReviewWorkspace
from arena_coach.gui.widgets.settings_panel import SettingsPanel
from arena_coach.gui.widgets.stats_preview_panel import StatsPreviewPanel
from arena_coach.gui.widgets.status_bar import ArenaStatusBar
from arena_coach.gui.widgets.tutorial_dialog import TutorialDialog
from arena_coach.gui.workers import FunctionWorker
from arena_coach.services.capture_service import CaptureService
from arena_coach.services.advanced_analysis_service import AdvancedAnalysisService
from arena_coach.services.data_exchange_service import DataExchangeService
from arena_coach.services.import_service import ImportService
from arena_coach.services.layout_service import LayoutService
from arena_coach.services.match_service import MatchService
from arena_coach.services.player_comparison_service import PlayerComparisonService
from arena_coach.services.player_service import PlayerService
from arena_coach.services.profile_service import ProfileService
from arena_coach.services.settings_service import SettingsService
from arena_coach.services.stats_preview_service import StatsPreviewService
from arena_coach.services.tutorial_service import TutorialService


def _action_entry(button: QPushButton, description: str) -> QWidget:
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 2)
    layout.setSpacing(3)
    layout.addWidget(button)
    label = QLabel(description)
    label.setWordWrap(True)
    label.setProperty("class", "muted")
    label.setStyleSheet("font-size: 11px;")
    layout.addWidget(label)
    return wrapper


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self._closing = False
        self.thread_pool = QThreadPool.globalInstance()
        self._workers: list[FunctionWorker] = []
        self.capture_service = CaptureService(config)
        self.advanced_service = AdvancedAnalysisService(config.database_path)
        self.import_service = ImportService(config.database_path, config.raw_log_dir)
        self.profile_service = ProfileService(config.database_path)
        self.match_service = MatchService(config.database_path)
        self.player_service = PlayerService(config.database_path)
        self.player_comparison_service = PlayerComparisonService(config.database_path)
        self.settings_service = SettingsService(config)
        self.data_exchange_service = DataExchangeService(config)
        self.stats_service = StatsPreviewService(config.database_path)
        self.tutorial_service = TutorialService(config.database_path)
        self.layout_service = LayoutService(config.database_path)
        self._layout_profile_id = self.layout_service.active_profile_id()

        self.setWindowTitle("Arena Coach")
        self.resize(1420, 880)

        self.status = ArenaStatusBar()
        self.setStatusBar(self.status)

        self.tabs = QTabWidget()
        self.capture_panel = CapturePanel(self.layout_service)
        self.review_panel = ReviewWorkspace(self.match_service, guided_default=config.use_guided_match_review)
        self.history_panel = MatchHistoryPanel(
            self.match_service,
            self.layout_service,
            self.stats_service,
            self.advanced_service,
        )
        self.players_panel = PlayersPanel(self.player_service)
        self.profile_panel = ProfilePanel(self.profile_service)
        self.stats_panel = StatsPreviewPanel(self.stats_service, self.layout_service)
        self.advanced_summary_panel = AdvancedSummaryPanel(self.advanced_service, self.layout_service)
        self.comparison_panel = PlayerComparisonPanel(self.player_comparison_service, self.layout_service)
        self.settings_panel = SettingsPanel(self.settings_service)
        self.debug_logs = QPlainTextEdit()
        self.debug_logs.setReadOnly(True)

        self.tabs.addTab(self.capture_panel, "Live Capture")
        self.tabs.addTab(self.review_panel, "Match Review")
        self.tabs.addTab(self.history_panel, "Match History")
        self.tabs.addTab(self.players_panel, "Players")
        self.tabs.addTab(self.profile_panel, "Profile")
        self.tabs.addTab(self.stats_panel, "Stats Preview")
        self.tabs.addTab(self.advanced_summary_panel, "Advanced Summary")
        self.tabs.addTab(self.comparison_panel, "Compare Players")
        self.tabs.addTab(self.settings_panel, "Settings")
        self.tabs.addTab(self.debug_logs, "Debug Logs")

        self._build_sidebar()
        self._build_actions()
        self._build_view_menu()
        self._connect_signals()
        self._apply_tooltips()

        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(12)
        root_layout.addWidget(self.sidebar_widget)
        root_layout.addWidget(self.tabs, 1)
        self.setCentralWidget(root)

        self.capture_timer = QTimer(self)
        self.capture_timer.setInterval(500)
        self.capture_timer.timeout.connect(self._refresh_capture_status)
        self.capture_timer.start()
        self.startup_connection_timer = QTimer(self)
        self.startup_connection_timer.setSingleShot(True)
        self.startup_connection_timer.timeout.connect(self.test_connection)
        self.tutorial_timer = QTimer(self)
        self.tutorial_timer.setSingleShot(True)
        self.tutorial_timer.timeout.connect(self.maybe_show_tutorial)

        self.refresh_all()
        self._update_layout_actions()
        self.startup_connection_timer.start(250)
        self.tutorial_timer.start(600)

    def _build_sidebar(self) -> None:
        self.sidebar_widget = QWidget()
        self.sidebar_widget.setMinimumWidth(390)
        self.sidebar_widget.setMaximumWidth(500)
        layout = QVBoxLayout(self.sidebar_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.sidebar_layout = layout

        state_box = QGroupBox("Arena Coach")
        form = QFormLayout(state_box)
        form.setContentsMargins(12, 12, 12, 12)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignTop | Qt.AlignLeft)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.sidebar_profile = QLabel("none")
        self.sidebar_profile.setWordWrap(True)
        self.sidebar_echo = QLabel("unknown")
        self.sidebar_echo.setWordWrap(True)
        self.sidebar_db = QLabel(str(self.config.database_path))
        self.sidebar_db.setWordWrap(True)
        self.sidebar_raw = QLabel(str(self.config.raw_log_dir))
        self.sidebar_raw.setWordWrap(True)
        self.sidebar_latest = QLabel("none")
        self.sidebar_latest.setWordWrap(True)
        self.sidebar_match_combo = QComboBox()
        self.sidebar_match_combo.setEditable(True)
        self.sidebar_match_combo.setMinimumContentsLength(24)
        form.addRow("active profile", self.sidebar_profile)
        form.addRow("Echo API", self.sidebar_echo)
        form.addRow("database", self.sidebar_db)
        form.addRow("match logs", self.sidebar_raw)
        form.addRow("latest match", self.sidebar_latest)
        form.addRow("selected match", self.sidebar_match_combo)
        layout.addWidget(state_box)

    def _build_actions(self) -> None:
        self.actions_box = QGroupBox("Actions")
        actions_layout = QVBoxLayout(self.actions_box)
        actions_layout.setContentsMargins(12, 12, 12, 12)
        actions_layout.setSpacing(8)
        self.action_test = QPushButton("Test Connection")
        self.action_start = QPushButton("Start Logging")
        self.action_stop = QPushButton("Stop Logging")
        self.action_process = QPushButton("Process Latest Match")
        self.action_parse = QPushButton("Advanced: Preview Latest Log")
        self.action_import = QPushButton("Advanced: Import Latest Log")
        self.action_review = QPushButton("Review Selected Match")
        self.action_finalize = QPushButton("Finalize Selected Match")
        self.action_infer = QPushButton("Infer Selected Match")
        self.action_export_data = QPushButton("Export My Data")
        self.action_import_data = QPushButton("Import Shared Data")
        self.action_tutorial = QPushButton("Show Tutorial")
        action_rows = [
            (self.action_test, "Check whether Arena Coach can talk to Echo right now."),
            (self.action_start, "Begin recording live Echo snapshots into a raw match log."),
            (self.action_stop, "End the current live capture and save the raw log."),
            (self.action_process, "Turn the newest raw log into a reviewable match and open review."),
            (self.action_parse, "See a technical preview of the latest raw log without saving a match."),
            (self.action_import, "Save the latest raw log as a match without opening guided review."),
            (self.action_review, "Open the selected match in Guided Review or Advanced Review."),
            (self.action_finalize, "Finalize the selected reviewed match with confirmed identities."),
            (self.action_infer, "Build advanced inferred events and advanced stat context for the selected match."),
            (self.action_export_data, "Create a clean zip in exports and reveal it in File Explorer."),
            (self.action_import_data, "Choose a shared Arena Coach zip and unpack it into imports."),
            (self.action_tutorial, "Open the in-app walkthrough again at any time."),
        ]
        for button, description in action_rows:
            actions_layout.addWidget(_action_entry(button, description))
        self.sidebar_layout.addWidget(self.actions_box)
        self.sidebar_layout.addStretch()

    def _build_view_menu(self) -> None:
        view_menu = self.menuBar().addMenu("View")
        self.customize_layout_action = QAction("Customize Current Tab Layout", self)
        self.customize_layout_action.setCheckable(True)
        self.customize_layout_action.triggered.connect(self._toggle_customize_current_tab)
        self.reset_current_layout_action = QAction("Reset Current Tab Layout", self)
        self.reset_current_layout_action.triggered.connect(self._reset_current_tab_layout)
        self.reset_all_layouts_action = QAction("Reset All Tab Layouts", self)
        self.reset_all_layouts_action.triggered.connect(self._reset_all_tab_layouts)
        view_menu.addAction(self.customize_layout_action)
        self.refresh_layout_action = QAction("Refresh Layout", self)
        self.refresh_layout_action.triggered.connect(self.refresh_current_tab_layout)
        view_menu.addAction(self.reset_current_layout_action)
        view_menu.addAction(self.reset_all_layouts_action)
        view_menu.addAction(self.refresh_layout_action)

    def _connect_signals(self) -> None:
        self.capture_panel.test_connection_requested.connect(self.test_connection)
        self.capture_panel.start_requested.connect(self.start_capture)
        self.capture_panel.stop_requested.connect(self.stop_capture)
        self.capture_panel.process_latest_requested.connect(self.process_latest_match)
        self.capture_panel.parse_requested.connect(self.parse_log)
        self.capture_panel.import_requested.connect(self.import_log)
        self.action_test.clicked.connect(self.test_connection)
        self.action_start.clicked.connect(self.start_capture)
        self.action_stop.clicked.connect(self.stop_capture)
        self.action_process.clicked.connect(self.process_latest_match)
        self.action_parse.clicked.connect(self.parse_latest_log)
        self.action_import.clicked.connect(self.import_latest_log)
        self.action_review.clicked.connect(self.review_selected_match)
        self.action_finalize.clicked.connect(self.finalize_selected_match)
        self.action_infer.clicked.connect(self.infer_selected_match)
        self.action_export_data.clicked.connect(self.export_my_data)
        self.action_import_data.clicked.connect(self.import_external_data)
        self.action_tutorial.clicked.connect(lambda: self.show_tutorial(force=True))
        self.history_panel.review_match_requested.connect(self.open_review_match)
        for panel in (
            self.profile_panel,
            self.players_panel,
            self.review_panel,
            self.history_panel,
            self.settings_panel,
            self.comparison_panel,
        ):
            if hasattr(panel, "message"):
                panel.message.connect(self.show_message)
            if hasattr(panel, "error"):
                panel.error.connect(self.show_error)
        self.profile_panel.data_changed.connect(self._profile_data_changed)
        self.players_panel.data_changed.connect(self._players_data_changed)
        self.review_panel.data_changed.connect(self.refresh_after_match_change)
        self.settings_panel.config_saved.connect(self._config_saved)
        self.settings_panel.export_requested.connect(self.export_my_data)
        self.settings_panel.import_requested.connect(self.import_external_data)
        self.settings_panel.open_folder_requested.connect(self.open_special_folder)
        self.settings_panel.backup_requested.connect(self.backup_database_now)
        self.sidebar_match_combo.currentIndexChanged.connect(self._sidebar_match_selected)
        self.tabs.currentChanged.connect(lambda _: self._update_layout_actions())
        for panel in self._card_layout_tabs():
            button = getattr(panel, "customize_button", None)
            if button is not None:
                button.toggled.connect(lambda _: self._update_layout_actions())

    def refresh_all(self) -> None:
        self.refresh_sidebar()
        self.refresh_logs()
        self.refresh_match_widgets()
        self.profile_panel.reload()
        self.players_panel.reload()
        self.stats_panel.reload()
        self.advanced_summary_panel.reload()
        self.comparison_panel.reload_players()
        self._refresh_capture_status()
        self._maybe_switch_profile_layouts()
        self.refresh_all_layouts()

    def refresh_match_widgets(self) -> None:
        self.history_panel.reload_maps()
        self.history_panel.reload()
        self.review_panel.reload()
        self.refresh_sidebar_matches()

    def refresh_after_match_change(self) -> None:
        self.refresh_match_widgets()
        self.stats_panel.reload()
        self.advanced_summary_panel.reload()
        self.comparison_panel.reload_players()

    def refresh_sidebar(self) -> None:
        active = self.profile_service.get_active_profile()
        self.sidebar_profile.setText(
            f"{active['display_name']} ({active['primary_echo_name'] or 'no Echo name'})" if active else "none"
        )
        self.sidebar_db.setText(str(self.config.database_path))
        self.sidebar_raw.setText(str(self.config.raw_log_dir))
        self.refresh_sidebar_matches()

    def refresh_sidebar_matches(self) -> None:
        matches = self.match_service.list_matches()
        self.sidebar_match_combo.blockSignals(True)
        current = self.sidebar_match_combo.currentData()
        self.sidebar_match_combo.clear()
        for match in matches:
            label = f"{match['display_name']} (Match {match['id']})"
            self.sidebar_match_combo.addItem(label, match["id"])
        if current is not None:
            index = self.sidebar_match_combo.findData(current)
            if index >= 0:
                self.sidebar_match_combo.setCurrentIndex(index)
        self.sidebar_match_combo.blockSignals(False)
        self.sidebar_latest.setText(self.sidebar_match_combo.itemText(0) if self.sidebar_match_combo.count() else "none")

    def refresh_logs(self) -> None:
        self.capture_panel.set_recent_logs(self.import_service.recent_raw_logs())

    def maybe_show_tutorial(self) -> None:
        try:
            if not self.tutorial_service.has_seen_tutorial():
                self.show_tutorial(force=False)
        except Exception:
            return

    def show_tutorial(self, force: bool = False) -> None:
        try:
            if not force and self.tutorial_service.has_seen_tutorial():
                return
        except Exception:
            if not force:
                return
        dialog = TutorialDialog(self)
        dialog.exec()
        try:
            self.tutorial_service.mark_seen()
        except Exception:
            return

    def export_my_data(self, options: Optional[dict[str, bool]] = None) -> None:
        export_options = options or self.settings_panel.export_options()
        self._run_task(
            lambda: self.data_exchange_service.export_data(**export_options),
            self._export_result,
            "Creating tester export...",
        )

    def import_external_data(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Arena Coach export zip",
            str(self.config.imports_dir),
            "Arena Coach export (*.zip)",
        )
        if not path:
            return
        self._run_task(
            lambda: self.data_exchange_service.import_data(Path(path)),
            self._import_external_result,
            "Importing external tester export...",
        )

    def backup_database_now(self) -> None:
        self._run_task(
            lambda: self.data_exchange_service.backup_database(reason="manual"),
            self._backup_result,
            "Creating database backup...",
        )

    def open_special_folder(self, folder_key: str) -> None:
        paths = self.data_exchange_service.openable_paths()
        path = paths.get(folder_key)
        if path is None:
            self.show_error(f"Unknown folder: {folder_key}")
            return
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        if opened:
            self.show_message(f"Opened {folder_key} folder")
        else:
            self.show_error(f"Could not open {folder_key} folder: {path}")

    def _reveal_in_explorer(self, path: Path) -> bool:
        target = Path(path)
        try:
            if target.exists() and target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
                return True
            if target.exists():
                subprocess.Popen(["explorer", str(target)])
                return True
        except Exception:
            pass
        fallback = target.parent if target.suffix else target
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(fallback)))

    def test_connection(self) -> None:
        if self._closing:
            return
        self._run_task(
            self.capture_service.test_connection,
            self._connection_result,
            "Testing Echo API connection...",
        )

    def start_capture(self) -> None:
        try:
            status = self.capture_service.start()
        except Exception as exc:
            self.show_error(str(exc))
            return
        self.capture_panel.set_capture_status(status)
        self.status.set_capture(True)
        self.status.set_raw_log(status.get("raw_log_path"))
        self.tabs.setCurrentWidget(self.capture_panel)
        self.show_message("Logging started")

    def stop_capture(self) -> None:
        self._run_task(self.capture_service.stop, self._capture_stopped, "Stopping capture...")

    def parse_latest_log(self) -> None:
        path = self.import_service.latest_raw_log()
        if path is None:
            self.show_error("No match logs found.")
            return
        self.parse_log(path)

    def import_latest_log(self) -> None:
        path = self.import_service.latest_raw_log()
        if path is None:
            self.show_error("No match logs found.")
            return
        self.import_log(path)

    def process_latest_match(self) -> None:
        path = self.capture_service.current_raw_log_path() or self.import_service.latest_raw_log()
        if path is None:
            self.show_error("No match log found to process.")
            return
        existing = self.match_service.raw_log_imported(path)
        if existing is not None:
            if QMessageBox.question(
                self,
                "Match already processed",
                f"This match log was already processed as Match #{existing['id']}. Open review?",
                QMessageBox.Yes | QMessageBox.Cancel,
            ) == QMessageBox.Yes:
                self.open_review_match(existing["id"])
            return
        self._run_task(
            lambda: self.match_service.process_log_for_review(path, self.import_service),
            self._process_match_result,
            f"Processing match {path.name}...",
        )

    def parse_log(self, path: Path) -> None:
        self._run_task(lambda: self.import_service.parse_log(path), self._parse_result, f"Parsing {path.name}...")

    def import_log(self, path: Path) -> None:
        existing = self.match_service.raw_log_imported(path)
        if existing is not None:
            if QMessageBox.warning(
                self,
                "Match log already saved",
                f"This match log is already Match #{existing['id']}. Save it again?",
                QMessageBox.Yes | QMessageBox.No,
            ) != QMessageBox.Yes:
                return
        self._run_task(lambda: self.import_service.import_log(path), self._import_result, f"Importing {path.name}...")

    def review_selected_match(self) -> None:
        match_id = self.selected_match_id()
        if match_id is not None:
            self.open_review_match(match_id)

    def open_review_match(self, match_id: int) -> None:
        self.review_panel.select_match(match_id)
        self.tabs.setCurrentWidget(self.review_panel)

    def finalize_selected_match(self) -> None:
        match_id = self.selected_match_id()
        if match_id is None:
            self.show_error("Select a match first.")
            return
        self.open_review_match(match_id)
        self.review_panel.finalize_current_match()

    def infer_selected_match(self) -> None:
        match_id = self.selected_match_id()
        if match_id is None:
            self.show_error("Select a match first.")
            return
        self._run_task(
            lambda: self.advanced_service.infer_match(match_id, force=False),
            self._infer_result,
            f"Running advanced inference for match #{match_id}...",
        )

    def selected_match_id(self) -> Optional[int]:
        value = self.sidebar_match_combo.currentData()
        return int(value) if value is not None else self.history_panel.selected_match_id()

    def _sidebar_match_selected(self) -> None:
        match_id = self.selected_match_id()
        if match_id is not None:
            self.history_panel.select_match(match_id)
            self.review_panel.select_match(match_id)

    def _refresh_capture_status(self) -> None:
        status = self.capture_service.status()
        self.capture_panel.set_capture_status(status)
        self.status.set_capture(bool(status.get("running")))
        self.status.set_raw_log(status.get("raw_log_path"))
        self.action_start.setEnabled(not bool(status.get("running")))
        self.action_stop.setEnabled(bool(status.get("running")))

    def _connection_result(self, result: Any) -> None:
        if result.ok:
            text = f"available ({result.source}, {result.latency_ms} ms)"
            self.sidebar_echo.setText(text)
            self.status.set_connection("available", True)
            self.show_message(text)
        else:
            self.sidebar_echo.setText("unavailable")
            self.status.set_connection("unavailable", False)
            self.show_error(result.error or "Echo API unavailable")

    def _capture_stopped(self, status: Any) -> None:
        self.capture_panel.set_capture_status(status)
        self.status.set_capture(False)
        self.status.set_raw_log(status.get("raw_log_path"))
        self.refresh_logs()
        self.show_message("Logging stopped")

    def _parse_result(self, result: Any) -> None:
        self.capture_panel.show_parse_preview(result)
        self.tabs.setCurrentWidget(self.capture_panel)
        self.show_message(f"Parsed {Path(result['raw_log_path']).name}")

    def _import_result(self, result: Any) -> None:
        self.capture_panel.show_import_result(result)
        self.refresh_match_widgets()
        self.review_panel.select_match(result["match_id"])
        self.history_panel.select_match(result["match_id"])
        self.tabs.setCurrentWidget(self.history_panel)
        self.show_message(f"Imported match #{result['match_id']}")

    def _process_match_result(self, result: Any) -> None:
        match_id = result["match_id"]
        self.refresh_match_widgets()
        self.open_review_match(match_id)
        action = "Opened existing match" if result["status"] == "existing" else "Processed match for review"
        self.show_message(f"{action}: #{match_id}")

    def _infer_result(self, result: Any) -> None:
        self.history_panel.select_match(result["match_id"])
        self.tabs.setCurrentWidget(self.history_panel)
        self.show_message(
            f"Advanced inference saved {result['advanced_events_saved']} events for match #{result['match_id']}"
        )

    def _config_saved(self, config: AppConfig) -> None:
        self.config = config
        self.capture_service.update_config(config)
        self.advanced_service = AdvancedAnalysisService(config.database_path)
        self.import_service = ImportService(config.database_path, config.raw_log_dir)
        self.player_comparison_service = PlayerComparisonService(config.database_path)
        self.settings_service.config = config
        self.data_exchange_service = DataExchangeService(config)
        self.history_panel.set_advanced_service(self.advanced_service)
        self.advanced_summary_panel.service = self.advanced_service
        self.comparison_panel.set_service(self.player_comparison_service)
        self.review_panel.set_guided_mode(config.use_guided_match_review)
        self.refresh_all()

    def _profile_data_changed(self) -> None:
        self.refresh_all()

    def _players_data_changed(self) -> None:
        self.refresh_match_widgets()
        self.comparison_panel.reload_players()

    def _run_task(self, function: Callable[[], Any], callback: Callable[[Any], None], message: str) -> None:
        self.show_message(message)
        worker = FunctionWorker(function)
        worker.signals.result.connect(callback)
        worker.signals.error.connect(self.show_error)
        worker.signals.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        self._workers.append(worker)
        self.thread_pool.start(worker)

    def show_message(self, message: str) -> None:
        self.status.set_action(message)
        self.debug_logs.appendPlainText(message)

    def show_error(self, message: str) -> None:
        first_line = message.splitlines()[0] if message else "Unknown error"
        self.status.set_action(first_line, error=True)
        self.debug_logs.appendPlainText(f"ERROR: {message}")

    def _supported_layout_tab(self) -> Optional[QWidget]:
        current = self.tabs.currentWidget()
        if current is not None and hasattr(current, "reset_layout") and hasattr(current, "reload_saved_layout"):
            return current
        return None

    def refresh_current_tab_layout(self) -> None:
        panel = self.tabs.currentWidget()
        if panel is None:
            return
        self._refresh_panel_layout(panel)
        self.show_message("Layout refreshed")

    def refresh_all_layouts(self) -> None:
        for index in range(self.tabs.count()):
            panel = self.tabs.widget(index)
            if panel is not None:
                self._refresh_panel_layout(panel)

    def _toggle_customize_current_tab(self, checked: bool) -> None:
        panel = self._supported_layout_tab()
        if panel is None:
            self.customize_layout_action.setChecked(False)
            return
        panel.set_customize_layout(checked)
        self._update_layout_actions()

    def _reset_current_tab_layout(self) -> None:
        panel = self._supported_layout_tab()
        if panel is None:
            self.show_error("Current tab does not support card layout customization.")
            return
        panel.reset_layout()
        panel.set_customize_layout(False)
        self._refresh_panel_layout(panel)
        self.show_message("Current tab layout reset")
        self._update_layout_actions()

    def _reset_all_tab_layouts(self) -> None:
        profile_id = self.layout_service.active_profile_id()
        self.layout_service.reset_all_card_orders(profile_id)
        for panel in self._card_layout_tabs():
            panel.reset_layout()
            panel.set_customize_layout(False)
            self._refresh_panel_layout(panel)
        self.show_message("All tab layouts reset")
        self._update_layout_actions()

    def _card_layout_tabs(self) -> list[QWidget]:
        return [
            panel
            for panel in (
                self.capture_panel,
                self.history_panel,
                self.stats_panel,
                self.advanced_summary_panel,
                self.comparison_panel,
            )
            if hasattr(panel, "reset_layout")
        ]

    def _update_layout_actions(self) -> None:
        panel = self._supported_layout_tab()
        enabled = panel is not None
        self.customize_layout_action.setEnabled(enabled)
        self.reset_current_layout_action.setEnabled(enabled)
        self.refresh_layout_action.setEnabled(True)
        if enabled:
            action_text = "Save Current Tab Layout" if bool(panel.customize_layout_enabled()) else "Customize Current Tab Layout"
            self.customize_layout_action.setText(action_text)
            self.customize_layout_action.blockSignals(True)
            self.customize_layout_action.setChecked(bool(panel.customize_layout_enabled()))
            self.customize_layout_action.blockSignals(False)
        else:
            self.customize_layout_action.setText("Customize Current Tab Layout")
            self.customize_layout_action.blockSignals(True)
            self.customize_layout_action.setChecked(False)
            self.customize_layout_action.blockSignals(False)

    def _maybe_switch_profile_layouts(self) -> None:
        profile_id = self.layout_service.active_profile_id()
        if profile_id == self._layout_profile_id:
            return
        self._layout_profile_id = profile_id
        for panel in self._card_layout_tabs():
            panel.reload_saved_layout()
            self._refresh_panel_layout(panel)

    def _refresh_panel_layout(self, panel: QWidget) -> None:
        refresh = getattr(panel, "refresh_layout", None)
        if callable(refresh):
            refresh()
        panel.updateGeometry()
        panel.adjustSize()
        if panel.layout() is not None:
            panel.layout().invalidate()
            panel.layout().activate()

    def _export_result(self, result: Any) -> None:
        export_path = Path(result["export_path"])
        self._reveal_in_explorer(export_path)
        self.show_message(f"Export created: {export_path.name}")

    def _import_external_result(self, result: Any) -> None:
        self.show_message(f"Imported external export to: {result['import_dir']}")

    def _backup_result(self, result: Any) -> None:
        self.show_message(f"Backup created: {result['backup_path']}")

    def _apply_tooltips(self) -> None:
        tooltips = {
            self.action_test: "Check whether Arena Coach can reach the local Echo API right now.",
            self.action_start: "Begin recording live Echo snapshots into a raw match log.",
            self.action_stop: "Stop the current live capture session and save the raw match log.",
            self.action_process: "Parse the latest raw log, save it if needed, and open match review.",
            self.action_parse: "Show a technical preview of the latest raw log without saving it as a match.",
            self.action_import: "Import the latest raw log directly into match history without guided review.",
            self.action_review: "Open the currently selected match in the review workspace.",
            self.action_finalize: "Finalize the selected match after identities, teams, and self are confirmed.",
            self.action_infer: "Generate advanced inferred events and advanced player metrics for the selected match.",
            self.action_export_data: "Create a shareable Arena Coach export zip in exports and reveal it in File Explorer.",
            self.action_import_data: "Pick a shared Arena Coach export zip and unpack it into the imports folder.",
            self.action_tutorial: "Open the guided walkthrough again.",
            self.capture_panel.test_button: "Check whether Arena Coach can reach the local Echo API right now.",
            self.capture_panel.start_button: "Begin recording live Echo snapshots into a raw match log.",
            self.capture_panel.stop_button: "Stop the current live capture session and save the raw match log.",
            self.capture_panel.process_button: "Parse the latest raw log, save it if needed, and open match review.",
            self.settings_panel.export_button: "Create a zip export you can send back to the developer.",
            self.settings_panel.import_button: "Import another tester's export into a separate imports folder.",
            self.settings_panel.backup_button: "Create a safety copy of your database before risky changes or updates.",
            self.settings_panel.open_backups_button: "Open the folder that stores Arena Coach database backups.",
            self.settings_panel.open_exports_button: "Open the folder where Arena Coach saves tester exports.",
            self.settings_panel.open_imports_button: "Open the folder where imported tester exports are extracted.",
            self.capture_panel.reset_layout_button: "Reset the current Live Capture card layout without touching your data.",
            self.history_panel.reset_layout_button: "Reset the current Match History card layout without touching your data.",
            self.stats_panel.reset_layout_button: "Reset the current Stats Preview card layout without touching your data.",
            self.advanced_summary_panel.reset_layout_button: "Reset the current Advanced Summary card layout without touching your data.",
            self.comparison_panel.reset_layout_button: "Reset the current Compare Players card layout without touching your data.",
        }
        for widget, text in tooltips.items():
            widget.setToolTip(text)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._closing = True
        self.capture_timer.stop()
        self.startup_connection_timer.stop()
        self.tutorial_timer.stop()
        self.thread_pool.waitForDone(1500)
        super().closeEvent(event)
