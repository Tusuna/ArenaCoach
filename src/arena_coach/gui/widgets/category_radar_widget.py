"""Radar-style category score visualization widgets."""

from __future__ import annotations

import math
from typing import Mapping, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget


RADAR_CATEGORY_ORDER = (
    ("speed", "SPEED"),
    ("defense", "DEFENSE"),
    ("passing", "PASS"),
    ("possession", "POSSESSION"),
    ("shooting", "SHOOT"),
    ("offense", "OFFENSE"),
)


def grade_for_score(score: Optional[float]) -> str:
    if score is None:
        return "-"
    value = float(score)
    if value >= 90.0:
        return "S"
    if value >= 75.0:
        return "A"
    if value >= 55.0:
        return "B"
    if value >= 40.0:
        return "C"
    if value >= 20.0:
        return "D"
    return "F"


def category_scores_from_breakdown(category_breakdown: Mapping[str, object]) -> dict[str, Optional[float]]:
    scores: dict[str, Optional[float]] = {}
    for key, _label in RADAR_CATEGORY_ORDER:
        row = category_breakdown.get(key) if isinstance(category_breakdown, Mapping) else None
        if isinstance(row, Mapping) and row.get("overall_score") is not None:
            scores[key] = float(row["overall_score"])
        else:
            scores[key] = None
    return scores


def overall_score_from_scores(scores: Mapping[str, Optional[float]]) -> Optional[float]:
    values = [float(value) for value in scores.values() if value is not None]
    if not values:
        return None
    return sum(values) / float(len(values))


class CategoryRadarWidget(QWidget):
    def __init__(self, accent: str = "#7ce7ff", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._scores: dict[str, Optional[float]] = {key: None for key, _label in RADAR_CATEGORY_ORDER}
        self._accent = QColor(accent)
        self._accent_fill = QColor(self._accent)
        self._accent_fill.setAlpha(72)
        self._grid = QColor("#2b3544")
        self._axis = QColor("#405167")
        self._text = QColor("#eef4ff")
        self._muted = QColor("#7f94a8")
        self._overall_label = "Overall"
        self._overall_score: Optional[float] = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(320)
        self.setMinimumWidth(320)

    def set_scores(
        self,
        scores: Mapping[str, Optional[float]],
        *,
        overall_label: str = "Overall",
        overall_score: Optional[float] = None,
    ) -> None:
        updated = {key: scores.get(key) for key, _label in RADAR_CATEGORY_ORDER}
        self._scores = {key: (float(value) if value is not None else None) for key, value in updated.items()}
        self._overall_label = str(overall_label)
        self._overall_score = float(overall_score) if overall_score is not None else overall_score_from_scores(self._scores)
        self.update()

    def clear(self) -> None:
        self.set_scores({})

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), Qt.transparent)

        outer = self.rect().adjusted(12, 12, -12, -12)
        header_height = 48
        chart_rect = QRectF(outer.left(), outer.top() + header_height, outer.width(), outer.height() - header_height)
        if chart_rect.width() <= 0 or chart_rect.height() <= 0:
            return

        self._draw_header(painter, outer)
        self._draw_radar(painter, chart_rect)

    def _draw_header(self, painter: QPainter, outer: QRectF) -> None:
        score = self._overall_score
        grade = grade_for_score(score)

        title_font = QFont(painter.font())
        title_font.setPointSize(9)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(self._muted)
        painter.drawText(QRectF(outer.left(), outer.top(), outer.width(), 18), Qt.AlignLeft | Qt.AlignVCenter, self._overall_label.upper())

        score_font = QFont(painter.font())
        score_font.setPointSize(22)
        score_font.setBold(True)
        painter.setFont(score_font)
        painter.setPen(self._text)
        painter.drawText(QRectF(outer.left(), outer.top() + 16, outer.width() - 72, 28), Qt.AlignLeft | Qt.AlignVCenter, f"{float(score):.1f}" if score is not None else "--")

        grade_font = QFont(painter.font())
        grade_font.setPointSize(28)
        grade_font.setBold(True)
        painter.setFont(grade_font)
        painter.setPen(self._accent)
        painter.drawText(QRectF(outer.right() - 64, outer.top(), 64, 44), Qt.AlignRight | Qt.AlignVCenter, grade)

    def _draw_radar(self, painter: QPainter, chart_rect: QRectF) -> None:
        center = QPointF(chart_rect.center().x(), chart_rect.center().y() + 10.0)
        radius = min(chart_rect.width() * 0.33, chart_rect.height() * 0.34)
        if radius <= 0:
            return

        for scale in (0.2, 0.4, 0.6, 0.8, 1.0):
            polygon = QPolygonF([self._point_for_axis(index, center, radius * scale) for index in range(len(RADAR_CATEGORY_ORDER))])
            pen = QPen(self._grid, 1.0)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPolygon(polygon)

        for index in range(len(RADAR_CATEGORY_ORDER)):
            painter.setPen(QPen(self._axis, 1.0))
            painter.drawLine(center, self._point_for_axis(index, center, radius))

        score_polygon = self._score_polygon(center, radius)
        if score_polygon.count() >= 3:
            painter.setPen(QPen(self._accent, 2.0))
            painter.setBrush(self._accent_fill)
            painter.drawPolygon(score_polygon)

        self._draw_axis_labels(painter, center, radius)

    def _draw_axis_labels(self, painter: QPainter, center: QPointF, radius: float) -> None:
        label_font = QFont(painter.font())
        label_font.setPointSize(11)
        label_font.setBold(True)
        grade_font = QFont(painter.font())
        grade_font.setPointSize(22)
        grade_font.setBold(True)

        for index, (key, label) in enumerate(RADAR_CATEGORY_ORDER):
            anchor = self._point_for_axis(index, center, radius + 38.0)
            rect = QRectF(anchor.x() - 72.0, anchor.y() - 32.0, 144.0, 68.0)

            painter.setFont(label_font)
            painter.setPen(self._text)
            painter.drawText(QRectF(rect.left(), rect.top(), rect.width(), 24.0), Qt.AlignCenter, label)

            painter.setFont(grade_font)
            painter.setPen(self._accent)
            painter.drawText(QRectF(rect.left(), rect.top() + 20.0, rect.width(), 36.0), Qt.AlignCenter, grade_for_score(self._scores.get(key)))

    def _score_polygon(self, center: QPointF, radius: float) -> QPolygonF:
        points = []
        for index, (key, _label) in enumerate(RADAR_CATEGORY_ORDER):
            value = self._scores.get(key)
            normalized = 0.0 if value is None else max(0.0, min(float(value), 100.0)) / 100.0
            points.append(self._point_for_axis(index, center, radius * normalized))
        return QPolygonF(points)

    @staticmethod
    def _point_for_axis(index: int, center: QPointF, radius: float) -> QPointF:
        angle = math.radians(-90.0 + (360.0 / float(len(RADAR_CATEGORY_ORDER))) * index)
        return QPointF(center.x() + math.cos(angle) * radius, center.y() + math.sin(angle) * radius)


class DualCategoryRadarWidget(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.left_title = QLabel("Left Player")
        self.right_title = QLabel("Right Player")
        self.left_title.setStyleSheet("color: #7f94a8; font-weight: 600;")
        self.right_title.setStyleSheet("color: #7f94a8; font-weight: 600;")

        self.left_chart = CategoryRadarWidget("#7ce7ff")
        self.right_chart = CategoryRadarWidget("#ffb347")

        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.left_title)
        left_layout.addWidget(self.left_chart, 1)

        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.right_title)
        right_layout.addWidget(self.right_chart, 1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        left_widget = QWidget()
        left_widget.setLayout(left_layout)
        right_widget = QWidget()
        right_widget.setLayout(right_layout)
        layout.addWidget(left_widget, 1)
        layout.addWidget(right_widget, 1)
        self.setMinimumHeight(360)

    def set_comparison(
        self,
        left_scores: Mapping[str, Optional[float]],
        right_scores: Mapping[str, Optional[float]],
        *,
        left_label: str = "Left Player",
        right_label: str = "Right Player",
        left_overall: Optional[float] = None,
        right_overall: Optional[float] = None,
    ) -> None:
        self.left_title.setText(left_label)
        self.right_title.setText(right_label)
        self.left_chart.set_scores(left_scores, overall_label="Overall", overall_score=left_overall)
        self.right_chart.set_scores(right_scores, overall_label="Overall", overall_score=right_overall)

    def clear(self) -> None:
        self.left_title.setText("Left Player")
        self.right_title.setText("Right Player")
        self.left_chart.clear()
        self.right_chart.clear()
