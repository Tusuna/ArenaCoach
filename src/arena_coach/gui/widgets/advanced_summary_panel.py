"""Top-level local-user advanced summary tab."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from arena_coach.gui.widgets.card_container import CardContainer
from arena_coach.gui.widgets.category_radar_widget import (
    CategoryRadarWidget,
    category_scores_from_breakdown,
    overall_score_from_scores,
)
from arena_coach.gui.widgets.compact_card_list import CompactCardList
from arena_coach.gui.widgets.confidence_filter_widget import ConfidenceFilterWidget
from arena_coach.gui.widgets.multi_select_menu_button import MultiSelectMenuButton
from arena_coach.match_context import PRIVATE_MATCH_TYPES, private_match_type_label
from arena_coach.services.advanced_analysis_service import AdvancedAnalysisService
from arena_coach.stats.stat_filters import StatsFilter


SUMMARY_EVENT_LABELS = {
    "turnover": "Turnovers",
    "interception": "Interceptions",
    "missed_shot": "Shots Missed",
    "shot_saved": "Shots Saved By Goalie",
    "clear": "Clears",
    "initiator": "Initiators",
    "pass_to_covered_teammate": "Pass To Covered Teammate",
    "shooter_uncovered": "Possible Shooter Uncovered",
    "lane_coverage_failure": "Possible Lane Gap",
}


class AdvancedSummaryPanel(QWidget):
    layout_tab_id = "advanced_summary"

    def __init__(self, service: AdvancedAnalysisService, layout_service) -> None:
        super().__init__()
        self.service = service
        self.layout_service = layout_service

        self.confidence_filter = ConfidenceFilterWidget(("high", "medium"))
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
        self.include_afk = QCheckBox("Include suspected AFK")
        self.refresh_button = QPushButton("Refresh")
        self.customize_button = QPushButton("Customize Layout")
        self.customize_button.setCheckable(True)
        self.reset_layout_button = QPushButton("Reset Layout")

        self.overview_widget = QWidget()
        overview_shell = QHBoxLayout(self.overview_widget)
        overview_shell.setContentsMargins(0, 0, 0, 0)
        overview_shell.setSpacing(16)
        self.overview_form_widget = QWidget()
        self.overview_layout = QFormLayout(self.overview_form_widget)
        self.overview_chart = CategoryRadarWidget("#7ce7ff")
        overview_shell.addWidget(self.overview_form_widget, 2)
        overview_shell.addWidget(self.overview_chart, 3)
        self.transition_widget = QWidget()
        self.transition_layout = QFormLayout(self.transition_widget)
        self.shooting_widget = QWidget()
        self.shooting_layout = QFormLayout(self.shooting_widget)
        self.speed_widget = QWidget()
        self.speed_layout = QFormLayout(self.speed_widget)
        self.possession_widget = QWidget()
        self.possession_layout = QFormLayout(self.possession_widget)
        self.offense_widget = QWidget()
        self.offense_layout = QFormLayout(self.offense_widget)
        self.defense_widget = QWidget()
        self.defense_layout = QFormLayout(self.defense_widget)
        self.passing_widget = QWidget()
        self.passing_layout = QFormLayout(self.passing_widget)
        self.event_totals = CompactCardList("No advanced player events yet.")
        self.recent_matches = CompactCardList("No recent advanced matches yet.")

        self.cards = CardContainer()
        self.cards.order_changed.connect(self._save_layout_order)
        self.cards.sizes_changed.connect(self._save_layout_sizes)
        self.cards.add_card("overview", "Overview", self.overview_widget)
        self.cards.add_card("transition_summary", "Transition Summary", self.transition_widget)
        self.cards.add_card("shooting_breakdown", "Shooting Breakdown", self.shooting_widget)
        self.cards.add_card("speed_inputs", "Speed Inputs", self.speed_widget)
        self.cards.add_card("possession_inputs", "Possession Inputs", self.possession_widget)
        self.cards.add_card("offense_inputs", "Offense Inputs", self.offense_widget)
        self.cards.add_card("defense_inputs", "Defense Inputs", self.defense_widget)
        self.cards.add_card("passing_inputs", "Passing Inputs", self.passing_widget)
        self.cards.add_card("event_totals", "Event Totals", self.event_totals)
        self.cards.add_card("recent_matches", "Recent Advanced Matches", self.recent_matches)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Confidence"))
        filters.addWidget(self.confidence_filter)
        filters.addWidget(self.competitive_only)
        filters.addWidget(QLabel("Match type"))
        filters.addWidget(self.classification_filter)
        filters.addWidget(QLabel("Private subtype"))
        filters.addWidget(self.private_type_filter)
        filters.addWidget(QLabel("Last"))
        filters.addWidget(self.last_filter)
        filters.addWidget(self.include_afk)
        filters.addStretch()
        filters.addWidget(self.reset_layout_button)
        filters.addWidget(self.customize_button)
        filters.addWidget(self.refresh_button)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.addLayout(filters)
        content_layout.addWidget(self.cards)
        content_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll)

        self.refresh_button.clicked.connect(self.reload)
        self.confidence_filter.selection_changed.connect(self.reload)
        self.competitive_only.stateChanged.connect(self.reload)
        self.classification_filter.selection_changed.connect(self._classification_filter_changed)
        self.private_type_filter.selection_changed.connect(self.reload)
        self.last_filter.currentIndexChanged.connect(self.reload)
        self.include_afk.stateChanged.connect(self.reload)
        self.customize_button.toggled.connect(self.set_customize_layout)
        self.reset_layout_button.clicked.connect(self._handle_reset_layout)

        self._sync_private_type_filter_enabled()
        self.reload_saved_layout()
        self.reload()

    def reload(self) -> None:
        payload = self.service.local_user_summary(
            confidence_levels=self.confidence_filter.selected_levels(),
            filters=self._filters(),
        )
        self._load_overview(payload)
        self._load_transitions(payload.get("transitions") or {})
        categories = payload.get("category_breakdown") or {}
        self._load_category(self.shooting_layout, categories.get("shooting") or {})
        self._load_category(self.speed_layout, categories.get("speed") or {})
        self._load_category(self.possession_layout, categories.get("possession") or {})
        self._load_category(self.offense_layout, categories.get("offense") or {})
        self._load_category(self.defense_layout, categories.get("defense") or {})
        self._load_category(self.passing_layout, categories.get("passing") or {})
        self._load_event_totals(
            payload.get("display_event_totals") or {},
            payload.get("event_averages_per_round") or {},
        )
        self._load_recent_matches(payload.get("recent_matches") or [])

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
        )

    def _load_overview(self, payload: dict) -> None:
        _clear_form(self.overview_layout)
        active_profile = payload.get("active_profile") or {}
        player_names = payload.get("canonical_player_names") or []
        warnings = payload.get("warnings") or []
        category_breakdown = payload.get("category_breakdown") or {}
        radar_scores = category_scores_from_breakdown(category_breakdown)
        self.overview_layout.addRow("active profile", _label(active_profile.get("display_name") or "none"))
        self.overview_layout.addRow(
            "self player",
            _label(", ".join(str(name) for name in player_names) if player_names else "not linked yet"),
        )
        self.overview_layout.addRow("confidence view", _label(", ".join(payload.get("confidence_levels") or [])))
        self.overview_layout.addRow("finalized matches", _label(payload.get("total_finalized_matches", 0)))
        self.overview_layout.addRow("matches with advanced data", _label(payload.get("matches_with_advanced_data", 0)))
        self.overview_layout.addRow("event round samples", _label(_round_sample_text(payload.get("total_rounds_considered", 0))))
        self.overview_layout.addRow("advanced events", _label(payload.get("total_advanced_events", 0)))
        self.overview_layout.addRow("personal round samples", _label(_round_sample_text(payload.get("metric_rounds_considered", 0))))
        if payload.get("competitive_baseline_sample_size") is not None:
            self.overview_layout.addRow(
                "competitive baseline rows",
                _label(payload.get("competitive_baseline_sample_size", 0)),
            )
        baseline_matches = payload.get("competitive_baseline_match_ids") or []
        if baseline_matches:
            self.overview_layout.addRow(
                "competitive baseline matches",
                _label(", ".join(str(match_id) for match_id in baseline_matches)),
            )
        metric_note = payload.get("metric_summary_note")
        if metric_note:
            self.overview_layout.addRow("metric note", _label(metric_note))

        confidence_counts = payload.get("confidence_counts") or {}
        self.overview_layout.addRow(
            "confidence mix",
            _label(
                " | ".join(
                    f"{level.title()} {int(confidence_counts.get(level, 0))}" for level in ("high", "medium", "low")
                )
            ),
        )
        if warnings:
            self.overview_layout.addRow("warning", _warning_label(" | ".join(str(warning) for warning in warnings)))
        self.overview_chart.set_scores(
            radar_scores,
            overall_label="Overall",
            overall_score=overall_score_from_scores(radar_scores),
        )

    def _load_transitions(self, transitions: dict) -> None:
        _clear_form(self.transition_layout)
        offense_avg = transitions.get("average_time_to_offense")
        defense_avg = transitions.get("average_time_to_defense")
        self.transition_layout.addRow(
            "average time to offense",
            _label(f"{float(offense_avg):.2f}s" if offense_avg is not None else "No samples"),
        )
        self.transition_layout.addRow(
            "average time to defense",
            _label(f"{float(defense_avg):.2f}s" if defense_avg is not None else "No samples"),
        )
        self.transition_layout.addRow("offense samples", _label(transitions.get("offense_samples", 0)))
        self.transition_layout.addRow("defense samples", _label(transitions.get("defense_samples", 0)))

    def _load_category(self, layout: QFormLayout, category: dict) -> None:
        _clear_form(layout)
        if not category:
            layout.addRow("status", _label("No data yet."))
            return
        overall_score = category.get("overall_score")
        if overall_score is not None:
            layout.addRow("overall score", _label(f"{float(overall_score):.1f}"))
        note = str(category.get("score_note") or "").strip()
        if note:
            layout.addRow("note", _label(note))
        for metric in category.get("metrics") or []:
            label = str(metric.get("label") or "metric")
            value = str(metric.get("value") or "")
            note_value = str(metric.get("note") or "").strip()
            if note_value:
                value = f"{value}  ({note_value})"
            layout.addRow(label, _label(value))

    def _load_event_totals(self, counts: dict[str, int], averages: dict[str, float]) -> None:
        items = []
        for event_type, count in sorted(counts.items()):
            items.append(
                {
                    "title": SUMMARY_EVENT_LABELS.get(event_type, event_type.replace("_", " ").title()),
                    "chips": [f"Count {int(count)}", f"Avg/Rd {float(averages.get(event_type, 0.0)):.2f}"],
                }
            )
        self.event_totals.set_items(items)

    def _load_recent_matches(self, rows: list[dict]) -> None:
        items = []
        for row in rows:
            counts = row.get("counts") or {}
            chips = []
            for event_type in ("turnover", "interception", "clear", "initiator", "missed_shot", "shot_saved"):
                count = counts.get(event_type)
                if count:
                    chips.append(f"{SUMMARY_EVENT_LABELS.get(event_type, event_type)} {int(count)}")
            offense_avg = row.get("average_time_to_offense")
            defense_avg = row.get("average_time_to_defense")
            if offense_avg is not None:
                chips.append(f"To offense {float(offense_avg):.2f}s")
            if defense_avg is not None:
                chips.append(f"To defense {float(defense_avg):.2f}s")
            subtitle_parts = [str(row.get("match_classification") or "Unknown"), str(row.get("result") or "unknown")]
            if row.get("private_match_type"):
                subtitle_parts.append(str(row["private_match_type"]))
            items.append(
                {
                    "title": row.get("display_name") or f"Match {row.get('match_id')}",
                    "subtitle": " | ".join(subtitle_parts),
                    "chips": chips,
                }
            )
        self.recent_matches.set_items(items)


def _clear_form(layout: QFormLayout) -> None:
    while layout.rowCount():
        layout.removeRow(0)


def _label(value: object) -> QLabel:
    label = QLabel(str(value))
    label.setWordWrap(True)
    return label


def _round_sample_text(value: object) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(numeric - round(numeric)) < 0.001:
        return str(int(round(numeric)))
    return f"{numeric:.2f}"


def _warning_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #ff7a7a;")
    return label
