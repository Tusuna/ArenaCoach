"""Compact card/list widgets for small dashboard surfaces."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from arena_coach.gui.widgets.category_radar_widget import CategoryRadarWidget, grade_for_score, overall_score_from_scores


class CompactCardList(QWidget):
    def __init__(self, empty_text: str = "No data yet.") -> None:
        super().__init__()
        self.empty_text = empty_text
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        self.scroll.setWidget(self.content)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll)

        self.set_items([])

    def set_items(self, items: Sequence[Mapping[str, object]]) -> None:
        _clear_layout(self.content_layout)
        if not items:
            self.content_layout.addWidget(_empty_label(self.empty_text))
            self.content_layout.addStretch()
            return
        for item in items:
            self.content_layout.addWidget(_CompactCard(item))
        self.content_layout.addStretch()


class TeamScoreboardCard(QWidget):
    def __init__(self, title: str, accent: str) -> None:
        super().__init__()
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(f"color: {accent}; font-weight: bold; font-size: 16px;")
        self.details_label = QLabel("")
        self.details_label.setWordWrap(True)
        self.details_label.setStyleSheet("color: #9fb2c4;")
        self.details_label.setVisible(False)
        self.rows = CompactCardList("No active rows.")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.title_label)
        layout.addWidget(self.details_label)
        layout.addWidget(self.rows)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_details(self, text: str) -> None:
        value = str(text or "").strip()
        self.details_label.setText(value)
        self.details_label.setVisible(bool(value))

    def set_rows(self, rows: Sequence[Mapping[str, object]]) -> None:
        self.rows.set_items(rows)


class _CompactCard(QFrame):
    def __init__(self, item: Mapping[str, object]) -> None:
        super().__init__()
        self.setObjectName("compactCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        title = QLabel(str(item.get("title") or ""))
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: 600;")

        subtitle_value = str(item.get("subtitle") or "").strip()
        subtitle = QLabel(subtitle_value)
        subtitle.setWordWrap(True)
        subtitle.setVisible(bool(subtitle_value))
        subtitle.setProperty("class", "muted")

        chips_widget = QWidget()
        chips_root = QVBoxLayout(chips_widget)
        chips_root.setContentsMargins(0, 0, 0, 0)
        chips_root.setSpacing(6)
        chip_rows = item.get("chip_rows")
        if chip_rows:
            normalized_rows = [
                [_chip_spec(chip) for chip in row if _chip_spec(chip) is not None]
                for row in chip_rows
                if row
            ]
        else:
            chip_values = [_chip_spec(chip) for chip in item.get("chips") or []]
            normalized_rows = _chunked([chip for chip in chip_values if chip is not None], 5)
        for chip_specs in normalized_rows:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            row_layout.setAlignment(Qt.AlignLeft)
            for chip in chip_specs:
                row_layout.addWidget(_chip(chip))
            row_layout.addStretch()
            chips_root.addWidget(row_widget)
        chips_widget.setVisible(bool(normalized_rows))

        warning_value = str(item.get("warning") or "").strip()
        warning = QLabel(warning_value)
        warning.setWordWrap(True)
        warning.setVisible(bool(warning_value))
        warning.setStyleSheet("color: #ff7a7a;")

        text_column = QWidget()
        text_layout = QVBoxLayout(text_column)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(5)
        text_layout.addWidget(title)
        text_layout.addWidget(subtitle)
        text_layout.addWidget(chips_widget)
        text_layout.addWidget(warning)

        body_layout = QHBoxLayout()
        body_layout.setContentsMargins(10, 8, 10, 8)
        body_layout.setSpacing(10)
        body_layout.addWidget(text_column, 1)

        radar_scores = item.get("radar_scores")
        if isinstance(radar_scores, Mapping) and radar_scores:
            radar = CategoryRadarWidget(
                "#7ce7ff",
                show_header=True,
                show_axis_labels=True,
                show_axis_grades=False,
                compact=True,
            )
            radar.setBaseSize(228, 228)
            radar.set_base_fixed_size(228)
            radar.set_scores(
                radar_scores,
                overall_label="Match",
                overall_score=item.get("radar_overall"),
                category_details=item.get("radar_details") if isinstance(item.get("radar_details"), Mapping) else None,
            )
            radar.setToolTip(str(item.get("radar_tooltip") or _radar_tooltip(radar_scores)))
            body_layout.addWidget(radar, 0, Qt.AlignTop)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(body_layout)


def _chip(spec: Mapping[str, str]) -> QToolButton:
    button = QToolButton()
    button.setText(str(spec.get("text") or ""))
    button.setFocusPolicy(Qt.NoFocus)
    button.setCursor(Qt.PointingHandCursor)
    button.setAutoRaise(False)
    button.setStyleSheet(
        "QToolButton { padding: 3px 8px; border: 1px solid #2f4861; border-radius: 6px; color: #d7f7ff; background: #142333; }"
        "QToolButton:hover { border-color: #55d6ff; background: #183044; }"
    )
    tooltip = str(spec.get("tooltip") or "").strip()
    if tooltip:
        button.setToolTip(tooltip)
        button.clicked.connect(lambda _checked=False, widget=button, text=tooltip: _show_chip_tooltip(widget, text))
    return button


def _empty_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #7f94a8; padding: 4px;")
    return label


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget is not None:
            widget.deleteLater()
        elif child_layout is not None:
            _clear_layout(child_layout)  # type: ignore[arg-type]


def _chunked(values: Sequence[Any], size: int) -> list[list[Any]]:
    if not values:
        return []
    return [list(values[index : index + size]) for index in range(0, len(values), size)]


def _chip_spec(value: Any) -> Mapping[str, str] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        text = str(value.get("text") or "").strip()
        if not text:
            return None
        return {
            "text": text,
            "tooltip": str(value.get("tooltip") or "").strip(),
        }
    text = str(value).strip()
    if not text:
        return None
    return {"text": text, "tooltip": ""}


def _show_chip_tooltip(widget: QWidget, text: str) -> None:
    if not text:
        return
    global_pos = widget.mapToGlobal(widget.rect().bottomLeft())
    from PySide6.QtWidgets import QToolTip

    QToolTip.showText(global_pos, text, widget)


def _radar_tooltip(scores: Mapping[str, Any]) -> str:
    overall = overall_score_from_scores(scores)
    lines = []
    if overall is not None:
        lines.append(f"Match-only overall: {float(overall):.1f} ({grade_for_score(overall)})")
    for key, label in (
        ("shooting", "Shooting"),
        ("speed", "Speed"),
        ("possession", "Possession"),
        ("offense", "Offense"),
        ("defense", "Defense"),
        ("passing", "Passing"),
    ):
        value = scores.get(key)
        if value is None:
            continue
        lines.append(f"{label}: {float(value):.1f} ({grade_for_score(value)})")
    return "\n".join(lines)
