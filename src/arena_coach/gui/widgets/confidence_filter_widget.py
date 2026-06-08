"""Reusable multi-select confidence filter widget."""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QWidget


CONFIDENCE_LEVELS = ("high", "medium", "low")


class ConfidenceFilterWidget(QWidget):
    selection_changed = Signal()

    def __init__(self, selected: Iterable[str] | None = None) -> None:
        super().__init__()
        selected_set = {str(level).casefold() for level in (selected or ("high", "medium"))}
        if not selected_set:
            selected_set = {"high", "medium"}

        self._updating = False
        self.high = QCheckBox("High")
        self.medium = QCheckBox("Medium")
        self.low = QCheckBox("Low")
        self._boxes = {
            "high": self.high,
            "medium": self.medium,
            "low": self.low,
        }

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for level in CONFIDENCE_LEVELS:
            box = self._boxes[level]
            box.setChecked(level in selected_set)
            box.toggled.connect(lambda checked, level=level: self._on_toggled(level, checked))
            layout.addWidget(box)

    def selected_levels(self) -> list[str]:
        return [level for level in CONFIDENCE_LEVELS if self._boxes[level].isChecked()]

    def set_selected_levels(self, selected: Iterable[str]) -> None:
        selected_set = {str(level).casefold() for level in selected}
        if not selected_set:
            selected_set = {"high", "medium"}
        self._updating = True
        try:
            for level in CONFIDENCE_LEVELS:
                self._boxes[level].setChecked(level in selected_set)
        finally:
            self._updating = False
        self.selection_changed.emit()

    def _on_toggled(self, level: str, checked: bool) -> None:
        if self._updating:
            return
        if checked:
            self.selection_changed.emit()
            return
        if any(box.isChecked() for key, box in self._boxes.items() if key != level):
            self.selection_changed.emit()
            return
        self._updating = True
        try:
            self._boxes[level].setChecked(True)
        finally:
            self._updating = False

