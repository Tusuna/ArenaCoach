"""Match history browser."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from arena_coach.gui.widgets.card_container import CardContainer
from arena_coach.gui.widgets.compact_card_list import CompactCardList, TeamScoreboardCard
from arena_coach.gui.widgets.advanced_analysis_panel import AdvancedAnalysisPanel
from arena_coach.gui.widgets.event_timeline_panel import EventTimelinePanel
from arena_coach.gui.widgets.multi_select_menu_button import MultiSelectMenuButton
from arena_coach.match_context import private_match_type_label
from arena_coach.services.advanced_analysis_service import AdvancedAnalysisService
from arena_coach.services.layout_service import LayoutService
from arena_coach.services.match_service import MatchService


class MatchHistoryPanel(QWidget):
    review_match_requested = Signal(int)
    message = Signal(str)
    error = Signal(str)

    layout_tab_id = "match_history"

    def __init__(
        self,
        service: MatchService,
        layout_service: LayoutService,
        quality_service: Optional[Any] = None,
        advanced_service: Optional[AdvancedAnalysisService] = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.layout_service = layout_service
        self.quality_service = quality_service
        self.advanced_service = advanced_service
        self.rows: List[Dict[str, Any]] = []
        self.current_detail: Optional[Dict[str, Any]] = None

        self.finalized_filter = MultiSelectMenuButton(all_selected_text="All statuses")
        self.finalized_filter.set_options([("finalized", "Finalized"), ("unfinalized", "Unreviewed")])
        self.result_filter = MultiSelectMenuButton(all_selected_text="All results")
        self.result_filter.set_options(
            [("win", "Win"), ("loss", "Loss"), ("tie", "Tie"), ("unknown", "Unknown")]
        )
        self.map_filter = MultiSelectMenuButton(all_selected_text="All maps")
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("search matches")
        self.refresh_button = QPushButton("Refresh")
        self.customize_button = QPushButton("Customize Layout")
        self.customize_button.setCheckable(True)
        self.reset_layout_button = QPushButton("Reset Layout")

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(["match", "id", "type", "map", "blue", "orange", "team", "result", "status", "quality"])
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)

        self.detail_summary = QLabel("Select a match to view details.")
        self.detail_summary.setWordWrap(True)
        self.roster_toggle = QCheckBox("Show roster/no-stat rows")
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.timeline = EventTimelinePanel()
        self.rounds_list = CompactCardList("No round breakdown available.")
        self.blue_scoreboard = TeamScoreboardCard("BLUE TEAM", "#7ce7ff")
        self.orange_scoreboard = TeamScoreboardCard("ORANGE TEAM", "#ffb347")
        self.advanced_panel = AdvancedAnalysisPanel(advanced_service) if advanced_service is not None else None

        self.cards = CardContainer()
        self.cards.order_changed.connect(self._save_layout_order)
        self.cards.sizes_changed.connect(self._save_layout_sizes)
        self.cards.add_card("match_list", "Match List", self._build_match_list_card())
        self.cards.add_card("match_detail", "Match Detail", self._build_match_detail_card())
        self.cards.add_card("blue_team", "Blue Team", self.blue_scoreboard)
        self.cards.add_card("orange_team", "Orange Team", self.orange_scoreboard)
        self.cards.add_card("round_summary", "Round Summary", self.rounds_list)
        self.cards.add_card("event_timeline", "Event Timeline", self.timeline)
        if self.advanced_panel is not None:
            self.cards.add_card("advanced_analysis", "Advanced Analysis", self.advanced_panel)

        for widget in (self.finalized_filter, self.result_filter, self.map_filter):
            widget.selection_changed.connect(self.reload)
        self.search_box.textChanged.connect(self.reload)
        self.refresh_button.clicked.connect(self.reload)
        self.customize_button.toggled.connect(self.set_customize_layout)
        self.reset_layout_button.clicked.connect(self._handle_reset_layout)
        self.table.itemSelectionChanged.connect(self._load_selected_detail)
        self.table.itemDoubleClicked.connect(lambda _: self._emit_review_selected())
        self.roster_toggle.toggled.connect(self._reload_detail_views)
        if self.advanced_panel is not None:
            self.advanced_panel.message.connect(self.message)
            self.advanced_panel.error.connect(self.error)

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
        self.reload_maps()
        self.reload_saved_layout()
        self.reload()

    def set_advanced_service(self, service: Optional[AdvancedAnalysisService]) -> None:
        self.advanced_service = service
        if self.advanced_panel is not None and service is not None:
            self.advanced_panel.service = service

    def reload_maps(self) -> None:
        current = self.map_filter.selected_values()
        self.map_filter.blockSignals(True)
        self.map_filter.set_options([(map_name, map_name) for map_name in self.service.list_maps()], selected_values=current)
        self.map_filter.blockSignals(False)

    def reload(self) -> None:
        self.rows = self.service.list_matches(
            finalized=self.finalized_filter.selected_values(),
            result=self.result_filter.selected_values(),
            map_name=self.map_filter.selected_values(),
            search=self.search_box.text(),
        )
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.rows))
        for row_index, match in enumerate(self.rows):
            quality_label = self._quality_label(match["id"])
            values = [
                match["display_name"],
                match["id"],
                match.get("match_classification") or "Unknown",
                match["map_name"],
                match["blue_score"],
                match["orange_score"],
                match["user_team"],
                match["result"] or "unknown",
                "Finalized" if match["finalized"] else "Unreviewed",
                quality_label,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem("" if value is None else str(value))
                if column == 1:
                    item.setData(256, match["id"])
                self.table.setItem(row_index, column, item)
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)

    def selected_match_id(self) -> Optional[int]:
        selected = self.table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        item = self.table.item(row, 1)
        value = item.data(256) if item else None
        return int(value) if value is not None else None

    def select_match(self, match_id: int) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item and item.data(256) == match_id:
                self.table.selectRow(row)
                break

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

    def _emit_review_selected(self) -> None:
        match_id = self.selected_match_id()
        if match_id is not None:
            self.review_match_requested.emit(match_id)

    def _load_selected_detail(self) -> None:
        match_id = self.selected_match_id()
        if match_id is None:
            return
        try:
            self.current_detail = self.service.get_match_detail(match_id)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self._reload_detail_views()

    def _reload_detail_views(self) -> None:
        detail = self.current_detail
        if not detail:
            return
        match = detail["match"]
        quality = self._quality_payload(match["id"])
        self.detail_summary.setText(_detail_summary_text(match, quality, detail.get("quality") or {}))

        lines = [
            match["display_name"],
            f"match id: {match['id']}",
            f"type: {_match_type_text(match)}",
            f"map: {match['map_name'] or 'none'}",
            f"points: blue={match['blue_score']} orange={match['orange_score']}",
            f"result: {match['result'] or 'unknown'}",
            f"user team: {match['user_team'] or 'none'}",
            f"finalized: {match['finalized']}",
            f"quality: {quality.get('quality_label', 'Unknown')}",
            f"match log: {match['raw_log_path']}",
        ]
        if quality.get("quality_reasons"):
            lines.append("quality reasons: " + ", ".join(quality["quality_reasons"]))
        if match.get("round_warning"):
            lines.append("warning: " + str(match["round_warning"]))
        lines.extend(["", "rosters:"])
        for player in detail["players"]:
            lines.append(
                f"  {player['team'] or 'unknown'} | {player['match_alias']} | mapped={player['canonical_name'] or 'guest'} | confirmed={player['confirmed']} | self={player['is_user']}"
            )
        lines.append("")
        lines.append("event counts:")
        lines.extend(f"  {key}: {value}" for key, value in detail["event_counts"].items())
        self.detail.setPlainText("\n".join(lines))

        self._load_scoreboard(detail)
        self._load_rounds(match)
        self.timeline.load_events(detail["events"])
        if self.advanced_panel is not None:
            self.advanced_panel.set_match(match["id"], detail["players"])

    def _quality_payload(self, match_id: int) -> Dict[str, Any]:
        if self.quality_service is None:
            return {}
        try:
            return self.quality_service.quality_for_match(match_id)
        except Exception:
            return {}

    def _quality_label(self, match_id: int) -> str:
        return str(self._quality_payload(match_id).get("quality_label") or "Unknown")

    def _load_scoreboard(self, detail: Dict[str, Any]) -> None:
        match = detail["match"]
        header_totals = detail["scoreboards"].get("header_totals") or {}
        self.blue_scoreboard.set_title(f"BLUE TEAM - {header_totals.get('blue', match['blue_score'])}")
        self.orange_scoreboard.set_title(f"ORANGE TEAM - {header_totals.get('orange', match['orange_score'])}")
        round_details = detail["scoreboards"].get("round_details") or {}
        self.blue_scoreboard.set_details(str(round_details.get("blue") or ""))
        self.orange_scoreboard.set_details(str(round_details.get("orange") or ""))
        show_roster_rows = self.roster_toggle.isChecked()
        blue_rows = _scoreboard_items(detail["scoreboards"].get("blue") or [], show_roster_rows)
        orange_rows = _scoreboard_items(detail["scoreboards"].get("orange") or [], show_roster_rows)
        self.blue_scoreboard.set_rows(blue_rows)
        self.orange_scoreboard.set_rows(orange_rows)

    def _load_rounds(self, match: Dict[str, Any]) -> None:
        rows = []
        for round_item in match.get("round_summary") or []:
            winner = str(round_item.get("winner") or "unknown").title()
            rows.append(
                {
                    "title": f"Round {round_item.get('round', '?')}: Blue {round_item.get('blue_points', 0)} - Orange {round_item.get('orange_points', 0)}",
                    "chips": [f"Winner {winner}", f"Confidence {round_item.get('confidence', 'unknown')}"],
                }
            )
        self.rounds_list.set_items(rows)

    def _build_match_list_card(self) -> QWidget:
        widget = QWidget()
        filters = QHBoxLayout()
        filters.addWidget(QLabel("status"))
        filters.addWidget(self.finalized_filter)
        filters.addWidget(QLabel("result"))
        filters.addWidget(self.result_filter)
        filters.addWidget(QLabel("maps"))
        filters.addWidget(self.map_filter)
        filters.addWidget(self.search_box, 1)
        filters.addWidget(self.refresh_button)

        layout = QVBoxLayout(widget)
        layout.addLayout(filters)
        layout.addWidget(self.table)
        return widget

    def _build_match_detail_card(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(self.detail_summary)
        layout.addWidget(self.roster_toggle)
        layout.addWidget(self.detail)
        return widget


def _scoreboard_items(rows: List[Dict[str, Any]], show_roster_rows: bool) -> List[Dict[str, Any]]:
    items = []
    for row in rows:
        if not show_roster_rows and row.get("suppressed_default"):
            continue
        name = row.get("display_name") or row.get("canonical_name") or row.get("match_alias")
        advanced = row.get("advanced_stats") or {}
        pass_count = int(advanced.get("completed_passes") or row.get("passes") or 0)
        base_chips = [
            f"Pts {int(row.get('points') or 0)}",
            f"G {int(row.get('goals') or 0)}",
            f"Ast {int(row.get('assists') or 0)}",
            f"Saves {int(row.get('saves') or 0)}",
            f"Stuns {int(row.get('stuns') or 0)}",
        ]
        extra_chips = [
            f"Pass {pass_count}",
            f"TO {int(advanced.get('turnovers') or row.get('turnovers') or 0)}",
            f"Int {int(advanced.get('interceptions') or row.get('interceptions') or 0)}",
            f"Clr {int(advanced.get('clears') or 0)}",
            f"Miss {int(advanced.get('missed_shots') or 0)}",
            f"ShotSv {int(advanced.get('shots_saved') or 0)}",
            (
                f"Sh% {float(advanced.get('shooting_percentage')):.1f}%"
                if advanced.get("shooting_percentage") is not None
                else "Sh% --"
            ),
        ]
        transition_chips = [
            f"ToDef {float(advanced.get('avg_time_to_defense')):.2f}s"
            if advanced.get("avg_time_to_defense") is not None
            else "ToDef --",
            f"ToOff {float(advanced.get('avg_time_to_offense')):.2f}s"
            if advanced.get("avg_time_to_offense") is not None
            else "ToOff --",
        ]
        subtitle_parts = []
        if row.get("userid"):
            subtitle_parts.append(f"User ID {row['userid']}")
        if row.get("player_id") is None:
            subtitle_parts.append("Guest / Unknown")
        elif row.get("match_alias") and row.get("canonical_name") and row["match_alias"] != row["canonical_name"]:
            subtitle_parts.append(f"Match alias {row['match_alias']}")
        warning_parts = []
        if row.get("afk_suspected"):
            warning_parts.append("Suspected AFK")
        if row.get("suppressed_default"):
            warning_parts.append("Roster-only row")
        items.append(
            {
                "title": str(name),
                "subtitle": " | ".join(subtitle_parts),
                "chip_rows": [base_chips, extra_chips, transition_chips],
                "warning": " | ".join(warning_parts),
            }
        )
    return items


def _detail_summary_text(match: Dict[str, Any], quality: Dict[str, Any], detail_quality: Dict[str, Any]) -> str:
    parts = [
        match["display_name"],
        f"Type: {_match_type_text(match)}",
        f"Map: {match['map_name'] or 'none'}",
        f"Points: Blue {match['blue_score']} - Orange {match['orange_score']}",
    ]
    if int(match.get("total_rounds_played") or 0) > 1 or int(match.get("blue_round_wins") or 0) + int(match.get("orange_round_wins") or 0) > 1:
        parts.append(
            f"Rounds: Blue {int(match.get('blue_round_wins') or 0)} - Orange {int(match.get('orange_round_wins') or 0)}"
        )
        parts.append(f"Total rounds: {int(match.get('total_rounds_played') or 0)}")
        parts.append(f"Points carry over: {_yes_no_unknown(match.get('points_carry_over'))}")
    parts.append(f"Result: {match['result'] or 'unknown'}")
    parts.append(f"Quality: {quality.get('quality_label', 'Unknown')}")
    warning = detail_quality.get("warning") or match.get("round_warning")
    if warning:
        parts.append(str(warning))
    team_switches = detail_quality.get("team_switch_aliases") or []
    if team_switches:
        parts.append("Team switch affected: " + ", ".join(team_switches))
    return "\n".join(parts)


def _match_type_text(match: Dict[str, Any]) -> str:
    classification = str(match.get("match_classification") or "Unknown")
    if classification.casefold() != "private":
        return classification
    return f"{classification} {private_match_type_label(match.get('private_match_type'))}"


def _yes_no_unknown(value: Any) -> str:
    if value is None:
        return "Unknown"
    return "Yes" if bool(value) else "No"
