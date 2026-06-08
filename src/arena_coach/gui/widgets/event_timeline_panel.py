"""Event timeline table."""

from __future__ import annotations

from typing import Any, Dict, Iterable

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget


class EventTimelinePanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["seq", "time", "type", "actor", "target", "assist", "team", "value"])
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)

    def load_events(self, events: Iterable[Dict[str, Any]]) -> None:
        self.table.setSortingEnabled(False)
        rows = list(events)
        self.table.setRowCount(len(rows))
        for row_index, event in enumerate(rows):
            values = [
                event.get("sequence"),
                event.get("game_clock_display") or event.get("captured_at"),
                event.get("event_type"),
                event.get("actor_alias"),
                event.get("target_alias"),
                event.get("assist_alias"),
                event.get("team"),
                event.get("value"),
            ]
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem("" if value is None else str(value)))
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)
