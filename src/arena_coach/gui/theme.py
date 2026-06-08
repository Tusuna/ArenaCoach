"""Basic dark QSS theme for Arena Coach."""

from __future__ import annotations


def dark_theme() -> str:
    return """
    QWidget {
        background: #10141b;
        color: #d8e3f0;
        font-family: "Segoe UI", Arial, sans-serif;
        font-size: 12px;
    }
    QMainWindow {
        background: #0b0f15;
    }
    QTabWidget::pane, QGroupBox {
        border: 1px solid #263343;
        border-radius: 4px;
        margin-top: 8px;
    }
    QGroupBox::title {
        color: #7ce7ff;
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 4px;
    }
    QTabBar::tab {
        background: #151b24;
        color: #aebbd0;
        padding: 8px 14px;
        border: 1px solid #263343;
        border-bottom: none;
    }
    QTabBar::tab:selected {
        background: #1d2733;
        color: #7ce7ff;
    }
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
        background: #0d1219;
        border: 1px solid #293747;
        border-radius: 3px;
        padding: 5px;
        selection-background-color: #2563eb;
    }
    QPushButton {
        background: #1b2735;
        border: 1px solid #365069;
        border-radius: 4px;
        padding: 6px 10px;
        color: #d8e3f0;
    }
    QPushButton:hover {
        background: #243348;
        border-color: #55d6ff;
    }
    QPushButton:pressed {
        background: #182232;
    }
    QPushButton:disabled {
        color: #607086;
        border-color: #202a36;
    }
    QTableWidget, QTableView {
        background: #0d1219;
        alternate-background-color: #121923;
        border: 1px solid #263343;
        gridline-color: #263343;
    }
    QHeaderView::section {
        background: #182231;
        color: #7ce7ff;
        border: 1px solid #263343;
        padding: 5px;
    }
    QScrollBar:vertical, QScrollBar:horizontal {
        background: #0b0f15;
        width: 12px;
        height: 12px;
    }
    QScrollBar::handle {
        background: #2d3b4e;
        border-radius: 4px;
    }
    QLabel[class="blueTeam"] { color: #5da8ff; }
    QLabel[class="orangeTeam"] { color: #ff9d4d; }
    QLabel[class="success"] { color: #5ff0a0; }
    QLabel[class="error"] { color: #ff6b6b; }
    QLabel[class="muted"] { color: #8a97aa; }
    """
