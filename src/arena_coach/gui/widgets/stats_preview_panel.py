"""Upgraded stats preview panel backed by the stats engine."""

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
from arena_coach.gui.widgets.compact_card_list import CompactCardList
from arena_coach.gui.widgets.multi_select_menu_button import MultiSelectMenuButton
from arena_coach.match_context import PRIVATE_MATCH_TYPES, private_match_type_label
from arena_coach.services.advanced_analysis_service import AdvancedAnalysisService
from arena_coach.services.layout_service import LayoutService
from arena_coach.services.stats_preview_service import StatsPreviewService
from arena_coach.stats.stat_filters import StatsFilter


class StatsPreviewPanel(QWidget):
    layout_tab_id = "stats_preview"

    def __init__(self, service: StatsPreviewService, layout_service: LayoutService) -> None:
        super().__init__()
        self.service = service
        self.advanced_service = AdvancedAnalysisService(service.database_path)
        self.layout_service = layout_service

        self.competitive_only = QCheckBox("Competitive only")
        self.classification_filter = MultiSelectMenuButton(all_selected_text="All match types")
        self.classification_filter.set_options(
            [
                ("public", "Public"),
                ("private", "Private"),
                ("tournament", "Tournament"),
                ("unknown", "Unknown"),
            ]
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
        self.include_guests = QCheckBox("Include guests")
        self.include_afk = QCheckBox("Include suspected AFK")
        self.refresh_button = QPushButton("Refresh")
        self.customize_button = QPushButton("Customize Layout")
        self.customize_button.setCheckable(True)
        self.reset_layout_button = QPushButton("Reset Layout")

        self.summary_widget = QWidget()
        self.summary_layout = QFormLayout(self.summary_widget)
        self.quality_widget = QWidget()
        self.quality_layout = QFormLayout(self.quality_widget)
        self.playstyle_widget = QWidget()
        playstyle_layout = QVBoxLayout(self.playstyle_widget)
        self.playstyle_label = QLabel("No data yet.")
        self.playstyle_note = QLabel("")
        self.playstyle_note.setWordWrap(True)
        self.low_sample_label = QLabel("")
        self.low_sample_label.setWordWrap(True)
        self.low_sample_label.setStyleSheet("color: #ffb347;")
        playstyle_layout.addWidget(self.playstyle_label)
        playstyle_layout.addWidget(self.playstyle_note)
        playstyle_layout.addWidget(self.low_sample_label)
        self.category_snapshot = CompactCardList("No category scores yet.")

        self.trends_list = CompactCardList("No trends yet.")
        self.rivals_list = CompactCardList("No rival data yet.")
        self.teammates_list = CompactCardList("No teammate data yet.")
        self.recent_list = CompactCardList("No recent matches yet.")
        self.players_list = CompactCardList("No player appearance data yet.")

        self.cards = CardContainer()
        self.cards.order_changed.connect(self._save_layout_order)
        self.cards.sizes_changed.connect(self._save_layout_sizes)
        self.cards.add_card("profile_summary", "Profile Summary", self.summary_widget)
        self.cards.add_card("match_quality_summary", "Match Quality Summary", self.quality_widget)
        self.cards.add_card("playstyle_guess", "Playstyle Guess", self.playstyle_widget)
        self.cards.add_card("category_snapshot", "Category Snapshot", self.category_snapshot)
        self.cards.add_card("recent_trends", "Recent Trends", self.trends_list)
        self.cards.add_card("top_rivals", "Top Rivals", self.rivals_list)
        self.cards.add_card("best_teammates", "Best Teammates", self.teammates_list)
        self.cards.add_card("recent_matches", "Recent Matches", self.recent_list)
        self.cards.add_card("top_players_by_appearance", "Top Players by Appearances", self.players_list)

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
        filters.addWidget(self.include_guests)
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
        self.competitive_only.stateChanged.connect(self.reload)
        self.classification_filter.selection_changed.connect(self._classification_filter_changed)
        self.private_type_filter.selection_changed.connect(self.reload)
        self.last_filter.currentIndexChanged.connect(self.reload)
        self.scoring_mode.currentIndexChanged.connect(self.reload)
        self.include_guests.stateChanged.connect(self.reload)
        self.include_afk.stateChanged.connect(self.reload)
        self.customize_button.toggled.connect(self.set_customize_layout)
        self.reset_layout_button.clicked.connect(self._handle_reset_layout)

        self._sync_private_type_filter_enabled()
        self.reload_saved_layout()
        self.reload()

    def reload(self) -> None:
        filters = self._filters()
        payload = self.service.preview(filters)
        advanced = self.advanced_service.local_user_summary(filters=filters)
        summary = payload.get("summary") or {}
        quality = payload.get("quality") or {}
        trends = payload.get("trends") or {}
        matchups = payload.get("matchups") or {}
        teammates = payload.get("teammates") or {}

        self._load_summary(summary)
        self._load_quality(summary, quality)
        self._load_playstyle(summary.get("playstyle") or payload.get("playstyle") or {})
        self._load_category_snapshot(advanced.get("category_breakdown") or {})
        self._load_trends(trends.get("metrics") or [])
        self._load_rivals(matchups.get("top_rivals") or matchups.get("rows") or [])
        self._load_teammates(teammates.get("best_teammates") or teammates.get("rows") or [])
        self._load_recent(payload.get("recent_matches") or [])
        self._load_players(payload.get("top_players_by_appearances") or [])

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
            include_guest_players=self.include_guests.isChecked(),
            include_afk_players=self.include_afk.isChecked(),
            private_match_types=tuple(selected_private_types),
            last_n=last_n,
            category_scoring_mode=str(self.scoring_mode.currentData() or "mistake_adjusted"),
        )

    def _load_summary(self, summary: dict[str, object]) -> None:
        _clear_form(self.summary_layout)
        active_profile = summary.get("active_profile") or {}
        self.summary_layout.addRow("active profile", _label(active_profile.get("display_name") or "none"))
        self.summary_layout.addRow("matches played", _label(summary.get("matches_played", 0)))
        self.summary_layout.addRow("competitive eligible", _label(summary.get("competitive_eligible_matches", 0)))
        self.summary_layout.addRow(
            "record",
            _label(f"{summary.get('wins', 0)} / {summary.get('losses', 0)} / {summary.get('ties', 0)}"),
        )
        self.summary_layout.addRow("win rate", _label(f"{float(summary.get('win_rate', 0.0)):.2f}%"))
        averages = summary.get("averages") or {}
        self.summary_layout.addRow("avg points", _label(f"{float(averages.get('points', 0.0)):.2f}"))
        self.summary_layout.addRow("avg goals", _label(f"{float(averages.get('goals', 0.0)):.2f}"))
        self.summary_layout.addRow("avg assists", _label(f"{float(averages.get('assists', 0.0)):.2f}"))
        self.summary_layout.addRow("avg saves", _label(f"{float(averages.get('saves', 0.0)):.2f}"))
        self.summary_layout.addRow("avg stuns", _label(f"{float(averages.get('stuns', 0.0)):.2f}"))
        self.summary_layout.addRow("avg steals", _label(f"{float(averages.get('steals', 0.0)):.2f}"))
        self.summary_layout.addRow("shot efficiency", _label(f"{float(summary.get('shot_efficiency', 0.0)):.3f}"))

    def _load_quality(self, summary: dict[str, object], quality: dict[str, object]) -> None:
        _clear_form(self.quality_layout)
        counts = quality.get("counts") or {}
        self.quality_layout.addRow("competitive eligible", _label(counts.get("Competitive Eligible", 0)))
        self.quality_layout.addRow("AFK affected", _label(counts.get("AFK Affected", 0)))
        self.quality_layout.addRow("low quality", _label(counts.get("Low Quality", 0)))
        self.quality_layout.addRow("unreviewed", _label(counts.get("Unreviewed", 0)))
        self.quality_layout.addRow("excluded low quality", _label(summary.get("excluded_low_quality_count", 0)))
        self.quality_layout.addRow("guest/unmapped rows", _label(summary.get("guest_unmapped_count", 0)))

    def _load_playstyle(self, playstyle: dict[str, object]) -> None:
        label = str(playstyle.get("label") or "Unknown")
        explanation = str(playstyle.get("explanation") or "")
        sample_size = int(playstyle.get("sample_size") or 0)
        self.playstyle_label.setText(f"{label} ({sample_size} match sample)")
        self.playstyle_note.setText(explanation)
        self.low_sample_label.setText("")
        if label == "Low Sample":
            self.low_sample_label.setText("Low sample warning: this label is an early read, not a ranking.")

    def _load_trends(self, metrics: list[dict[str, object]]) -> None:
        items = []
        for metric in metrics:
            items.append(
                {
                    "title": str(metric.get("stat_name") or "Unknown").title(),
                    "subtitle": f"{float(metric.get('previous_average', 0.0)):.2f} -> {float(metric.get('last_average', 0.0)):.2f}",
                    "chips": [f"Delta {float(metric.get('delta', 0.0)):+.2f}", str(metric.get("direction") or "flat").title()],
                }
            )
        self.trends_list.set_items(items)

    def _load_category_snapshot(self, category_breakdown: dict[str, object]) -> None:
        items = []
        for key, label in (
            ("shooting", "Shooting"),
            ("speed", "Speed"),
            ("possession", "Possession"),
            ("offense", "Offense"),
            ("defense", "Defense"),
            ("passing", "Passing"),
        ):
            category = category_breakdown.get(key) if isinstance(category_breakdown, dict) else None
            if not isinstance(category, dict):
                continue
            display_score = category.get("display_score")
            final_score = category.get("final_score")
            base_score = category.get("base_score")
            penalty = category.get("mistake_penalty")
            confidence_label = str(category.get("confidence_label") or "unknown").title()
            rounds = category.get("rounds_considered")
            subtitle = str(category.get("explanation") or "").strip()
            chips = []
            if display_score is not None:
                chips.append(f"Display {float(display_score):.1f}")
            if final_score is not None:
                chips.append(f"Absolute {float(final_score):.1f}")
            if base_score is not None:
                chips.append(f"Base {float(base_score):.1f}")
            if penalty is not None:
                chips.append(f"Penalty -{float(penalty):.1f}")
            if rounds is not None:
                chips.append(f"{confidence_label} ({float(rounds):.2f} rd)")
            issues = list(category.get("main_mistake_inputs") or [])
            warning = f"Main issues: {', '.join(issues[:3])}" if issues else ""
            items.append(
                {
                    "title": label,
                    "subtitle": subtitle,
                    "chips": chips,
                    "warning": warning,
                }
            )
        self.category_snapshot.set_items(items)

    def _load_rivals(self, rows: list[dict[str, object]]) -> None:
        items = []
        for row in rows[:10]:
            items.append(
                {
                    "title": row.get("display_name") or "Unknown",
                    "subtitle": f"Matches against {row.get('matches_against', 0)} | Win rate {float(row.get('win_rate_against', 0.0)):.2f}%",
                    "chips": [
                        f"Opp Pts {float((row.get('opponent_totals') or {}).get('points', 0.0)):.1f}",
                        f"Opp Goals {float((row.get('opponent_totals') or {}).get('goals', 0.0)):.1f}",
                        f"Opp Stuns {float((row.get('opponent_totals') or {}).get('stuns', 0.0)):.1f}",
                    ],
                }
            )
        self.rivals_list.set_items(items)

    def _load_teammates(self, rows: list[dict[str, object]]) -> None:
        items = []
        for row in rows[:10]:
            items.append(
                {
                    "title": row.get("display_name") or "Unknown",
                    "subtitle": f"Matches together {row.get('matches_together', 0)} | Win rate {float(row.get('win_rate_together', 0.0)):.2f}%",
                    "chips": [
                        f"My Pts {float((row.get('user_averages') or {}).get('points', 0.0)):.2f}",
                        f"Their Pts {float((row.get('teammate_averages') or {}).get('points', 0.0)):.2f}",
                        str(row.get("confidence") or "Unknown"),
                    ],
                }
            )
        self.teammates_list.set_items(items)

    def _load_recent(self, matches: list[dict[str, object]]) -> None:
        items = []
        for match in matches:
            subtitle_parts = [
                str(match.get("match_classification") or "Unknown"),
                f"Blue {match.get('blue_score', 0)} - Orange {match.get('orange_score', 0)}",
            ]
            if match.get("private_match_type"):
                subtitle_parts.append(private_match_type_label(match["private_match_type"]))
            items.append(
                {
                    "title": match.get("display_name") or "Unknown Match",
                    "subtitle": " | ".join(subtitle_parts),
                    "chips": [str(match.get("quality_label") or "Unknown")],
                }
            )
        self.recent_list.set_items(items)

    def _load_players(self, players: list[dict[str, object]]) -> None:
        items = []
        for player in players[:10]:
            items.append(
                {
                    "title": player.get("name") or "Unknown",
                    "chips": [f"Appearances {player.get('appearances', 0)}"],
                }
            )
        self.players_list.set_items(items)


def _clear_form(layout: QFormLayout) -> None:
    while layout.rowCount():
        layout.removeRow(0)


def _label(value: object) -> QLabel:
    label = QLabel(str(value))
    label.setWordWrap(True)
    return label
