"""Compact card/list widgets for small dashboard surfaces."""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


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
                [str(chip) for chip in row if str(chip).strip()]
                for row in chip_rows
                if row
            ]
        else:
            chip_values = [str(chip) for chip in item.get("chips") or [] if str(chip).strip()]
            normalized_rows = _chunked(chip_values, 5)
        for chip_values in normalized_rows:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            row_layout.setAlignment(Qt.AlignLeft)
            for chip in chip_values:
                row_layout.addWidget(_chip(chip))
            row_layout.addStretch()
            chips_root.addWidget(row_widget)
        chips_widget.setVisible(bool(normalized_rows))

        warning_value = str(item.get("warning") or "").strip()
        warning = QLabel(warning_value)
        warning.setWordWrap(True)
        warning.setVisible(bool(warning_value))
        warning.setStyleSheet("color: #ff7a7a;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(chips_widget)
        layout.addWidget(warning)


def _chip(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(
        "padding: 3px 8px; border: 1px solid #2f4861; border-radius: 6px; color: #d7f7ff; background: #142333;"
    )
    return label


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


def _chunked(values: Sequence[str], size: int) -> list[list[str]]:
    if not values:
        return []
    return [list(values[index : index + size]) for index in range(0, len(values), size)]
