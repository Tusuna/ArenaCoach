"""Simple reorderable card container used inside fixed top-level tabs."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class CardContainer(QWidget):
    order_changed = Signal(list)
    sizes_changed = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self._cards: Dict[str, _CardWrapper] = {}
        self._default_order: list[str] = []
        self._order: list[str] = []
        self._customize_mode = False

        self.layout_root = QVBoxLayout(self)
        self.layout_root.setContentsMargins(0, 0, 0, 0)
        self.layout_root.setSpacing(10)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def add_card(self, card_id: str, title: str, widget: QWidget) -> None:
        wrapper = _CardWrapper(card_id, title, widget)
        wrapper.move_up_requested.connect(lambda card_id=card_id: self.move_card(card_id, -1))
        wrapper.move_down_requested.connect(lambda card_id=card_id: self.move_card(card_id, 1))
        wrapper.size_changed.connect(lambda _size, card_id=card_id: self._emit_sizes_changed())
        wrapper.set_customize_mode(self._customize_mode)
        self._cards[card_id] = wrapper
        self._default_order.append(card_id)
        self._order.append(card_id)
        self._rebuild()

    def set_customize_mode(self, enabled: bool) -> None:
        self._customize_mode = enabled
        for wrapper in self._cards.values():
            wrapper.set_customize_mode(enabled)

    def customize_mode(self) -> bool:
        return self._customize_mode

    def card_order(self) -> list[str]:
        return list(self._order)

    def default_order(self) -> list[str]:
        return list(self._default_order)

    def apply_order(self, order: Iterable[str]) -> bool:
        proposed = [str(item) for item in order]
        if set(proposed) != set(self._default_order) or len(proposed) != len(self._default_order):
            self._order = list(self._default_order)
            self._rebuild()
            return False
        self._order = proposed
        self._rebuild()
        return True

    def reset_order(self) -> None:
        self._order = list(self._default_order)
        self._rebuild()
        self.order_changed.emit(self.card_order())

    def apply_sizes(self, sizes: Mapping[str, int]) -> None:
        for card_id, wrapper in self._cards.items():
            wrapper.set_saved_height(sizes.get(card_id))
        self.refresh_layout()

    def reset_sizes(self) -> None:
        for wrapper in self._cards.values():
            wrapper.set_saved_height(None)
        self.refresh_layout()
        self.sizes_changed.emit(self.card_sizes())

    def card_sizes(self) -> dict[str, int]:
        sizes: dict[str, int] = {}
        for card_id, wrapper in self._cards.items():
            height = wrapper.saved_height()
            if height is not None:
                sizes[card_id] = height
        return sizes

    def set_card_height(self, card_id: str, height: Optional[int]) -> None:
        wrapper = self._cards.get(card_id)
        if wrapper is None:
            return
        wrapper.set_saved_height(height)
        self.refresh_layout()
        self.sizes_changed.emit(self.card_sizes())

    def move_card(self, card_id: str, delta: int) -> None:
        if card_id not in self._order:
            return
        index = self._order.index(card_id)
        next_index = index + delta
        if next_index < 0 or next_index >= len(self._order):
            return
        self._order[index], self._order[next_index] = self._order[next_index], self._order[index]
        self._rebuild()
        self.order_changed.emit(self.card_order())

    def set_card_title(self, card_id: str, title: str) -> None:
        wrapper = self._cards.get(card_id)
        if wrapper is not None:
            wrapper.set_title(title)

    def card_widget(self, card_id: str) -> Optional[QWidget]:
        wrapper = self._cards.get(card_id)
        return wrapper.content if wrapper is not None else None

    def refresh_layout(self) -> None:
        self.layout_root.invalidate()
        self.layout_root.activate()
        for wrapper in self._cards.values():
            wrapper.updateGeometry()
            wrapper.content.updateGeometry()
        self.updateGeometry()
        self.adjustSize()
        parent = self.parentWidget()
        while parent is not None:
            parent.updateGeometry()
            parent.adjustSize()
            parent = parent.parentWidget()

    def _emit_sizes_changed(self) -> None:
        self.sizes_changed.emit(self.card_sizes())

    def _rebuild(self) -> None:
        while self.layout_root.count():
            item = self.layout_root.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        for index, card_id in enumerate(self._order):
            wrapper = self._cards[card_id]
            wrapper.set_move_state(index > 0, index < len(self._order) - 1)
            self.layout_root.addWidget(wrapper)
        self.layout_root.addStretch()
        self.refresh_layout()


class _CardWrapper(QFrame):
    move_up_requested = Signal()
    move_down_requested = Signal()
    size_changed = Signal(int)

    def __init__(self, card_id: str, title: str, content: QWidget) -> None:
        super().__init__()
        self.card_id = card_id
        self.content = content
        self._saved_height: Optional[int] = None
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet("font-weight: 600;")
        self.move_up_button = QPushButton("Move Up")
        self.move_down_button = QPushButton("Move Down")
        self.move_up_button.clicked.connect(self.move_up_requested.emit)
        self.move_down_button.clicked.connect(self.move_down_requested.emit)
        self.resize_handle = _ResizeHandle()
        self.resize_handle.drag_delta.connect(self._resize_by_delta)
        self.resize_handle.drag_finished.connect(self._emit_size_changed)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.title_label)
        header.addStretch()
        header.addWidget(self.move_up_button)
        header.addWidget(self.move_down_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addLayout(header)
        layout.addWidget(content)
        layout.addWidget(self.resize_handle)

        self.setObjectName("cardWrapper")
        self.setFrameShape(QFrame.StyledPanel)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.set_customize_mode(False)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_customize_mode(self, enabled: bool) -> None:
        self.move_up_button.setVisible(enabled)
        self.move_down_button.setVisible(enabled)

    def set_move_state(self, can_move_up: bool, can_move_down: bool) -> None:
        self.move_up_button.setEnabled(can_move_up)
        self.move_down_button.setEnabled(can_move_down)

    def saved_height(self) -> Optional[int]:
        return self._saved_height

    def set_saved_height(self, height: Optional[int]) -> None:
        if height is None:
            self._saved_height = None
            self.setMinimumHeight(0)
            self.setMaximumHeight(16777215)
            self.updateGeometry()
            self.adjustSize()
            return
        normalized = max(80, int(height))
        self._saved_height = normalized
        self.setFixedHeight(normalized)
        self.updateGeometry()

    def _resize_by_delta(self, delta_y: int) -> None:
        base_height = self._saved_height or self.height()
        self.set_saved_height(base_height + delta_y)

    def _emit_size_changed(self) -> None:
        self.size_changed.emit(self._saved_height or self.height())


class _ResizeHandle(QFrame):
    drag_delta = Signal(int)
    drag_finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._dragging = False
        self._last_global_pos = QPoint()
        self.setCursor(Qt.SizeVerCursor)
        self.setFixedHeight(14)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("border-top: 1px solid #263343; color: #7f94a8; padding-top: 2px;")

        label = QLabel("Resize")
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("color: #7f94a8; font-size: 11px;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch()
        layout.addWidget(label)
        layout.addStretch()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self._dragging = True
        self._last_global_pos = event.globalPosition().toPoint()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self._dragging:
            return super().mouseMoveEvent(event)
        current = event.globalPosition().toPoint()
        delta = current.y() - self._last_global_pos.y()
        if delta:
            self.drag_delta.emit(delta)
            self._last_global_pos = current
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._dragging and event.button() == Qt.LeftButton:
            self._dragging = False
            self.drag_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
