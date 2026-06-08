"""Match review container with guided and advanced modes."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from arena_coach.gui.widgets.guided_match_review_panel import GuidedMatchReviewPanel
from arena_coach.gui.widgets.match_review_panel import MatchReviewPanel
from arena_coach.services.match_service import MatchService


class ReviewWorkspace(QWidget):
    data_changed = Signal()
    message = Signal(str)
    error = Signal(str)

    def __init__(self, service: MatchService, guided_default: bool = True) -> None:
        super().__init__()
        self.guided_panel = GuidedMatchReviewPanel(service)
        self.advanced_panel = MatchReviewPanel(service)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.guided_panel, "Guided Review")
        self.tabs.addTab(self.advanced_panel, "Advanced Review")

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)

        for panel in (self.guided_panel, self.advanced_panel):
            panel.data_changed.connect(self.data_changed.emit)
            panel.message.connect(self.message.emit)
            panel.error.connect(self.error.emit)

        self.set_guided_mode(guided_default)

    def set_guided_mode(self, enabled: bool) -> None:
        self.tabs.setCurrentWidget(self.guided_panel if enabled else self.advanced_panel)

    def reload(self) -> None:
        current = self.current_match_id()
        self.guided_panel.reload()
        self.advanced_panel.reload()
        if current is not None:
            self.select_match(current)

    def select_match(self, match_id: int) -> None:
        self.guided_panel.select_match(match_id)
        self.advanced_panel.select_match(match_id)

    def current_match_id(self) -> Optional[int]:
        current = self.tabs.currentWidget()
        if hasattr(current, "current_match_id"):
            return current.current_match_id()
        return self.guided_panel.current_match_id()

    def finalize_current_match(self) -> None:
        current = self.tabs.currentWidget()
        if hasattr(current, "finalize_current_match"):
            current.finalize_current_match()
        elif hasattr(current, "_finalize"):
            current._finalize()
