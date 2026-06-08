"""Compact menu button with checkable multi-select options."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QToolButton


class MultiSelectMenuButton(QToolButton):
    selection_changed = Signal()

    def __init__(
        self,
        *,
        all_selected_text: str,
        minimum_selected: int = 1,
    ) -> None:
        super().__init__()
        self._all_selected_text = str(all_selected_text)
        self._minimum_selected = max(0, int(minimum_selected))
        self._options: list[tuple[str, str]] = []
        self._actions: dict[str, QAction] = {}
        self._labels: dict[str, str] = {}
        self._updating = False

        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self.setPopupMode(QToolButton.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._update_text()

    def set_options(
        self,
        options: Sequence[tuple[Any, str]],
        *,
        selected_values: Iterable[Any] | None = None,
    ) -> None:
        previous_selected = (
            {str(value) for value in selected_values}
            if selected_values is not None
            else set(self.selected_values())
        )
        normalized = [(str(value), str(label)) for value, label in options if str(label).strip()]
        self._options = normalized
        self._labels = {value: label for value, label in normalized}
        self._actions.clear()
        self._menu.clear()

        option_values = [value for value, _label in normalized]
        if not option_values:
            self._update_text()
            return

        effective_selected = previous_selected.intersection(option_values)
        if len(effective_selected) < self._minimum_selected:
            effective_selected = set(option_values)

        self._updating = True
        try:
            for value, label in normalized:
                action = QAction(label, self)
                action.setCheckable(True)
                action.setChecked(value in effective_selected)
                action.toggled.connect(lambda checked, value=value: self._on_toggled(value, checked))
                self._menu.addAction(action)
                self._actions[value] = action
        finally:
            self._updating = False
        self._update_text()
        self.selection_changed.emit()

    def selected_values(self) -> list[str]:
        return [value for value, _label in self._options if self._actions.get(value) and self._actions[value].isChecked()]

    def selected_labels(self) -> list[str]:
        return [self._labels[value] for value in self.selected_values() if value in self._labels]

    def set_selected_values(self, values: Iterable[Any]) -> None:
        selected = {str(value) for value in values}
        option_values = [value for value, _label in self._options]
        effective_selected = selected.intersection(option_values)
        if len(effective_selected) < self._minimum_selected:
            effective_selected = set(option_values)

        self._updating = True
        try:
            for value in option_values:
                action = self._actions.get(value)
                if action is not None:
                    action.setChecked(value in effective_selected)
        finally:
            self._updating = False
        self._update_text()
        self.selection_changed.emit()

    def has_value(self, value: Any) -> bool:
        return str(value) in self._actions

    def all_selected(self) -> bool:
        return bool(self._options) and len(self.selected_values()) == len(self._options)

    def _on_toggled(self, value: str, checked: bool) -> None:
        if self._updating:
            return
        if checked:
            self._update_text()
            self.selection_changed.emit()
            return
        selected = self.selected_values()
        if len(selected) >= self._minimum_selected:
            self._update_text()
            self.selection_changed.emit()
            return
        self._updating = True
        try:
            action = self._actions.get(value)
            if action is not None:
                action.setChecked(True)
        finally:
            self._updating = False
        self._update_text()

    def _update_text(self) -> None:
        selected_labels = self.selected_labels()
        if not self._options or len(selected_labels) == len(self._options):
            self.setText(self._all_selected_text)
            return
        if len(selected_labels) <= 2:
            self.setText(", ".join(selected_labels))
            return
        self.setText(f"{len(selected_labels)} selected")
