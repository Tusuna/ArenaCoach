"""Canonical player management panel."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from arena_coach.services.player_service import PlayerService


class PlayersPanel(QWidget):
    data_changed = Signal()
    message = Signal(str)
    error = Signal(str)

    def __init__(self, service: PlayerService) -> None:
        super().__init__()
        self.service = service
        self.players: List[Dict[str, Any]] = []
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("search canonical names or aliases")
        self.refresh_button = QPushButton("Refresh")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["id", "canonical name", "notes", "aliases", "created"])
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._load_selected_player)

        self.create_name = QLineEdit()
        self.create_notes = QTextEdit()
        self.create_notes.setMaximumHeight(80)
        self.create_button = QPushButton("Create Player")

        self.detail_id = QLabel("none")
        self.detail_name = QLineEdit()
        self.detail_notes = QTextEdit()
        self.detail_notes.setMaximumHeight(100)
        self.save_button = QPushButton("Save Player")
        self.alias_name = QLineEdit()
        self.alias_userid = QLineEdit()
        self.add_alias_button = QPushButton("Add Alias")
        self.alias_table = QTableWidget(0, 4)
        self.alias_table.setHorizontalHeaderLabels(["alias", "userid", "confidence", "created"])

        self.search_box.textChanged.connect(self.reload)
        self.refresh_button.clicked.connect(self.reload)
        self.create_button.clicked.connect(self._create_player)
        self.save_button.clicked.connect(self._save_player)
        self.add_alias_button.clicked.connect(self._add_alias)

        top = QHBoxLayout()
        top.addWidget(self.search_box, 1)
        top.addWidget(self.refresh_button)

        create_box = QGroupBox("Create Player")
        create_layout = QFormLayout(create_box)
        create_layout.addRow("name", self.create_name)
        create_layout.addRow("notes", self.create_notes)
        create_layout.addRow("", self.create_button)

        detail_box = QGroupBox("Selected Player")
        detail_layout = QFormLayout(detail_box)
        detail_layout.addRow("id", self.detail_id)
        detail_layout.addRow("name", self.detail_name)
        detail_layout.addRow("notes", self.detail_notes)
        detail_layout.addRow("", self.save_button)
        detail_layout.addRow("new alias", self.alias_name)
        detail_layout.addRow("userid", self.alias_userid)
        detail_layout.addRow("", self.add_alias_button)
        detail_layout.addRow("aliases", self.alias_table)

        side = QVBoxLayout()
        side.addWidget(create_box)
        side.addWidget(detail_box)

        body = QHBoxLayout()
        left = QVBoxLayout()
        left.addLayout(top)
        left.addWidget(self.table)
        body.addLayout(left, 2)
        body.addLayout(side, 1)

        layout = QVBoxLayout(self)
        layout.addLayout(body)
        self.reload()

    def reload(self) -> None:
        self.players = self.service.list_players(self.search_box.text())
        self.table.setRowCount(len(self.players))
        for row_index, player in enumerate(self.players):
            values = [player["id"], player["canonical_name"], player["notes"], player["alias_count"], player["created_at"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem("" if value is None else str(value))
                if column == 0:
                    item.setData(256, player["id"])
                self.table.setItem(row_index, column, item)
        self.table.resizeColumnsToContents()

    def selected_player_id(self) -> Optional[int]:
        selected = self.table.selectedItems()
        if not selected:
            return None
        item = self.table.item(selected[0].row(), 0)
        return int(item.text()) if item else None

    def _load_selected_player(self) -> None:
        player_id = self.selected_player_id()
        player = next((item for item in self.players if item["id"] == player_id), None)
        if not player:
            return
        self.detail_id.setText(str(player["id"]))
        self.detail_name.setText(player["canonical_name"])
        self.detail_notes.setPlainText(player["notes"] or "")
        self._load_aliases(player["id"])

    def _load_aliases(self, player_id: int) -> None:
        aliases = self.service.list_aliases(player_id)
        self.alias_table.setRowCount(len(aliases))
        for row_index, alias in enumerate(aliases):
            values = [alias["alias_name"], alias["userid"], alias["confidence"], alias["created_at"]]
            for column, value in enumerate(values):
                self.alias_table.setItem(row_index, column, QTableWidgetItem("" if value is None else str(value)))
        self.alias_table.resizeColumnsToContents()

    def _create_player(self) -> None:
        try:
            player_id = self.service.create_player(self.create_name.text(), self.create_notes.toPlainText())
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Created player #{player_id}")
        self.create_name.clear()
        self.create_notes.clear()
        self.reload()
        self.data_changed.emit()

    def _save_player(self) -> None:
        player_id = self.selected_player_id()
        if player_id is None:
            self.error.emit("Select a player first.")
            return
        try:
            self.service.update_player(player_id, self.detail_name.text(), self.detail_notes.toPlainText())
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Saved player #{player_id}")
        self.reload()
        self.data_changed.emit()

    def _add_alias(self) -> None:
        player_id = self.selected_player_id()
        if player_id is None:
            self.error.emit("Select a player first.")
            return
        try:
            self.service.add_alias(player_id, self.alias_name.text(), self.alias_userid.text())
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit("Alias saved")
        self.alias_name.clear()
        self.alias_userid.clear()
        self._load_aliases(player_id)
        self.reload()
        self.data_changed.emit()
