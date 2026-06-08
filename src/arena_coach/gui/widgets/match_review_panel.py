"""Manual match review and identity mapping panel."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from arena_coach.match_context import PRIVATE_MATCH_TYPES, private_match_type_label
from arena_coach.services.match_service import MatchService


class MatchReviewPanel(QWidget):
    data_changed = Signal()
    message = Signal(str)
    error = Signal(str)

    def __init__(self, service: MatchService) -> None:
        super().__init__()
        self.service = service
        self.review: Optional[Dict[str, Any]] = None
        self.player_options: List[Dict[str, Any]] = []

        self.match_combo = QComboBox()
        self.match_combo.setEditable(True)
        self.refresh_button = QPushButton("Refresh")
        self.match_summary = QLabel("no match selected")

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "match name",
                "user id",
                "session slot",
                "team",
                "existing player",
                "reviewed",
                "me",
                "guest",
                "stats",
                "suggested match",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._load_selected_player)

        self.alias_label = QLabel("none")
        self.player_combo = QComboBox()
        self.player_combo.setEditable(True)
        self.team_combo = QComboBox()
        self.team_combo.addItems(["blue", "orange", "spectator"])
        self.private_type_combo = QComboBox()
        self.private_type_combo.addItem("Not used for this match", None)
        for match_type in PRIVATE_MATCH_TYPES:
            self.private_type_combo.addItem(private_match_type_label(match_type), match_type)
        self.new_player_name = QLineEdit()
        self.map_button = QPushButton("Link Player")
        self.use_suggestion_button = QPushButton("Use Suggested Match")
        self.create_button = QPushButton("Create New Player")
        self.guest_button = QPushButton("Confirm As Guest")
        self.self_button = QPushButton("Mark As Me")
        self.team_button = QPushButton("Save Team")
        self.private_type_button = QPushButton("Save Match Type")
        self.finalize_button = QPushButton("Finalize Match")
        self.checklist = QPlainTextEdit()
        self.checklist.setReadOnly(True)

        self.refresh_button.clicked.connect(self.reload)
        self.match_combo.currentIndexChanged.connect(self._load_selected_match)
        self.map_button.clicked.connect(self._map_selected)
        self.use_suggestion_button.clicked.connect(self._apply_suggestion)
        self.create_button.clicked.connect(self._create_player)
        self.guest_button.clicked.connect(self._confirm_guest)
        self.self_button.clicked.connect(self._mark_self)
        self.team_button.clicked.connect(self._set_team)
        self.private_type_button.clicked.connect(self._set_private_type)
        self.finalize_button.clicked.connect(self._finalize)

        top = QHBoxLayout()
        top.addWidget(QLabel("Match"))
        top.addWidget(self.match_combo, 1)
        top.addWidget(self.refresh_button)

        action_box = QGroupBox("Advanced Player Actions")
        action_layout = QFormLayout(action_box)
        action_layout.addRow("Match name", self.alias_label)
        action_layout.addRow("Private match type", self.private_type_combo)
        action_layout.addRow("", self.private_type_button)
        action_layout.addRow("Existing player", self.player_combo)
        action_layout.addRow("Team", self.team_combo)
        action_layout.addRow("New player name", self.new_player_name)
        action_layout.addRow("", self.map_button)
        action_layout.addRow("", self.use_suggestion_button)
        action_layout.addRow("", self.create_button)
        action_layout.addRow("", self.guest_button)
        action_layout.addRow("", self.self_button)
        action_layout.addRow("", self.team_button)
        action_layout.addRow("", self.finalize_button)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(action_box)
        right_layout.addWidget(QGroupBox("Finalize Checklist"))
        right_layout.itemAt(1).widget().setLayout(QVBoxLayout())
        right_layout.itemAt(1).widget().layout().addWidget(self.checklist)

        splitter = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addLayout(top)
        left_layout.addWidget(self.match_summary)
        left_layout.addWidget(self.table)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)
        self.reload()

    def reload(self) -> None:
        matches = self.service.list_matches()
        current = self.current_match_id()
        self.match_combo.blockSignals(True)
        self.match_combo.clear()
        for match in matches:
            label = f"{match['display_name']} (Match {match['id']})"
            self.match_combo.addItem(label, match["id"])
        if current is not None:
            index = self.match_combo.findData(current)
            if index >= 0:
                self.match_combo.setCurrentIndex(index)
        self.match_combo.blockSignals(False)
        self.player_options = self.service.list_player_options()
        self._load_selected_match()

    def select_match(self, match_id: int) -> None:
        index = self.match_combo.findData(match_id)
        if index >= 0:
            self.match_combo.setCurrentIndex(index)
        else:
            self.reload()
            index = self.match_combo.findData(match_id)
            if index >= 0:
                self.match_combo.setCurrentIndex(index)

    def current_match_id(self) -> Optional[int]:
        value = self.match_combo.currentData()
        return int(value) if value is not None else None

    def _load_selected_match(self) -> None:
        match_id = self.current_match_id()
        if match_id is None:
            return
        try:
            self.review = self.service.get_review_data(match_id)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self._populate_table()
        self._populate_player_combo(None)
        self._populate_checklist()

    def _populate_table(self) -> None:
        if not self.review:
            return
        match = self.review["match"]
        self.match_summary.setText(
            f"{match['display_name']} | map={match['map_name'] or 'none'} | "
            f"result={match['result'] or 'unknown'} | finalized={match['finalized']}"
        )
        self.private_type_combo.setVisible(str(match.get("match_classification") or "").casefold() == "private")
        self.private_type_button.setVisible(str(match.get("match_classification") or "").casefold() == "private")
        private_type = match.get("private_match_type")
        index = self.private_type_combo.findData(private_type if private_type else "Unknown")
        if index >= 0:
            self.private_type_combo.setCurrentIndex(index)
        players = self.review["players"]
        self.table.setRowCount(len(players))
        for row_index, player in enumerate(players):
            stats = player.get("stats", {})
            suggestion = player["suggestions"][0] if player.get("suggestions") else None
            afk = " AFK?" if stats.get("afk_suspected") else ""
            values = [
                player["match_alias"],
                player["userid"],
                player["playerid"],
                player["team"] or "unknown",
                player["canonical_name"] if player["player_id"] else "unlinked",
                "yes" if player["confirmed"] else "no",
                "yes" if player["is_user"] else "no",
                "yes" if player["confirmed"] and not player["player_id"] else "no",
                f"pts={stats.get('points', 0)} g={stats.get('goals', 0)} a={stats.get('assists', 0)} s={stats.get('saves', 0)} stuns={stats.get('stuns', 0)}{afk}",
                f"{suggestion['canonical_name']} ({suggestion['reason']})" if suggestion else "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem("" if value is None else str(value))
                if column == 0:
                    item.setData(256, player["match_alias"])
                self.table.setItem(row_index, column, item)
        self.table.resizeColumnsToContents()

    def _populate_player_combo(self, selected_player_id: Optional[int]) -> None:
        self.player_combo.blockSignals(True)
        self.player_combo.clear()
        self.player_combo.addItem("Choose existing player", None)
        for player in self.player_options:
            self.player_combo.addItem(player["label"], player["id"])
            if selected_player_id == player["id"]:
                self.player_combo.setCurrentIndex(self.player_combo.count() - 1)
        self.player_combo.blockSignals(False)

    def _populate_checklist(self) -> None:
        if not self.review:
            return
        validation = self.review["validation"]
        lines = []
        for item in validation["items"]:
            marker = "OK" if item["ok"] else "NEEDS"
            lines.append(f"{marker}: {item['label']}")
            if not item["ok"]:
                lines.append(f"  {item['message']}")
        self.checklist.setPlainText("\n".join(lines))
        self.finalize_button.setEnabled(validation["can_finalize"])

    def _selected_player(self) -> Optional[Dict[str, Any]]:
        if not self.review:
            return None
        selected = self.table.selectedItems()
        if not selected:
            return None
        alias = self.table.item(selected[0].row(), 0).data(256)
        return next((player for player in self.review["players"] if player["match_alias"] == alias), None)

    def _load_selected_player(self) -> None:
        player = self._selected_player()
        if not player:
            return
        self.alias_label.setText(player["match_alias"])
        self.team_combo.setCurrentText(player["team"] if player["team"] in {"blue", "orange", "spectator"} else "spectator")
        self.new_player_name.setText(player["match_alias"])
        self._populate_player_combo(player["player_id"])

    def _selected_alias(self) -> Optional[str]:
        player = self._selected_player()
        return player["match_alias"] if player else None

    def _selected_player_id(self) -> Optional[int]:
        value = self.player_combo.currentData()
        return int(value) if value is not None else None

    def _map_selected(self) -> None:
        alias = self._selected_alias()
        player_id = self._selected_player_id()
        if not alias or player_id is None:
            self.error.emit("Select an alias and canonical player.")
            return
        try:
            self.service.map_player(self.current_match_id(), alias, player_id)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Linked player: {alias}")
        self._after_change()

    def _apply_suggestion(self) -> None:
        player = self._selected_player()
        if not player or not player.get("suggestions"):
            self.error.emit("No suggestion for selected alias.")
            return
        try:
            self.service.map_player(self.current_match_id(), player["match_alias"], int(player["suggestions"][0]["player_id"]))
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Used suggested match for {player['match_alias']}")
        self._after_change()

    def _create_player(self) -> None:
        alias = self._selected_alias()
        if not alias:
            self.error.emit("Select an alias first.")
            return
        try:
            player_id = self.service.create_player_from_alias(self.current_match_id(), alias, self.new_player_name.text() or alias)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Created new player for {alias}")
        self._after_change()

    def _confirm_guest(self) -> None:
        alias = self._selected_alias()
        if not alias:
            self.error.emit("Select an alias first.")
            return
        try:
            self.service.confirm_guest(self.current_match_id(), alias)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Confirmed guest: {alias}")
        self._after_change()

    def _mark_self(self) -> None:
        alias = self._selected_alias()
        if not alias:
            self.error.emit("Select an alias first.")
            return
        try:
            self.service.mark_self(self.current_match_id(), alias)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Marked as me: {alias}")
        self._after_change()

    def _set_team(self) -> None:
        alias = self._selected_alias()
        if not alias:
            self.error.emit("Select an alias first.")
            return
        try:
            self.service.set_team(self.current_match_id(), alias, self.team_combo.currentText())
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Set team for {alias}")
        self._after_change()

    def _finalize(self) -> None:
        match_id = self.current_match_id()
        if match_id is None:
            return
        if QMessageBox.question(self, "Finalize Match", f"Finalize Match {match_id}?") != QMessageBox.Yes:
            return
        try:
            result = self.service.finalize_match(match_id)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Finalized match #{result['match_id']} ({result['result'] or 'unknown'})")
        self._after_change()

    def _set_private_type(self) -> None:
        match_id = self.current_match_id()
        if match_id is None:
            self.error.emit("Select a match first.")
            return
        try:
            self.service.set_private_match_type(match_id, self.private_type_combo.currentData())
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit("Saved private match type")
        self._after_change()

    def finalize_current_match(self) -> None:
        self._finalize()

    def _after_change(self) -> None:
        self._load_selected_match()
        self.data_changed.emit()
