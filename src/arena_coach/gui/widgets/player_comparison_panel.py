"""Fixed-tab player comparison workspace."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCompleter,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from arena_coach.gui.widgets.card_container import CardContainer
from arena_coach.gui.widgets.category_radar_widget import (
    DualCategoryRadarWidget,
    category_scores_from_breakdown,
    overall_score_from_scores,
)
from arena_coach.gui.widgets.compact_card_list import CompactCardList
from arena_coach.gui.widgets.multi_select_menu_button import MultiSelectMenuButton
from arena_coach.match_context import PRIVATE_MATCH_TYPES, private_match_type_label
from arena_coach.services.layout_service import LayoutService
from arena_coach.services.player_comparison_service import PlayerComparisonService
from arena_coach.stats.stat_filters import StatsFilter


class PlayerComparisonPanel(QWidget):
    layout_tab_id = "player_comparison"

    message = Signal(str)
    error = Signal(str)

    def __init__(self, service: PlayerComparisonService, layout_service: LayoutService) -> None:
        super().__init__()
        self.service = service
        self.layout_service = layout_service
        self._players: list[dict[str, Any]] = []
        self._last_payload: Optional[dict[str, Any]] = None

        self.left_player_combo = QComboBox()
        self.left_player_combo.setEditable(True)
        self.left_player_combo.setMinimumContentsLength(20)
        self.left_player_combo.setInsertPolicy(QComboBox.NoInsert)
        self.right_player_combo = QComboBox()
        self.right_player_combo.setEditable(True)
        self.right_player_combo.setMinimumContentsLength(20)
        self.right_player_combo.setInsertPolicy(QComboBox.NoInsert)
        _make_combo_searchable(self.left_player_combo, "Type to search player...")
        _make_combo_searchable(self.right_player_combo, "Type to search player...")
        self.swap_button = QPushButton("Swap")
        self.refresh_players_button = QPushButton("Refresh Players")
        self.player_hint = QLabel("Type in either player box to search your saved player database.")
        self.player_hint.setWordWrap(True)
        self.player_hint.setStyleSheet("color: #7f94a8;")

        self.competitive_only = QCheckBox("Competitive only")
        self.classification_filter = MultiSelectMenuButton(all_selected_text="All match types")
        self.classification_filter.set_options(
            [
                ("public", "Public"),
                ("private", "Private"),
                ("tournament", "Tournament"),
                ("unknown", "Unknown"),
            ],
            selected_values=("private", "tournament", "unknown"),
        )
        self.private_type_filter = MultiSelectMenuButton(all_selected_text="All private types")
        self.private_type_filter.set_options(
            [(match_type, private_match_type_label(match_type)) for match_type in PRIVATE_MATCH_TYPES]
        )
        self.last_filter = QComboBox()
        self.last_filter.addItems(["All", "5", "10", "25"])
        self.scoring_mode = QComboBox()
        self.scoring_mode.addItem("Mistake Adjusted", "mistake_adjusted")
        self.scoring_mode.addItem("Production Only", "production_only")
        self.include_afk = QCheckBox("Include suspected AFK")
        self.refresh_button = QPushButton("Refresh")
        self.customize_button = QPushButton("Customize Layout")
        self.customize_button.setCheckable(True)
        self.reset_layout_button = QPushButton("Reset Layout")

        self.category_filter = QComboBox()
        for key, label in CATEGORY_OPTIONS:
            self.category_filter.addItem(label, key)

        self.overview_widget = QWidget()
        overview_shell = QHBoxLayout(self.overview_widget)
        overview_shell.setContentsMargins(0, 0, 0, 0)
        overview_shell.setSpacing(16)
        self.overview_form_widget = QWidget()
        self.overview_layout = QFormLayout(self.overview_form_widget)
        self.overview_chart = DualCategoryRadarWidget()
        overview_shell.addWidget(self.overview_form_widget, 2)
        overview_shell.addWidget(self.overview_chart, 3)
        self.score_grid = _ComparisonGrid()
        self.averages_grid = _ComparisonGrid()
        self.totals_grid = _ComparisonGrid()
        self.category_detail_grid = _ComparisonGrid()
        self.shared_matches_list = CompactCardList("No shared matches in the current filters.")

        category_detail_widget = QWidget()
        category_detail_layout = QVBoxLayout(category_detail_widget)
        category_detail_layout.setContentsMargins(0, 0, 0, 0)
        category_toolbar = QHBoxLayout()
        category_toolbar.addWidget(QLabel("Category"))
        category_toolbar.addWidget(self.category_filter)
        category_toolbar.addStretch()
        category_detail_layout.addLayout(category_toolbar)
        category_detail_layout.addWidget(self.category_detail_grid)

        self.cards = CardContainer()
        self.cards.order_changed.connect(self._save_layout_order)
        self.cards.sizes_changed.connect(self._save_layout_sizes)
        self.cards.add_card("overview", "Comparison Overview", self.overview_widget)
        self.cards.add_card("category_scores", "Advanced Category Scores", self.score_grid)
        self.cards.add_card("core_averages", "Core Averages", self.averages_grid)
        self.cards.add_card("core_totals", "Core Totals", self.totals_grid)
        self.cards.add_card("category_detail", "Advanced Category Detail", category_detail_widget)
        self.cards.add_card("shared_matches", "Shared Matches", self.shared_matches_list)

        players_row = QHBoxLayout()
        players_row.addWidget(QLabel("Left player"))
        players_row.addWidget(self.left_player_combo, 1)
        players_row.addWidget(QLabel("Right player"))
        players_row.addWidget(self.right_player_combo, 1)
        players_row.addWidget(self.swap_button)
        players_row.addWidget(self.refresh_players_button)

        players_section = QVBoxLayout()
        players_section.addLayout(players_row)
        players_section.addWidget(self.player_hint)

        filters = QHBoxLayout()
        filters.addWidget(self.competitive_only)
        filters.addWidget(QLabel("Match type"))
        filters.addWidget(self.classification_filter)
        filters.addWidget(QLabel("Private subtype"))
        filters.addWidget(self.private_type_filter)
        filters.addWidget(QLabel("Last"))
        filters.addWidget(self.last_filter)
        filters.addWidget(QLabel("Scoring"))
        filters.addWidget(self.scoring_mode)
        filters.addWidget(self.include_afk)
        filters.addStretch()
        filters.addWidget(self.reset_layout_button)
        filters.addWidget(self.customize_button)
        filters.addWidget(self.refresh_button)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.addLayout(players_section)
        content_layout.addLayout(filters)
        content_layout.addWidget(self.cards)
        content_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)

        self.left_player_combo.currentIndexChanged.connect(self.reload)
        self.right_player_combo.currentIndexChanged.connect(self.reload)
        self.competitive_only.stateChanged.connect(self.reload)
        self.classification_filter.selection_changed.connect(self._classification_filter_changed)
        self.private_type_filter.selection_changed.connect(self.reload)
        self.last_filter.currentIndexChanged.connect(self.reload)
        self.scoring_mode.currentIndexChanged.connect(self.reload)
        self.include_afk.stateChanged.connect(self.reload)
        self.category_filter.currentIndexChanged.connect(self._reload_category_detail)
        self.refresh_players_button.clicked.connect(self.reload_players)
        self.swap_button.clicked.connect(self._swap_players)
        self.refresh_button.clicked.connect(self.reload)
        self.customize_button.toggled.connect(self.set_customize_layout)
        self.reset_layout_button.clicked.connect(self._handle_reset_layout)

        self._sync_private_type_filter_enabled()
        self.reload_players()
        self.reload_saved_layout()
        self.reload()

    def set_service(self, service: PlayerComparisonService) -> None:
        self.service = service
        self.reload_players()
        self.reload()

    def reload_players(self) -> None:
        left_id = self.left_player_combo.currentData()
        right_id = self.right_player_combo.currentData()
        self._players = self.service.list_players()
        self._populate_player_combo(self.left_player_combo, left_id)
        self._populate_player_combo(self.right_player_combo, right_id)
        if self.left_player_combo.count() and self.left_player_combo.currentIndex() < 0:
            self.left_player_combo.setCurrentIndex(0)
        if self.right_player_combo.count() and self.right_player_combo.currentIndex() < 0:
            default_index = 1 if self.right_player_combo.count() > 1 else 0
            self.right_player_combo.setCurrentIndex(default_index)
        self.reload()

    def reload(self) -> None:
        left_id = self._selected_player_id(self.left_player_combo)
        right_id = self._selected_player_id(self.right_player_combo)
        if left_id is None or right_id is None:
            self._clear_payload("Select two players to compare.")
            return
        if left_id == right_id:
            self._clear_payload("Select two different players to compare.")
            return
        try:
            payload = self.service.compare(left_id, right_id, self._filters())
        except Exception as exc:
            self._clear_payload(str(exc))
            self.error.emit(str(exc))
            return
        self._last_payload = payload
        self._load_overview(payload)
        self._load_category_scores(payload)
        self._load_core_averages(payload)
        self._load_core_totals(payload)
        self._reload_category_detail()
        self._load_shared_matches(payload)

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
        self.layout_service.save_card_order(self.layout_tab_id, order, self.layout_service.active_profile_id())

    def _save_layout_sizes(self, sizes: dict[str, int]) -> None:
        self.layout_service.save_card_sizes(self.layout_tab_id, sizes, self.layout_service.active_profile_id())

    def _handle_reset_layout(self) -> None:
        self.reset_layout()
        self.set_customize_layout(False)

    def _sync_customize_button(self, enabled: bool) -> None:
        self.customize_button.blockSignals(True)
        self.customize_button.setChecked(enabled)
        self.customize_button.setText("Save Layout" if enabled else "Customize Layout")
        self.customize_button.blockSignals(False)

    def _populate_player_combo(self, combo: QComboBox, selected_id: Optional[int]) -> None:
        combo.blockSignals(True)
        combo.clear()
        for player in self._players:
            combo.addItem(player["canonical_name"], player["id"])
        if selected_id is not None:
            index = combo.findData(selected_id)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _selected_player_id(self, combo: QComboBox) -> Optional[int]:
        value = combo.currentData()
        return int(value) if value is not None else None

    def _swap_players(self) -> None:
        left_id = self._selected_player_id(self.left_player_combo)
        right_id = self._selected_player_id(self.right_player_combo)
        if left_id is None or right_id is None:
            return
        self.left_player_combo.blockSignals(True)
        self.right_player_combo.blockSignals(True)
        left_index = self.left_player_combo.findData(right_id)
        right_index = self.right_player_combo.findData(left_id)
        if left_index >= 0:
            self.left_player_combo.setCurrentIndex(left_index)
        if right_index >= 0:
            self.right_player_combo.setCurrentIndex(right_index)
        self.left_player_combo.blockSignals(False)
        self.right_player_combo.blockSignals(False)
        self.reload()

    def _classification_filter_changed(self) -> None:
        self._sync_private_type_filter_enabled()
        self.reload()

    def _sync_private_type_filter_enabled(self) -> None:
        include_private = "private" in self.classification_filter.selected_values()
        self.private_type_filter.setEnabled(include_private)

    def _filters(self) -> StatsFilter:
        selected_match_types = set(self.classification_filter.selected_values())
        last_text = self.last_filter.currentText()
        last_n = None if last_text == "All" else int(last_text)
        selected_private_types = self.private_type_filter.selected_values() if "private" in selected_match_types else []
        return StatsFilter(
            competitive_only=self.competitive_only.isChecked(),
            include_low_quality=not self.competitive_only.isChecked(),
            include_public="public" in selected_match_types,
            include_private="private" in selected_match_types,
            include_tournament="tournament" in selected_match_types,
            include_unknown="unknown" in selected_match_types,
            include_afk_players=self.include_afk.isChecked(),
            private_match_types=tuple(selected_private_types),
            last_n=last_n,
            category_scoring_mode=str(self.scoring_mode.currentData() or "mistake_adjusted"),
        )

    def _clear_payload(self, text: str) -> None:
        self._last_payload = None
        _clear_form(self.overview_layout)
        self.overview_layout.addRow("status", _label(text))
        self.overview_chart.clear()
        self.score_grid.set_rows([], "Left", "Right")
        self.averages_grid.set_rows([], "Left", "Right")
        self.totals_grid.set_rows([], "Left", "Right")
        self.category_detail_grid.set_rows([], "Left", "Right")
        self.shared_matches_list.set_items([])

    def _load_overview(self, payload: dict[str, Any]) -> None:
        _clear_form(self.overview_layout)
        left_name = str(payload["left_player"]["canonical_name"])
        right_name = str(payload["right_player"]["canonical_name"])
        shared = payload.get("shared") or {}
        left_categories = payload.get("left_advanced", {}).get("category_breakdown") or {}
        right_categories = payload.get("right_advanced", {}).get("category_breakdown") or {}
        left_scores = category_scores_from_breakdown(left_categories)
        right_scores = category_scores_from_breakdown(right_categories)
        self.overview_layout.addRow("left player", _label(left_name))
        self.overview_layout.addRow("right player", _label(right_name))
        self.overview_layout.addRow(
            f"{left_name} display rating",
            _label(_format_value(payload.get("left_advanced", {}).get("display_overall_score"))),
        )
        self.overview_layout.addRow(
            f"{right_name} display rating",
            _label(_format_value(payload.get("right_advanced", {}).get("display_overall_score"))),
        )
        self.overview_layout.addRow(
            f"{left_name} absolute rating",
            _label(_format_value(payload.get("left_advanced", {}).get("absolute_overall_score"))),
        )
        self.overview_layout.addRow(
            f"{right_name} absolute rating",
            _label(_format_value(payload.get("right_advanced", {}).get("absolute_overall_score"))),
        )
        self.overview_layout.addRow("shared matches", _label(shared.get("shared_matches", 0)))
        self.overview_layout.addRow(
            "together / opposed / mixed",
            _label(
                f"{shared.get('together_matches', 0)} / "
                f"{shared.get('opposed_matches', 0)} / "
                f"{shared.get('mixed_team_matches', 0)}"
            ),
        )
        together = shared.get("together_record") or {}
        versus = shared.get("left_vs_right_record") or {}
        self.overview_layout.addRow(
            "together record",
            _label(f"{together.get('wins', 0)}-{together.get('losses', 0)}-{together.get('ties', 0)}"),
        )
        self.overview_layout.addRow(
            f"{left_name} vs {right_name}",
            _label(f"{versus.get('wins', 0)}-{versus.get('losses', 0)}-{versus.get('ties', 0)}"),
        )
        self.overview_layout.addRow(
            "advanced baseline rows",
            _label(
                f"{payload.get('left_advanced', {}).get('competitive_baseline_sample_size', 0)} player-team rows"
            ),
        )
        self.overview_layout.addRow(
            "scoring mode",
            _label(str(payload.get("left_advanced", {}).get("category_scoring_mode") or "mistake_adjusted").replace("_", " ").title()),
        )
        warnings = list(payload.get("left_advanced", {}).get("warnings") or []) + list(
            payload.get("right_advanced", {}).get("warnings") or []
        )
        if warnings:
            self.overview_layout.addRow("warning", _warning_label(" | ".join(dict.fromkeys(warnings))))
        self.overview_chart.set_comparison(
            left_scores,
            right_scores,
            left_label="Left Player",
            right_label="Right Player",
            left_overall=payload.get("left_advanced", {}).get("display_overall_score")
            if payload.get("left_advanced", {}).get("display_overall_score") is not None
            else overall_score_from_scores(left_scores),
            right_overall=payload.get("right_advanced", {}).get("display_overall_score")
            if payload.get("right_advanced", {}).get("display_overall_score") is not None
            else overall_score_from_scores(right_scores),
            left_details=left_categories,
            right_details=right_categories,
        )

    def _load_category_scores(self, payload: dict[str, Any]) -> None:
        left_name = str(payload["left_player"]["canonical_name"])
        right_name = str(payload["right_player"]["canonical_name"])
        left_categories = payload.get("left_advanced", {}).get("category_breakdown") or {}
        right_categories = payload.get("right_advanced", {}).get("category_breakdown") or {}
        rows = []
        for key, label in CATEGORY_OPTIONS:
            left_score = _category_score(left_categories.get(key))
            right_score = _category_score(right_categories.get(key))
            rows.append(_metric_row(label, left_score, right_score))
        self.score_grid.set_rows(rows, left_name, right_name)

    def _load_core_averages(self, payload: dict[str, Any]) -> None:
        left_name = str(payload["left_player"]["canonical_name"])
        right_name = str(payload["right_player"]["canonical_name"])
        left_stats = payload.get("left_stats") or {}
        right_stats = payload.get("right_stats") or {}
        left_averages = left_stats.get("averages") or {}
        right_averages = right_stats.get("averages") or {}
        rows = [
            _metric_row("Matches", left_stats.get("matches"), right_stats.get("matches")),
            _metric_row("Win Rate", left_stats.get("win_rate"), right_stats.get("win_rate"), percent=True),
            _metric_row("Shot Efficiency", left_stats.get("shot_efficiency"), right_stats.get("shot_efficiency")),
        ]
        for key, label in SUMMARY_FIELDS:
            rows.append(_metric_row(label, left_averages.get(key), right_averages.get(key)))
        self.averages_grid.set_rows(rows, left_name, right_name)

    def _load_core_totals(self, payload: dict[str, Any]) -> None:
        left_name = str(payload["left_player"]["canonical_name"])
        right_name = str(payload["right_player"]["canonical_name"])
        left_stats = payload.get("left_stats") or {}
        right_stats = payload.get("right_stats") or {}
        left_totals = left_stats.get("totals") or {}
        right_totals = right_stats.get("totals") or {}
        rows = [
            _metric_row("Wins", left_stats.get("wins"), right_stats.get("wins")),
            _metric_row("Losses", left_stats.get("losses"), right_stats.get("losses")),
            _metric_row("Ties", left_stats.get("ties"), right_stats.get("ties")),
            _metric_row("With User", left_stats.get("with_user_matches"), right_stats.get("with_user_matches")),
            _metric_row(
                "Against User",
                left_stats.get("against_user_matches"),
                right_stats.get("against_user_matches"),
            ),
            _metric_row("AFK Matches", left_stats.get("afk_matches"), right_stats.get("afk_matches")),
        ]
        for key, label in SUMMARY_FIELDS:
            rows.append(_metric_row(label, left_totals.get(key), right_totals.get(key)))
        self.totals_grid.set_rows(rows, left_name, right_name)

    def _reload_category_detail(self) -> None:
        if not self._last_payload:
            self.category_detail_grid.set_rows([], "Left", "Right")
            return
        category_key = str(self.category_filter.currentData() or "shooting")
        left_name = str(self._last_payload["left_player"]["canonical_name"])
        right_name = str(self._last_payload["right_player"]["canonical_name"])
        left_category = (self._last_payload.get("left_advanced", {}).get("category_breakdown") or {}).get(category_key) or {}
        right_category = (self._last_payload.get("right_advanced", {}).get("category_breakdown") or {}).get(category_key) or {}
        rows = []
        left_metrics = {str(item.get("label")): item for item in left_category.get("metrics") or []}
        right_metrics = {str(item.get("label")): item for item in right_category.get("metrics") or []}
        labels = []
        for item in left_category.get("metrics") or []:
            labels.append(str(item.get("label") or ""))
        for item in right_category.get("metrics") or []:
            label = str(item.get("label") or "")
            if label not in labels:
                labels.append(label)
        if left_category or right_category:
            rows.append(
                _metric_row(
                    "Display Score",
                    _category_display_score(left_category),
                    _category_display_score(right_category),
                )
            )
            rows.append(_metric_row("Absolute Score", _category_absolute_score(left_category), _category_absolute_score(right_category)))
        for label in labels:
            left_value = _metric_display(left_metrics.get(label))
            right_value = _metric_display(right_metrics.get(label))
            rows.append({"label": label, "left": left_value, "right": right_value, "delta": ""})
        self.category_detail_grid.set_rows(rows, left_name, right_name)

    def _load_shared_matches(self, payload: dict[str, Any]) -> None:
        left_name = str(payload["left_player"]["canonical_name"])
        right_name = str(payload["right_player"]["canonical_name"])
        items = []
        for row in payload.get("shared", {}).get("recent_shared_matches") or []:
            subtitle_parts = [str(row.get("context") or "mixed").title(), str(row.get("score") or "Unknown score")]
            if row.get("private_match_type"):
                subtitle_parts.append(private_match_type_label(row["private_match_type"]))
            items.append(
                {
                    "title": row.get("display_name") or f"Match {row.get('match_id')}",
                    "subtitle": " | ".join(subtitle_parts),
                    "chips": [
                        f"{left_name}: {', '.join(str(team).title() for team in row.get('left_teams') or []) or 'Unknown'}",
                        f"{right_name}: {', '.join(str(team).title() for team in row.get('right_teams') or []) or 'Unknown'}",
                        f"Result for {left_name}: {str(row.get('result_for_left') or 'unknown').title()}",
                    ],
                }
            )
        self.shared_matches_list.set_items(items)


class _ComparisonGrid(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(8)
        self.set_rows([], "Left", "Right")

    def set_rows(self, rows: list[dict[str, Any]], left_header: str, right_header: str) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        headers = ["Metric", left_header, right_header, "Delta"]
        for column, text in enumerate(headers):
            label = QLabel(text)
            label.setStyleSheet("font-weight: 600;")
            label.setWordWrap(True)
            self.grid.addWidget(label, 0, column)
        if not rows:
            empty = QLabel("No comparison data yet.")
            empty.setWordWrap(True)
            empty.setStyleSheet("color: #7f94a8;")
            self.grid.addWidget(empty, 1, 0, 1, 4)
            self.grid.setColumnStretch(0, 2)
            self.grid.setColumnStretch(1, 2)
            self.grid.setColumnStretch(2, 2)
            self.grid.setColumnStretch(3, 1)
            return
        for row_index, row in enumerate(rows, start=1):
            self.grid.addWidget(_cell(str(row.get("label") or "")), row_index, 0)
            self.grid.addWidget(_cell(str(row.get("left") or "")), row_index, 1)
            self.grid.addWidget(_cell(str(row.get("right") or "")), row_index, 2)
            self.grid.addWidget(_cell(str(row.get("delta") or "")), row_index, 3)
        self.grid.setColumnStretch(0, 2)
        self.grid.setColumnStretch(1, 2)
        self.grid.setColumnStretch(2, 2)
        self.grid.setColumnStretch(3, 1)


CATEGORY_OPTIONS = [
    ("shooting", "Shooting"),
    ("speed", "Speed"),
    ("possession", "Possession"),
    ("offense", "Offense"),
    ("defense", "Defense"),
    ("passing", "Passing"),
]

SUMMARY_FIELDS = [
    ("points", "Points"),
    ("goals", "Goals"),
    ("assists", "Assists"),
    ("saves", "Saves"),
    ("stuns", "Stuns"),
    ("steals", "Steals"),
    ("shots", "Shots"),
    ("passes", "Passes"),
    ("catches", "Catches"),
    ("turnovers", "Turnovers"),
    ("interceptions", "Interceptions"),
    ("blocks", "Blocks"),
    ("possession_time", "Possession Time"),
]


def _clear_form(layout: QFormLayout) -> None:
    while layout.rowCount():
        layout.removeRow(0)


def _label(value: object) -> QLabel:
    label = QLabel(str(value))
    label.setWordWrap(True)
    return label


def _warning_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #ff7a7a;")
    return label


def _cell(value: str) -> QLabel:
    label = QLabel(value)
    label.setWordWrap(True)
    return label


def _category_score(category: Optional[dict[str, Any]]) -> Optional[float]:
    if not category:
        return None
    value = category.get("display_score")
    if value is None:
        value = category.get("overall_score")
    return float(value) if value is not None else None


def _category_display_score(category: Optional[dict[str, Any]]) -> Optional[float]:
    if not category:
        return None
    value = category.get("display_score")
    return float(value) if value is not None else None


def _category_absolute_score(category: Optional[dict[str, Any]]) -> Optional[float]:
    if not category:
        return None
    value = category.get("absolute_score")
    if value is None:
        value = category.get("overall_score")
    return float(value) if value is not None else None


def _metric_display(metric: Optional[dict[str, Any]]) -> str:
    if not metric:
        return "--"
    value = str(metric.get("value") or "--")
    note = str(metric.get("note") or "").strip()
    return f"{value} ({note})" if note else value


def _metric_row(label: str, left_value: Any, right_value: Any, *, percent: bool = False) -> dict[str, Any]:
    left_text = _format_value(left_value, percent=percent)
    right_text = _format_value(right_value, percent=percent)
    left_number = _maybe_number(left_value)
    right_number = _maybe_number(right_value)
    delta_text = ""
    if left_number is not None and right_number is not None:
        delta = left_number - right_number
        if abs(delta) >= 0.005:
            delta_text = f"{delta:+.2f}"
        else:
            delta_text = "Even"
    return {"label": label, "left": left_text, "right": right_text, "delta": delta_text}


def _format_value(value: Any, *, percent: bool = False) -> str:
    if value is None:
        return "--"
    number = _maybe_number(value)
    if number is None:
        return str(value)
    if percent:
        return f"{number:.2f}%"
    if abs(number - round(number)) < 0.0001:
        return str(int(round(number)))
    return f"{number:.2f}"


def _maybe_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _make_combo_searchable(combo: QComboBox, placeholder: str) -> None:
    if combo.lineEdit():
        combo.lineEdit().setPlaceholderText(placeholder)
    completer = combo.completer()
    if completer is None:
        completer = QCompleter(combo.model(), combo)
        combo.setCompleter(completer)
    completer.setCompletionMode(QCompleter.PopupCompletion)
    completer.setFilterMode(Qt.MatchContains)
