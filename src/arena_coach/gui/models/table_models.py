"""Reusable simple table model."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


class DictTableModel(QAbstractTableModel):
    def __init__(self, columns: Sequence[tuple[str, str]], rows: List[Dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.columns = list(columns)
        self.rows = rows or []

    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.EditRole):
            return None
        key = self.columns[index.column()][0]
        value = self.rows[index.row()].get(key)
        if isinstance(value, bool):
            return "yes" if value else "no"
        return "" if value is None else str(value)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.columns[section][1]
        return str(section + 1)
