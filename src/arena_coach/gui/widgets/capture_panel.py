"""Live capture and match log panel."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from arena_coach.gui.widgets.card_container import CardContainer
from arena_coach.services.layout_service import LayoutService


class CapturePanel(QWidget):
    test_connection_requested = Signal()
    start_requested = Signal()
    stop_requested = Signal()
    process_latest_requested = Signal()
    parse_requested = Signal(Path)
    import_requested = Signal(Path)

    layout_tab_id = "live_capture"

    def __init__(self, layout_service: LayoutService) -> None:
        super().__init__()
        self.layout_service = layout_service
        self.running_label = QLabel("stopped")
        self.snapshot_label = QLabel("0")
        self.game_status_label = QLabel("none")
        self.score_label = QLabel("blue=0 orange=0")
        self.players_label = QLabel("none")
        self.players_label.setWordWrap(True)
        self.raw_path_label = QLabel("none")
        self.raw_path_label.setWordWrap(True)
        self.log_combo = QComboBox()
        self.log_combo.setEditable(False)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)

        self.test_button = QPushButton("Test Connection")
        self.start_button = QPushButton("Start Logging")
        self.stop_button = QPushButton("Stop Logging")
        self.browse_button = QPushButton("Browse Match Log")
        self.parse_button = QPushButton("Advanced: Preview Log")
        self.import_button = QPushButton("Advanced: Import Log")
        self.process_button = QPushButton("Process Match for Review")
        self.customize_button = QPushButton("Customize Layout")
        self.customize_button.setCheckable(True)
        self.reset_layout_button = QPushButton("Reset Layout")

        self.test_button.clicked.connect(self.test_connection_requested.emit)
        self.start_button.clicked.connect(self.start_requested.emit)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.browse_button.clicked.connect(self._browse)
        self.process_button.clicked.connect(self.process_latest_requested.emit)
        self.parse_button.clicked.connect(self._parse_selected)
        self.import_button.clicked.connect(self._import_selected)
        self.customize_button.toggled.connect(self.set_customize_layout)
        self.reset_layout_button.clicked.connect(self._handle_reset_layout)

        self.cards = CardContainer()
        self.cards.order_changed.connect(self._save_layout_order)
        self.cards.sizes_changed.connect(self._save_layout_sizes)
        self.cards.add_card("status", "Status", self._build_status_card())
        self.cards.add_card("controls", "Capture Controls", self._build_controls_card())
        self.cards.add_card("logs", "Match Logs", self._build_logs_card())
        self.cards.add_card("preview", "Parse / Process Result", self._build_preview_card())

        toolbar = QHBoxLayout()
        toolbar.addStretch()
        toolbar.addWidget(self.reset_layout_button)
        toolbar.addWidget(self.customize_button)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.addLayout(toolbar)
        content_layout.addWidget(self.cards)
        content_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)

        self.reload_saved_layout()

    def set_recent_logs(self, paths: Iterable[Path]) -> None:
        current = self.selected_log_path()
        self.log_combo.blockSignals(True)
        self.log_combo.clear()
        for path in paths:
            self.log_combo.addItem(str(path), str(path))
        if current:
            index = self.log_combo.findData(str(current))
            if index >= 0:
                self.log_combo.setCurrentIndex(index)
        self.log_combo.blockSignals(False)

    def selected_log_path(self) -> Optional[Path]:
        value = self.log_combo.currentData()
        return Path(value) if value else None

    def set_capture_status(self, status: Dict[str, Any]) -> None:
        running = bool(status.get("running"))
        self.running_label.setText("running" if running else "stopped")
        self.snapshot_label.setText(str(status.get("snapshot_count") or 0))
        self.game_status_label.setText(str(status.get("latest_game_status") or "none"))
        self.score_label.setText(
            f"blue={status.get('latest_blue_score')} orange={status.get('latest_orange_score')}"
        )
        players = status.get("detected_players") or []
        names = [str(player.get("primary_name") or player.get("name")) for player in players if player.get("primary_name") or player.get("name")]
        self.players_label.setText(", ".join(names) if names else "none")
        self.raw_path_label.setText(str(status.get("raw_log_path") or "none"))

    def show_parse_preview(self, preview: Dict[str, Any]) -> None:
        lines = [
            f"match log: {preview['raw_log_path']}",
            f"valid snapshots: {preview['valid_snapshots']}",
            f"invalid lines: {preview['invalid_lines']}",
            f"sessionid: {preview.get('detected_sessionid') or 'none'}",
            f"map: {preview.get('detected_map') or 'none'}",
            f"players: {', '.join(str(player.get('name')) for player in preview['detected_players'] if player.get('name'))}",
            f"teams: {', '.join(str(team.get('team')) for team in preview['detected_teams'] if team.get('team'))}",
            f"score: blue={preview.get('blue_score')} orange={preview.get('orange_score')}",
            f"events: {preview['event_count']}",
            "event counts:",
        ]
        lines.extend(f"  {key}: {value}" for key, value in preview["event_counts"].items())
        self.preview.setPlainText("\n".join(lines))

    def show_import_result(self, result: Dict[str, Any]) -> None:
        self.preview.setPlainText(
            "\n".join(
                [
                    f"saved match id: {result['match_id']}",
                    f"match log: {result['raw_log_path']}",
                    f"events saved: {result['events_saved']}",
                    f"match players saved: {result['match_players_saved']}",
                    f"match player stats saved: {result['match_player_stats_saved']}",
                    f"finalized: {str(result['finalized']).lower()}",
                ]
            )
        )

    def set_customize_layout(self, enabled: bool) -> None:
        self._sync_customize_button(enabled)
        self.cards.set_customize_mode(enabled)

    def customize_layout_enabled(self) -> bool:
        return self.cards.customize_mode()

    def reload_saved_layout(self) -> None:
        order = self.layout_service.load_card_order(
            self.layout_tab_id,
            self.cards.default_order(),
            self.layout_service.active_profile_id(),
        )
        self.cards.apply_order(order)
        self.cards.apply_sizes(
            self.layout_service.load_card_sizes(self.layout_tab_id, self.layout_service.active_profile_id())
        )

    def reset_layout(self) -> None:
        self.layout_service.reset_card_order(self.layout_tab_id, self.layout_service.active_profile_id())
        self.cards.reset_order()
        self.cards.reset_sizes()

    def refresh_layout(self) -> None:
        self.cards.refresh_layout()
        self.updateGeometry()

    def _save_layout_order(self, order: list[str]) -> None:
        self.layout_service.save_card_order(
            self.layout_tab_id,
            order,
            self.layout_service.active_profile_id(),
        )

    def _save_layout_sizes(self, sizes: dict[str, int]) -> None:
        self.layout_service.save_card_sizes(
            self.layout_tab_id,
            sizes,
            self.layout_service.active_profile_id(),
        )

    def _handle_reset_layout(self) -> None:
        self.reset_layout()
        self.set_customize_layout(False)

    def _sync_customize_button(self, enabled: bool) -> None:
        self.customize_button.blockSignals(True)
        self.customize_button.setChecked(enabled)
        self.customize_button.setText("Save Layout" if enabled else "Customize Layout")
        self.customize_button.blockSignals(False)

    def _build_status_card(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        form.addRow("status", self.running_label)
        form.addRow("snapshots", self.snapshot_label)
        form.addRow("game status", self.game_status_label)
        form.addRow("score", self.score_label)
        form.addRow("detected players", self.players_label)
        form.addRow("current log", self.raw_path_label)
        return widget

    def _build_controls_card(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.addWidget(self.test_button)
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
        layout.addWidget(self.process_button)
        return widget

    def _build_logs_card(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(self.log_combo)
        buttons = QHBoxLayout()
        buttons.addWidget(self.browse_button)
        buttons.addWidget(self.parse_button)
        buttons.addWidget(self.import_button)
        layout.addLayout(buttons)
        return widget

    def _build_preview_card(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(self.preview)
        return widget

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Arena Coach match log", "", "JSONL logs (*.jsonl)")
        if not path:
            return
        existing = self.log_combo.findData(path)
        if existing < 0:
            self.log_combo.insertItem(0, path, path)
            self.log_combo.setCurrentIndex(0)
        else:
            self.log_combo.setCurrentIndex(existing)

    def _parse_selected(self) -> None:
        path = self.selected_log_path()
        if path:
            self.parse_requested.emit(path)

    def _import_selected(self) -> None:
        path = self.selected_log_path()
        if path:
            self.import_requested.emit(path)
