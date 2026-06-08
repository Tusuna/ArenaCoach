"""Advanced analysis section for selected matches."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from arena_coach.gui.widgets.confidence_filter_widget import ConfidenceFilterWidget
from arena_coach.gui.widgets.compact_card_list import CompactCardList
from arena_coach.gui.widgets.multi_select_menu_button import MultiSelectMenuButton
from arena_coach.services.advanced_analysis_service import AdvancedAnalysisService


EVENT_TYPE_OPTIONS = [
    "all",
    "turnover",
    "intercepted_pass",
    "missed_pass",
    "missed_catch",
    "shot_saved",
    "missed_shot",
    "blocked_shot",
    "stuffed_shot",
    "clear",
    "initiator",
    "pass_to_covered_teammate",
    "shooter_uncovered",
    "lane_coverage_failure",
    "offensive_transition_time",
    "defensive_transition_time",
]


class AdvancedAnalysisPanel(QWidget):
    message = Signal(str)
    error = Signal(str)

    def __init__(self, service: AdvancedAnalysisService) -> None:
        super().__init__()
        self.service = service
        self.current_match_id: Optional[int] = None

        self.confidence_filter = ConfidenceFilterWidget(("high", "medium"))
        self.event_type = MultiSelectMenuButton(all_selected_text="All event types")
        self.event_type.set_options([(event_type, event_type.replace("_", " ").title()) for event_type in EVENT_TYPE_OPTIONS if event_type != "all"])
        self.player_filter = QComboBox()
        self.player_filter.addItem("all players", None)
        self.force_rebuild = QCheckBox("Force recalculation")
        self.infer_button = QPushButton("Run Advanced Inference")
        self.refresh_button = QPushButton("Refresh View")

        self.summary_list = CompactCardList("No advanced events yet.")
        self.player_list = CompactCardList("No player breakdown yet.")
        self.timeline = QTableWidget(0, 6)
        self.timeline.setHorizontalHeaderLabels(["clock", "type", "actor", "target", "confidence", "explanation"])
        self.timeline.setAlternatingRowColors(True)
        self.timeline.setWordWrap(True)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Confidence"))
        filters.addWidget(self.confidence_filter)
        filters.addWidget(QLabel("Type"))
        filters.addWidget(self.event_type)
        filters.addWidget(QLabel("Player"))
        filters.addWidget(self.player_filter)
        filters.addWidget(self.force_rebuild)
        filters.addStretch()
        filters.addWidget(self.infer_button)
        filters.addWidget(self.refresh_button)

        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addWidget(QLabel("Advanced Event Summary"))
        layout.addWidget(self.summary_list)
        layout.addWidget(QLabel("Player Breakdown"))
        layout.addWidget(self.player_list)
        layout.addWidget(QLabel("Advanced Event Timeline"))
        layout.addWidget(self.timeline)

        self.infer_button.clicked.connect(self.infer_current_match)
        self.refresh_button.clicked.connect(self.reload)
        self.confidence_filter.selection_changed.connect(self.reload)
        self.event_type.selection_changed.connect(self.reload)
        self.player_filter.currentIndexChanged.connect(self.reload)

    def set_match(self, match_id: Optional[int], players: Optional[list[dict[str, Any]]] = None) -> None:
        self.current_match_id = match_id
        self.player_filter.blockSignals(True)
        self.player_filter.clear()
        self.player_filter.addItem("all players", None)
        for player in players or []:
            player_id = player.get("player_id")
            if player_id is None:
                continue
            label = player.get("canonical_name") or player.get("match_alias")
            self.player_filter.addItem(str(label), int(player_id))
        self.player_filter.blockSignals(False)
        self.reload()

    def infer_current_match(self) -> None:
        if self.current_match_id is None:
            self.error.emit("Select a match first.")
            return
        try:
            result = self.service.infer_match(self.current_match_id, force=self.force_rebuild.isChecked())
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(
            f"Advanced inference complete for match #{result['match_id']}: {result['advanced_events_saved']} events saved."
        )
        self.reload()

    def reload(self) -> None:
        if self.current_match_id is None:
            self.summary_list.set_items([])
            self.player_list.set_items([])
            self.timeline.setRowCount(0)
            return
        try:
            payload = self.service.summary(
                self.current_match_id,
                confidence_levels=self.confidence_filter.selected_levels(),
                event_types=self.event_type.selected_values(),
                player_id=self.player_filter.currentData(),
                include_low_confidence=True,
            )
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self._load_summary(payload.get("counts") or {})
        self._load_player_breakdown(payload.get("player_breakdown") or [])
        self._load_timeline(payload.get("timeline") or [])

    def _load_summary(self, counts: dict[str, int]) -> None:
        items = [
            {"title": event_type.replace("_", " ").title(), "chips": [f"Count {count}"]}
            for event_type, count in counts.items()
        ]
        self.summary_list.set_items(items)

    def _load_player_breakdown(self, rows: list[dict[str, Any]]) -> None:
        items = []
        for row in rows:
            chips = [f"{key} {value}" for key, value in sorted((row.get("counts") or {}).items())[:5]]
            stats = row.get("stats") or {}
            items.append(
                {
                    "title": row.get("canonical_name") or row.get("alias") or "Unknown",
                    "subtitle": f"Team {row.get('team') or 'unknown'} | Pts {stats.get('points', 0)} | G {stats.get('goals', 0)} | A {stats.get('assists', 0)}",
                    "chips": chips,
                }
            )
        self.player_list.set_items(items)

    def _load_timeline(self, rows: list[dict[str, Any]]) -> None:
        self.timeline.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [
                row.get("start_game_clock") or row.get("start_sequence") or "",
                row.get("event_type") or "",
                row.get("actor_alias") or "",
                row.get("target_alias") or "",
                row.get("confidence") or "",
                row.get("explanation") or "",
            ]
            for column, value in enumerate(values):
                self.timeline.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.timeline.resizeColumnsToContents()
