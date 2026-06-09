"""GUI entry point."""

from __future__ import annotations

from pathlib import Path
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from arena_coach.config import ConfigError, load_config
from arena_coach.database import initialize_database
from arena_coach.gui.main_window import MainWindow
from arena_coach.gui.theme import dark_theme


class LoadingScreen(QWidget):
    def __init__(self, app: QApplication) -> None:
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self._app = app
        self._progress = 0.0
        self._status = "Starting Arena Coach..."
        self._detail = "Preparing your local workspace..."
        self.setFixedSize(660, 320)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

    def show_loading(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self._app.processEvents()

    def advance(self, target: float, status: str, detail: str, *, duration_ms: int = 240) -> None:
        target = max(0.0, min(100.0, target))
        self._status = status
        self._detail = detail
        start = self._progress
        steps = max(10, int(abs(target - start) * 1.8))
        delay = max(0.006, duration_ms / 1000 / steps)
        for index in range(1, steps + 1):
            fraction = index / steps
            eased = 1 - (1 - fraction) * (1 - fraction)
            self._progress = start + (target - start) * eased
            self.update()
            self._app.processEvents()
            time.sleep(delay)
        self._progress = target
        self.update()
        self._app.processEvents()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0b0f15"))

        card = QRectF(18, 18, self.width() - 36, self.height() - 36)
        card_path = QPainterPath()
        card_path.addRoundedRect(card, 16, 16)
        painter.fillPath(card_path, QColor("#10141b"))
        painter.setPen(QPen(QColor("#263343"), 2))
        painter.drawPath(card_path)

        accent = QRectF(card.left() + 24, card.top() + 24, 74, 10)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#55d6ff"))
        painter.drawRoundedRect(accent, 5, 5)

        painter.setPen(QColor("#7ce7ff"))
        painter.setFont(QFont("Segoe UI", 24, QFont.Bold))
        painter.drawText(int(card.left() + 24), int(card.top() + 86), "Arena Coach")

        painter.setPen(QColor("#8a97aa"))
        painter.setFont(QFont("Segoe UI", 11))
        painter.drawText(
            int(card.left() + 24),
            int(card.top() + 116),
            "Capture, review, and advanced stats are loading...",
        )

        bar_rect = QRectF(card.left() + 24, card.top() + 170, card.width() - 48, 18)
        painter.setBrush(QColor("#182231"))
        painter.setPen(QPen(QColor("#365069"), 1))
        painter.drawRoundedRect(bar_rect, 9, 9)

        fill_width = max(18.0, (bar_rect.width() - 4) * (self._progress / 100.0))
        fill_rect = QRectF(bar_rect.left() + 2, bar_rect.top() + 2, min(fill_width, bar_rect.width() - 4), bar_rect.height() - 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#55d6ff"))
        painter.drawRoundedRect(fill_rect, 7, 7)

        painter.setPen(QColor("#d8e3f0"))
        painter.setFont(QFont("Segoe UI", 12, QFont.Bold))
        painter.drawText(
            QRectF(card.left() + 24, card.top() + 206, card.width() - 48, 28),
            Qt.AlignLeft | Qt.AlignVCenter,
            self._status,
        )

        painter.setPen(QColor("#8a97aa"))
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(
            QRectF(card.left() + 24, card.top() + 238, card.width() - 160, 42),
            Qt.TextWordWrap,
            self._detail,
        )

        painter.setPen(QColor("#7ce7ff"))
        painter.setFont(QFont("Consolas", 14, QFont.Bold))
        painter.drawText(
            QRectF(card.right() - 96, card.top() + 228, 72, 30),
            Qt.AlignRight | Qt.AlignVCenter,
            f"{int(round(self._progress)):02d}%",
        )


def create_application(argv: list[str] | None = None) -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Arena Coach")
    app.setStyleSheet(dark_theme())
    return app


def main() -> int:
    app = create_application()
    loading = LoadingScreen(app)
    loading.show_loading()
    loading.advance(10, "Booting interface...", "Preparing the desktop shell and loading theme.")
    try:
        loading.advance(32, "Loading settings...", "Reading your local Arena Coach configuration and paths.")
        config = load_config()
        loading.advance(58, "Opening database...", "Checking schema, local profile state, and saved layout data.")
        initialize_database(config.database_path)
        loading.advance(82, "Building workspace...", "Preparing tabs, side panels, card layouts, and review tools.")
        window = MainWindow(config)
        app.processEvents()
        loading.advance(96, "Finalizing startup...", "Finishing the first connection check timers and getting the app ready.")
    except ConfigError as exc:
        loading.close()
        QMessageBox.critical(None, "Arena Coach Config Error", str(exc))
        return 2
    except Exception as exc:  # noqa: BLE001
        loading.close()
        QMessageBox.critical(
            None,
            "Arena Coach Startup Error",
            "Arena Coach hit an unexpected startup error.\n\n"
            f"{exc}\n\n"
            "If this build was copied from another PC, try launching again after this update. "
            "If it still fails, delete arena_coach_config.json in the ArenaCoach folder and relaunch.",
        )
        return 1
    window.show()
    window.raise_()
    window.activateWindow()
    app.processEvents()
    loading.advance(100, "Arena Coach ready", "Your workspace is loaded. Opening the main window now.", duration_ms=180)
    loading.close()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
