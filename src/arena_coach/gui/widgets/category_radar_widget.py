"""Radar-style category score visualization widgets."""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QHelpEvent, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QToolTip, QVBoxLayout, QWidget


RADAR_CATEGORY_ORDER = (
    ("speed", "SPEED"),
    ("defense", "DEFENSE"),
    ("passing", "PASS"),
    ("possession", "POSSESSION"),
    ("shooting", "SHOOT"),
    ("offense", "OFFENSE"),
)

COMPACT_LABELS = {
    "speed": "SPEED",
    "defense": "DEF",
    "passing": "PASS",
    "possession": "POSS",
    "shooting": "SHOT",
    "offense": "OFF",
}


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


def category_scores_from_breakdown(
    category_breakdown: Mapping[str, object],
    preferred_key: str = "display_score",
) -> dict[str, Optional[float]]:
    scores: dict[str, Optional[float]] = {}
    for key, _label in RADAR_CATEGORY_ORDER:
        row = category_breakdown.get(key) if isinstance(category_breakdown, Mapping) else None
        if isinstance(row, Mapping):
            value = row.get(preferred_key)
            if value is None and preferred_key != "overall_score":
                value = row.get("overall_score")
            if value is None and preferred_key != "final_score":
                value = row.get("final_score")
            scores[key] = float(value) if value is not None else None
            continue
        scores[key] = None
    return scores


def overall_score_from_scores(scores: Mapping[str, Optional[float]]) -> Optional[float]:
    values = [float(value) for value in scores.values() if value is not None]
    if not values:
        return None
    return sum(values) / float(len(values))


class CategoryRadarWidget(QWidget):
    def __init__(
        self,
        accent: str = "#7ce7ff",
        parent: Optional[QWidget] = None,
        *,
        show_header: bool = True,
        show_axis_labels: bool = True,
        show_axis_grades: bool = True,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self._scores: dict[str, Optional[float]] = {key: None for key, _label in RADAR_CATEGORY_ORDER}
        self._category_details: dict[str, Mapping[str, Any]] = {}
        self._axis_hit_regions: list[tuple[QRectF, str]] = []
        self._accent = QColor(accent)
        self._accent_fill = QColor(self._accent)
        self._accent_fill.setAlpha(72)
        self._grid = QColor("#2b3544")
        self._axis = QColor("#405167")
        self._text = QColor("#eef4ff")
        self._muted = QColor("#7f94a8")
        self._overall_label = "Overall"
        self._overall_score: Optional[float] = None
        self._show_header = show_header
        self._show_axis_labels = show_axis_labels
        self._show_axis_grades = show_axis_grades
        self._compact = compact
        self._base_min_height = 180 if compact else 320
        self._base_min_width = 180 if compact else 320
        self._base_fixed_size: Optional[int] = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.refresh_scale()

    def set_scores(
        self,
        scores: Mapping[str, Optional[float]],
        *,
        overall_label: str = "Overall",
        overall_score: Optional[float] = None,
        category_details: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> None:
        updated = {key: scores.get(key) for key, _label in RADAR_CATEGORY_ORDER}
        self._scores = {key: (float(value) if value is not None else None) for key, value in updated.items()}
        self._overall_label = str(overall_label)
        self._overall_score = float(overall_score) if overall_score is not None else overall_score_from_scores(self._scores)
        self._category_details = {
            str(key): value for key, value in (category_details or {}).items() if isinstance(value, Mapping)
        }
        self.update()

    def clear(self) -> None:
        self.set_scores({}, category_details={})

    def set_base_fixed_size(self, size: int) -> None:
        self._base_fixed_size = int(size)
        self.refresh_scale()

    def refresh_scale(self) -> None:
        scale = self._ui_scale()
        if self._base_fixed_size is not None:
            fixed = int(round(self._base_fixed_size * scale))
            self.setFixedSize(fixed, fixed)
        else:
            self.setMinimumHeight(int(round(self._base_min_height * scale)))
            self.setMinimumWidth(int(round(self._base_min_width * scale)))
        self.updateGeometry()
        self.update()

    def event(self, event) -> bool:  # noqa: ANN001
        if event.type() == QEvent.ToolTip and isinstance(event, QHelpEvent):
            key = self._category_key_at(event.position().toPoint())
            if key is not None:
                QToolTip.showText(event.globalPos(), self._category_tooltip_text(key), self)
                return True
        return super().event(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        key = self._category_key_at(event.position().toPoint())
        self.setCursor(Qt.PointingHandCursor if key is not None else Qt.ArrowCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        key = self._category_key_at(event.position().toPoint())
        if key is not None:
            QToolTip.showText(event.globalPosition().toPoint(), self._category_tooltip_text(key), self)
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), Qt.transparent)
        self._axis_hit_regions = []

        scale = self._ui_scale()
        outer = self.rect().adjusted(int(round(12 * scale)), int(round(12 * scale)), int(round(-12 * scale)), int(round(-12 * scale)))
        header_height = int(round(48 * scale)) if self._show_header else 0
        chart_rect = QRectF(outer.left(), outer.top() + header_height, outer.width(), outer.height() - header_height)
        if chart_rect.width() <= 0 or chart_rect.height() <= 0:
            return

        if self._show_header:
            self._draw_header(painter, outer)
        self._draw_radar(painter, chart_rect)

    def _draw_header(self, painter: QPainter, outer: QRectF) -> None:
        scale = self._ui_scale()
        score = self._overall_score
        grade = grade_for_score(score)

        title_font = QFont(painter.font())
        title_font.setPointSizeF(9 * scale)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(self._muted)
        painter.drawText(QRectF(outer.left(), outer.top(), outer.width(), 18 * scale), Qt.AlignLeft | Qt.AlignVCenter, self._overall_label.upper())

        score_font = QFont(painter.font())
        score_font.setPointSizeF(22 * scale)
        score_font.setBold(True)
        painter.setFont(score_font)
        painter.setPen(self._text)
        painter.drawText(
            QRectF(outer.left(), outer.top() + (16 * scale), outer.width() - (72 * scale), 28 * scale),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"{float(score):.1f}" if score is not None else "--",
        )

        grade_font = QFont(painter.font())
        grade_font.setPointSizeF(28 * scale)
        grade_font.setBold(True)
        painter.setFont(grade_font)
        painter.setPen(self._accent)
        painter.drawText(QRectF(outer.right() - (64 * scale), outer.top(), 64 * scale, 44 * scale), Qt.AlignRight | Qt.AlignVCenter, grade)

    def _draw_radar(self, painter: QPainter, chart_rect: QRectF) -> None:
        scale = self._ui_scale()
        radius_scale = 0.37 if self._compact else 0.33
        height_scale = 0.36 if self._compact else 0.34
        center = QPointF(
            chart_rect.center().x(),
            chart_rect.center().y() + ((-8.0 if self._compact else 10.0) * scale),
        )
        radius = min(chart_rect.width() * radius_scale, chart_rect.height() * height_scale)
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

        if self._show_axis_labels or self._show_axis_grades:
            self._draw_axis_labels(painter, center, radius)

    def _draw_axis_labels(self, painter: QPainter, center: QPointF, radius: float) -> None:
        scale = self._ui_scale()
        label_font = QFont(painter.font())
        label_font.setPointSizeF((8 if self._compact else 11) * scale)
        label_font.setBold(True)
        score_font = QFont(painter.font())
        score_font.setPointSizeF((9 if self._compact else 12) * scale)
        score_font.setBold(True)
        grade_font = QFont(painter.font())
        grade_font.setPointSizeF((10 if self._compact else 15) * scale)
        grade_font.setBold(True)

        for index, (key, label) in enumerate(RADAR_CATEGORY_ORDER):
            anchor_offset = (24.0 if self._compact else 44.0) * scale
            anchor = self._point_for_axis(index, center, radius + anchor_offset)
            rect_width = (98.0 if self._compact else 156.0) * scale
            rect_height = (50.0 if self._compact else 78.0) * scale
            rect = QRectF(anchor.x() - (rect_width / 2.0), anchor.y() - (rect_height / 2.0), rect_width, rect_height)
            self._axis_hit_regions.append((rect, key))

            if self._show_axis_labels:
                painter.setFont(label_font)
                painter.setPen(self._text)
                painter.drawText(
                    QRectF(rect.left(), rect.top(), rect.width(), (18.0 if self._compact else 22.0) * scale),
                    Qt.AlignCenter,
                    COMPACT_LABELS.get(key, label) if self._compact else label,
                )

            value = self._scores.get(key)
            painter.setFont(score_font)
            painter.setPen(self._accent)
            painter.drawText(
                QRectF(
                    rect.left(),
                    rect.top() + ((14.0 if self._compact else 22.0) * scale),
                    rect.width(),
                    (18.0 if self._compact else 20.0) * scale,
                ),
                Qt.AlignCenter,
                f"{float(value):.1f}" if value is not None else "--",
            )

            if self._show_axis_grades:
                painter.setFont(grade_font)
                painter.setPen(self._muted)
                painter.drawText(
                    QRectF(
                        rect.left(),
                        rect.top() + ((30.0 if self._compact else 42.0) * scale),
                        rect.width(),
                        (16.0 if self._compact else 20.0) * scale,
                    ),
                    Qt.AlignCenter,
                    grade_for_score(self._scores.get(key)),
                )

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

    def _category_key_at(self, point: QPoint) -> Optional[str]:
        pointf = QPointF(point)
        for rect, key in self._axis_hit_regions:
            if rect.contains(pointf):
                return key
        return None

    def _category_tooltip_text(self, key: str) -> str:
        label = dict(RADAR_CATEGORY_ORDER).get(key, key.title())
        score = self._scores.get(key)
        lines = [f"{label}: {float(score):.1f} ({grade_for_score(score)})" if score is not None else f"{label}: --"]
        detail = self._category_details.get(key)
        if not detail:
            return "\n".join(lines)
        absolute_score = detail.get("absolute_score")
        display_percentile = detail.get("display_percentile")
        display_context = str(detail.get("display_context") or "").strip()
        if absolute_score is not None and score is not None and abs(float(absolute_score) - float(score)) >= 0.05:
            lines.append(f"Absolute score: {float(absolute_score):.1f}")
        if display_percentile is not None:
            lines.append(f"Relative percentile: {float(display_percentile):.1f}")
        if display_context:
            lines.append(f"Context: {display_context}")
        note = str(detail.get("score_note") or "").strip()
        if note:
            lines.extend(["", note])
        metrics = list(detail.get("metrics") or [])
        if metrics:
            lines.append("")
            for metric in metrics:
                metric_label = str(metric.get("label") or "Metric")
                metric_value = str(metric.get("value") or "--")
                metric_note = str(metric.get("note") or "").strip()
                if metric_note:
                    lines.append(f"{metric_label}: {metric_value} ({metric_note})")
                else:
                    lines.append(f"{metric_label}: {metric_value}")
        return "\n".join(lines)

    @staticmethod
    def _ui_scale() -> float:
        app = QApplication.instance()
        if app is None:
            return 1.0
        try:
            return max(0.75, min(2.0, float(app.property("arena_coach_zoom") or 1.0)))
        except (TypeError, ValueError):
            return 1.0


class DualCategoryRadarWidget(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.left_title = QLabel("Left Player")
        self.right_title = QLabel("Right Player")
        self.left_title.setStyleSheet("color: #7f94a8; font-weight: 600;")
        self.right_title.setStyleSheet("color: #7f94a8; font-weight: 600;")

        self.left_chart = CategoryRadarWidget("#7ce7ff", show_axis_grades=False)
        self.right_chart = CategoryRadarWidget("#ffb347", show_axis_grades=False)

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
        left_details: Optional[Mapping[str, Mapping[str, Any]]] = None,
        right_details: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> None:
        self.left_title.setText(left_label)
        self.right_title.setText(right_label)
        self.left_chart.set_scores(
            left_scores,
            overall_label="Overall",
            overall_score=left_overall,
            category_details=left_details,
        )
        self.right_chart.set_scores(
            right_scores,
            overall_label="Overall",
            overall_score=right_overall,
            category_details=right_details,
        )

    def clear(self) -> None:
        self.left_title.setText("Left Player")
        self.right_title.setText("Right Player")
        self.left_chart.clear()
        self.right_chart.clear()

    def refresh_scale(self) -> None:
        scale = CategoryRadarWidget._ui_scale()
        self.setMinimumHeight(int(round(360 * scale)))
        self.left_chart.refresh_scale()
        self.right_chart.refresh_scale()
        self.updateGeometry()
