"""Guided match review workflow."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from arena_coach.match_context import PRIVATE_MATCH_TYPES, private_match_type_label
from arena_coach.services.match_service import MatchService


class GuidedMatchReviewPanel(QWidget):
    data_changed = Signal()
    message = Signal(str)
    error = Signal(str)

    def __init__(self, service: MatchService) -> None:
        super().__init__()
        self.service = service
        self.review: Optional[Dict[str, Any]] = None
        self.player_options: List[Dict[str, Any]] = []
        self.step = 0
        self.player_index = 0

        self.match_combo = QComboBox()
        self.match_combo.setEditable(True)
        self.refresh_button = QPushButton("Refresh")
        self.title = QLabel("Review Match")
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        self.body.setMaximumHeight(180)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "Player", "Team", "Status", "Stats"])
        self.table.verticalHeader().setVisible(False)

        self.selector = QComboBox()
        self.selector.setEditable(True)
        self.selector.setInsertPolicy(QComboBox.NoInsert)
        self.selector.setToolTip("Type to search, then choose a player from the dropdown.")
        self.new_player_name = QLineEdit()
        self.selector_hint = QLabel("Type to search your player database, then choose a suggested or existing player.")
        self.selector_hint.setProperty("class", "muted")
        self.selector_hint.setWordWrap(True)
        self.private_type_combo = QComboBox()
        for match_type in PRIVATE_MATCH_TYPES:
            self.private_type_combo.addItem(private_match_type_label(match_type), match_type)
        self.private_type_help = QLabel(
            "This affects filtering and future stat weighting. It does not delete or hide the match."
        )
        self.private_type_help.setWordWrap(True)
        self.private_type_help.setProperty("class", "muted")
        self.team_player_combo = QComboBox()
        self.team_choice_combo = QComboBox()
        self.team_choice_combo.addItems(["blue", "orange", "spectator"])
        self.afk_checkbox = QCheckBox("AFK / inactive")
        self.afk_warning = QLabel(
            "Arena Coach suspects this player was AFK. Uncheck only if you saw them actively playing."
        )
        self.afk_warning.setProperty("class", "error")
        self.afk_warning.setWordWrap(True)
        self.back_button = QPushButton("Back")
        self.primary_button = QPushButton("Continue")
        self.existing_button = QPushButton("Existing Player")
        self.create_button = QPushButton("Create New Player")
        self.guest_button = QPushButton("Guest/Unknown")
        self.me_button = QPushButton("This Is Me")
        self.save_team_button = QPushButton("Save Team")
        self.finalize_button = QPushButton("Finalize Match")

        top = QHBoxLayout()
        top.addWidget(QLabel("Match"))
        top.addWidget(self.match_combo, 1)
        top.addWidget(self.refresh_button)

        actions = QHBoxLayout()
        for button in (
            self.back_button,
            self.primary_button,
            self.me_button,
            self.existing_button,
            self.create_button,
            self.guest_button,
            self.save_team_button,
            self.finalize_button,
        ):
            actions.addWidget(button)

        self.controls = QGroupBox("Choice")
        controls_layout = QFormLayout(self.controls)
        self.selector_label = QLabel("Detected / existing player")
        self.selector_hint_label = QLabel("")
        self.new_player_label = QLabel("New player name")
        self.private_type_label = QLabel("Private match type")
        self.private_type_help_label = QLabel("")
        self.team_player_label = QLabel("Team player")
        self.team_choice_label = QLabel("Team")
        self.afk_label = QLabel("")
        self.afk_warning_label = QLabel("")
        controls_layout.addRow(self.selector_label, self.selector)
        controls_layout.addRow(self.selector_hint_label, self.selector_hint)
        controls_layout.addRow(self.new_player_label, self.new_player_name)
        controls_layout.addRow(self.private_type_label, self.private_type_combo)
        controls_layout.addRow(self.private_type_help_label, self.private_type_help)
        controls_layout.addRow(self.afk_label, self.afk_checkbox)
        controls_layout.addRow(self.afk_warning_label, self.afk_warning)
        controls_layout.addRow(self.team_player_label, self.team_player_combo)
        controls_layout.addRow(self.team_choice_label, self.team_choice_combo)

        self.review_box = QGroupBox("Match Summary")
        review_layout = QVBoxLayout(self.review_box)
        review_layout.addWidget(self.title)
        review_layout.addWidget(self.body)
        review_layout.addWidget(self.controls)
        review_layout.addLayout(actions)

        self.finalize_overview = QWidget()
        overview_layout = QHBoxLayout(self.finalize_overview)
        self.blue_box = QGroupBox("Blue Team")
        self.orange_box = QGroupBox("Orange Team")
        self.blue_table = _team_table()
        self.orange_table = _team_table()
        blue_layout = QVBoxLayout(self.blue_box)
        orange_layout = QVBoxLayout(self.orange_box)
        blue_layout.addWidget(self.blue_table)
        orange_layout.addWidget(self.orange_table)
        overview_layout.addWidget(self.blue_box)
        overview_layout.addWidget(self.orange_box)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.review_box)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.finalize_overview, 1)

        self.refresh_button.clicked.connect(self.reload)
        self.match_combo.currentIndexChanged.connect(self._load_selected_match)
        self.back_button.clicked.connect(self._back)
        self.primary_button.clicked.connect(self._continue)
        self.me_button.clicked.connect(self._mark_me)
        self.existing_button.clicked.connect(self._link_existing)
        self.create_button.clicked.connect(self._create_player)
        self.guest_button.clicked.connect(self._confirm_guest)
        self.team_player_combo.currentIndexChanged.connect(self._team_player_changed)
        self.save_team_button.clicked.connect(self._save_team)
        self.finalize_button.clicked.connect(self._finalize)
        self.reload()

    def reload(self) -> None:
        current = self.current_match_id()
        matches = self.service.list_matches()
        self.match_combo.blockSignals(True)
        self.match_combo.clear()
        for match in matches:
            self.match_combo.addItem(match["display_name"], match["id"])
        if current is not None:
            index = self.match_combo.findData(current)
            if index >= 0:
                self.match_combo.setCurrentIndex(index)
        self.match_combo.blockSignals(False)
        self.player_options = self.service.list_player_options()
        self._load_selected_match()

    def select_match(self, match_id: int) -> None:
        index = self.match_combo.findData(match_id)
        if index < 0:
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
        self.step = 0
        self.player_index = 0
        self._render()

    def _render(self) -> None:
        if not self.review:
            return
        self._sync_private_match_type()
        self._populate_selector()
        self._populate_team_selector()
        self._populate_table()
        self._populate_finalize_overview()
        self._set_buttons()
        renderers = [self._summary_text, self._self_text, self._player_text, self._team_text, self._checklist_text]
        text = renderers[self.step]()
        self.title.setText(text.splitlines()[0] if text else "Review Match")
        self.body.setPlainText(text)
        self.review_box.setTitle(text.splitlines()[0] if text else "Review Match")
        self.table.setVisible(self.step != 4)
        self.finalize_overview.setVisible(self.step == 4)

    def _set_buttons(self) -> None:
        self.back_button.setEnabled(self.step > 0)
        self.primary_button.setVisible(self.step in {0, 1, 3})
        self.me_button.setVisible(self.step == 1)
        self.existing_button.setVisible(self.step == 2)
        self.create_button.setVisible(self.step == 2)
        self.guest_button.setVisible(self.step == 2)
        self.save_team_button.setVisible(self.step == 3)
        self.finalize_button.setVisible(self.step == 4)
        self.controls.setVisible(self.step in {0, 1, 2, 3} and self._controls_needed())
        self._set_control_visible(self.selector_label, self.selector, self.step in {1, 2})
        self._set_control_visible(self.selector_hint_label, self.selector_hint, self.step in {1, 2})
        self._set_control_visible(self.new_player_label, self.new_player_name, self.step == 2)
        self._set_control_visible(self.private_type_label, self.private_type_combo, self.step == 0 and self._is_private_match())
        self._set_control_visible(self.private_type_help_label, self.private_type_help, self.step == 0 and self._is_private_match())
        self._set_control_visible(self.afk_label, self.afk_checkbox, self.step == 2)
        self._set_control_visible(self.afk_warning_label, self.afk_warning, self.step == 2 and self.afk_warning.isVisible())
        self._set_control_visible(self.team_player_label, self.team_player_combo, self.step == 3)
        self._set_control_visible(self.team_choice_label, self.team_choice_combo, self.step == 3)

    def _populate_selector(self) -> None:
        self.selector.clear()
        if self.step == 1:
            self.selector_label.setText("Detected player")
            self.selector_hint.setText("Type to search detected players, then choose yourself.")
            for player in self.review["players"]:
                label = f"{player['match_alias']} ({player['team']})"
                self.selector.addItem(label, player["match_alias"])
            suggestion = self._self_suggestion()
            if suggestion:
                index = self.selector.findData(suggestion["match_alias"])
                if index >= 0:
                    self.selector.setCurrentIndex(index)
        elif self.step == 2:
            self.selector_label.setText("Detected / existing player")
            self.selector_hint.setText(
                "Suggested matches are listed first. Type any part of a name to search a large player database."
            )
            self.selector.addItem("Type to search existing players", None)
            current = self._current_review_player()
            suggested_id = None
            self.new_player_name.setText(current["match_alias"] if current else "")
            self.afk_checkbox.setChecked(bool(current and current.get("stats", {}).get("afk_suspected")))
            self.afk_warning.setVisible(bool(current and current.get("stats", {}).get("afk_suspected")))
            suggested_ids = set()
            if current:
                for suggestion in current.get("suggestions") or []:
                    player_id = int(suggestion["player_id"])
                    if suggested_id is None:
                        suggested_id = player_id
                    suggested_ids.add(player_id)
                    self.selector.addItem(
                        f"Suggested: {suggestion['canonical_name']} - {suggestion['reason']}",
                        player_id,
                    )
                if suggested_ids:
                    self.selector.insertSeparator(self.selector.count())
            for player in self.player_options:
                if player["id"] in suggested_ids:
                    continue
                self.selector.addItem(player["label"], player["id"])
                if player["id"] == suggested_id:
                    self.selector.setCurrentIndex(self.selector.count() - 1)
            if suggested_id is not None:
                index = self.selector.findData(suggested_id)
                if index >= 0:
                    self.selector.setCurrentIndex(index)
        _make_combo_searchable(self.selector)

    def _populate_team_selector(self) -> None:
        current_alias = self.team_player_combo.currentData()
        self.team_player_combo.blockSignals(True)
        self.team_player_combo.clear()
        if self.review:
            for player in self.review["players"]:
                self.team_player_combo.addItem(player["match_alias"], player["match_alias"])
        if current_alias:
            index = self.team_player_combo.findData(current_alias)
            if index >= 0:
                self.team_player_combo.setCurrentIndex(index)
        self.team_player_combo.blockSignals(False)
        self._team_player_changed()

    def _populate_table(self) -> None:
        players = self.review["players"] if self.review else []
        self.table.setRowCount(len(players))
        for row, player in enumerate(players):
            stats = player.get("stats", {})
            reviewed = "Me" if player["is_user"] else "Linked" if player["player_id"] else "Guest" if player["confirmed"] else "Needs review"
            stat_text = _stats_text(stats)
            if stats.get("afk_suspected"):
                stat_text += " AFK?"
            values = [row + 1, player["match_alias"], player["team"] or "unknown", reviewed, stat_text]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()

    def _populate_finalize_overview(self) -> None:
        if not self.review:
            return
        match = self.review["match"]
        self.blue_box.setTitle(f"Blue Team - {match['blue_score']}")
        self.orange_box.setTitle(f"Orange Team - {match['orange_score']}")
        self._populate_team_table(self.blue_table, "blue")
        self._populate_team_table(self.orange_table, "orange")

    def _populate_team_table(self, table: QTableWidget, team: str) -> None:
        players = [player for player in self.review["players"] if player["team"] == team]
        table.setRowCount(len(players))
        for row, player in enumerate(players):
            stats = player.get("stats", {})
            values = [
                player.get("canonical_name") or player["match_alias"],
                player.get("userid") or "unknown",
                player.get("canonical_name") or ("Guest/Unknown" if player.get("confirmed") else "Not reviewed"),
                _stats_text(stats) + (" AFK?" if stats.get("afk_suspected") else ""),
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value)))
        table.resizeColumnsToContents()

    def _summary_text(self) -> str:
        match = self.review["match"]
        quality = self.review["quality"]
        lines = [
            "Match Summary",
            "",
            match["display_name"],
            f"Map: {match['map_name'] or 'unknown'}",
            f"Type: {match.get('match_classification') or 'Unknown'}",
            f"Date/time: {match['started_at'] or match['created_at'] or 'unknown'}",
            f"Score: Blue {match['blue_score']} / Orange {match['orange_score']}",
            f"Players detected: {len(self.review['players'])}",
            f"Active non-AFK players: {quality['active_non_afk_count']}",
        ]
        if self._is_private_match():
            lines.insert(5, f"Private match type: {private_match_type_label(match.get('private_match_type'))}")
        if quality["warning"]:
            lines.extend(["", quality["warning"]])
        if quality["suspected_afk_players"]:
            lines.append("Suspected AFK: " + ", ".join(quality["suspected_afk_players"]))
        lines.extend(["", "Press Continue if the summary is correct."])
        return "\n".join(lines)

    def _self_text(self) -> str:
        suggestion = self._self_suggestion()
        lines = ["Identify Yourself", ""]
        if suggestion:
            lines.append(f"Suggested: {suggestion['match_alias']}")
            lines.append("Reason: active profile Echo name or known user ID matched.")
        else:
            lines.append("No confident self suggestion was found.")
            lines.append("Select which detected player was you.")
        lines.append("")
        lines.append("If your known user ID was not found, selecting yourself here will save this match user ID to your local identity once linked.")
        return "\n".join(lines)

    def _player_text(self) -> str:
        player = self._current_review_player()
        if not player:
            return "All players reviewed."
        stats = player.get("stats", {})
        suggestion = player["suggestions"][0] if player.get("suggestions") else None
        lines = [
            "Assigning Players",
            "",
            f"Player {self.player_index + 1} of {len(self.review['players'])}",
            f"Match name: {player['match_alias']}",
            f"User ID: {player['userid'] or 'unknown'}",
            f"Team: {player['team'] or 'unknown'}",
            f"Stats: pts={stats.get('points', 0)} goals={stats.get('goals', 0)} assists={stats.get('assists', 0)} saves={stats.get('saves', 0)} stuns={stats.get('stuns', 0)}",
        ]
        if suggestion:
            lines.append(f"Suggested Match: {suggestion['canonical_name']} ({suggestion['reason']})")
        else:
            lines.append("Suggested Match: no match found")
        lines.append("")
        lines.append("Use Guest/Unknown only if you truly cannot identify this player or do not want them saved to your local player database.")
        return "\n".join(lines)

    def _team_text(self) -> str:
        lines = ["Team Confirmation", ""]
        for team in ("blue", "orange", "spectator"):
            names = [p["match_alias"] for p in self.review["players"] if p["team"] == team]
            lines.append(f"{team.title()}: {', '.join(names) if names else 'none'}")
        lines.append("")
        lines.append("Select a player below, choose the correct team, then Save Team if anything is wrong.")
        return "\n".join(lines)

    def _checklist_text(self) -> str:
        validation = self.service.validate_finalize(self.current_match_id())
        match = self.review["match"]
        lines = [
            "Finalize Checklist",
            "",
            f"Score: Blue {match['blue_score']} / Orange {match['orange_score']}",
        ]
        if self._is_private_match():
            lines.append(f"Private match type: {private_match_type_label(match.get('private_match_type'))}")
        for item in validation["items"]:
            lines.append(("OK: " if item["ok"] else "Needs attention: ") + item["label"])
            if not item["ok"]:
                lines.append("  " + item["message"])
        quality = self.review["quality"]
        if quality["warning"]:
            lines.extend(["", quality["warning"]])
        spectators = [player["match_alias"] for player in self.review["players"] if player["team"] == "spectator"]
        if spectators:
            lines.append("Spectators: " + ", ".join(spectators))
        return "\n".join(lines)

    def _self_suggestion(self) -> Optional[Dict[str, Any]]:
        return next((player for player in self.review["players"] if player.get("self_suggestion")), None)

    def _current_review_player(self) -> Optional[Dict[str, Any]]:
        players = self.review["players"]
        while self.player_index < len(players):
            player = players[self.player_index]
            if player["is_user"] or player["confirmed"]:
                self.player_index += 1
                continue
            return player
        return None

    def _continue(self) -> None:
        if self.step == 0:
            try:
                self._save_private_match_type()
            except Exception as exc:
                self.error.emit(str(exc))
                return
        if self.step == 1:
            self._mark_me()
            return
        if self.step < 4:
            self.step += 1
        self._render()

    def _back(self) -> None:
        if self.step > 0:
            self.step -= 1
        self._render()

    def _mark_me(self) -> None:
        alias = self.selector.currentData()
        if not alias:
            self.error.emit("Select yourself first.")
            return
        selected = next((player for player in self.review["players"] if player["match_alias"] == alias), None)
        try:
            if selected and selected["player_id"] is None:
                if selected.get("suggestions"):
                    self.service.map_player(self.current_match_id(), alias, int(selected["suggestions"][0]["player_id"]))
                else:
                    active = self.review.get("active_profile") or {}
                    canonical_name = active.get("display_name") or active.get("primary_echo_name") or alias
                    self.service.create_player_from_alias(self.current_match_id(), alias, canonical_name)
            self.service.mark_self(self.current_match_id(), alias)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Marked as me: {alias}")
        self.step = 2
        self._reload_keep_step()

    def _link_existing(self) -> None:
        player = self._current_review_player()
        player_id = self.selector.currentData()
        if not player or player_id is None:
            self.error.emit("Choose an existing player.")
            return
        try:
            self._save_current_afk(player)
            self.service.map_player(self.current_match_id(), player["match_alias"], int(player_id))
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Linked player: {player['match_alias']}")
        self.player_index += 1
        self._reload_keep_step()

    def _team_player_changed(self) -> None:
        alias = self.team_player_combo.currentData()
        if not self.review or not alias:
            return
        player = next((row for row in self.review["players"] if row["match_alias"] == alias), None)
        team = player.get("team") if player else None
        if team in {"blue", "orange", "spectator"}:
            self.team_choice_combo.setCurrentText(team)

    def _save_team(self) -> None:
        alias = self.team_player_combo.currentData()
        if not alias:
            self.error.emit("Choose a player first.")
            return
        try:
            self.service.set_team(self.current_match_id(), alias, self.team_choice_combo.currentText())
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Saved team: {alias}")
        self._reload_keep_step()

    def _create_player(self) -> None:
        player = self._current_review_player()
        if not player:
            return
        try:
            self._save_current_afk(player)
            self.service.create_player_from_alias(self.current_match_id(), player["match_alias"], self.new_player_name.text() or player["match_alias"])
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Created player: {player['match_alias']}")
        self.player_index += 1
        self._reload_keep_step()

    def _confirm_guest(self) -> None:
        player = self._current_review_player()
        if not player:
            return
        try:
            self._save_current_afk(player)
            self.service.confirm_guest(self.current_match_id(), player["match_alias"])
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Confirmed guest: {player['match_alias']}")
        self.player_index += 1
        self._reload_keep_step()

    def _finalize(self) -> None:
        match_id = self.current_match_id()
        if match_id is None:
            return
        if QMessageBox.question(self, "Finalize Match", "Save this reviewed match?") != QMessageBox.Yes:
            return
        try:
            self._save_private_match_type()
            result = self.service.finalize_match(match_id)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Finalized match #{result['match_id']}")
        self.data_changed.emit()
        self._reload_keep_step()

    def finalize_current_match(self) -> None:
        self._finalize()

    def _save_current_afk(self, player: Dict[str, Any]) -> None:
        self.service.set_afk_suspected(
            self.current_match_id(),
            player["match_alias"],
            self.afk_checkbox.isChecked(),
        )

    def _reload_keep_step(self) -> None:
        match_id = self.current_match_id()
        self.review = self.service.get_review_data(match_id)
        if self.step == 2 and self._current_review_player() is None:
            self.step = 3
        self.player_options = self.service.list_player_options()
        self._render()

    def _controls_needed(self) -> bool:
        return self.step in {1, 2, 3} or (self.step == 0 and self._is_private_match())

    def _is_private_match(self) -> bool:
        return bool(self.review and str(self.review["match"].get("match_classification") or "").casefold() == "private")

    def _save_private_match_type(self) -> None:
        if not self._is_private_match():
            return
        match_id = self.current_match_id()
        if match_id is None:
            return
        selected = self.private_type_combo.currentData()
        self.service.set_private_match_type(match_id, str(selected) if selected else "Unknown")
        self.review = self.service.get_review_data(match_id)

    def _sync_private_match_type(self) -> None:
        if not self.review:
            return
        current = self.review["match"].get("private_match_type") or "Unknown"
        index = self.private_type_combo.findData(current)
        if index >= 0:
            self.private_type_combo.setCurrentIndex(index)

    @staticmethod
    def _set_control_visible(label: QLabel, widget: QWidget, visible: bool) -> None:
        label.setVisible(visible)
        widget.setVisible(visible)


def _team_table() -> QTableWidget:
    table = QTableWidget(0, 4)
    table.setHorizontalHeaderLabels(["Player", "User ID", "Assigned DB Player", "Stats"])
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    return table


def _stats_text(stats: Dict[str, Any]) -> str:
    return (
        f"pts={stats.get('points', 0)} "
        f"g={stats.get('goals', 0)} "
        f"a={stats.get('assists', 0)} "
        f"saves={stats.get('saves', 0)} "
        f"stuns={stats.get('stuns', 0)}"
    )


def _make_combo_searchable(combo: QComboBox) -> None:
    if combo.lineEdit():
        combo.lineEdit().setPlaceholderText("Type to search...")
    completer = combo.completer()
    if completer is None:
        completer = QCompleter(combo.model(), combo)
        combo.setCompleter(completer)
    completer.setCompletionMode(QCompleter.PopupCompletion)
    completer.setFilterMode(Qt.MatchContains)
