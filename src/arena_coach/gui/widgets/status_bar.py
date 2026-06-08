"""Bottom status bar."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QStatusBar


class ArenaStatusBar(QStatusBar):
    def __init__(self) -> None:
        super().__init__()
        self.connection_label = QLabel("connection: unknown")
        self.capture_label = QLabel("capture: stopped")
        self.raw_log_label = QLabel("match log: none")
        self.action_label = QLabel("ready")
        self.addPermanentWidget(self.connection_label)
        self.addPermanentWidget(self.capture_label)
        self.addPermanentWidget(self.raw_log_label, 1)
        self.addWidget(self.action_label, 1)

    def set_connection(self, text: str, ok: bool | None = None) -> None:
        self.connection_label.setText(f"connection: {text}")
        self.connection_label.setProperty("class", "success" if ok else "error" if ok is False else "muted")
        self.connection_label.style().unpolish(self.connection_label)
        self.connection_label.style().polish(self.connection_label)

    def set_capture(self, running: bool) -> None:
        self.capture_label.setText("capture: running" if running else "capture: stopped")
        self.capture_label.setProperty("class", "success" if running else "muted")
        self.capture_label.style().unpolish(self.capture_label)
        self.capture_label.style().polish(self.capture_label)

    def set_raw_log(self, path: str | None) -> None:
        self.raw_log_label.setText(f"match log: {path or 'none'}")

    def set_action(self, text: str, error: bool = False) -> None:
        self.action_label.setText(text)
        self.action_label.setProperty("class", "error" if error else "success")
        self.action_label.style().unpolish(self.action_label)
        self.action_label.style().polish(self.action_label)
