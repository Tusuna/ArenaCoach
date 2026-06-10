"""Basic dark QSS theme for Arena Coach."""

from __future__ import annotations


def _px(value: float, scale: float) -> int:
    return max(1, int(round(float(value) * float(scale))))


def dark_theme(scale: float = 1.0) -> str:
    scale = max(0.75, min(2.0, float(scale)))
    font_size = _px(12, scale)
    pane_radius = _px(4, scale)
    pane_margin = _px(8, scale)
    title_left = _px(10, scale)
    title_padding = _px(4, scale)
    tab_pad_v = _px(8, scale)
    tab_pad_h = _px(14, scale)
    input_radius = _px(3, scale)
    input_pad = _px(5, scale)
    button_radius = _px(4, scale)
    button_pad_v = _px(6, scale)
    button_pad_h = _px(10, scale)
    header_pad = _px(5, scale)
    scrollbar = _px(12, scale)
    handle_radius = _px(4, scale)
    return f"""
    QWidget {{
        background: #10141b;
        color: #d8e3f0;
        font-family: "Segoe UI", Arial, sans-serif;
        font-size: {font_size}px;
    }}
    QMainWindow {{
        background: #0b0f15;
    }}
    QTabWidget::pane, QGroupBox {{
        border: 1px solid #263343;
        border-radius: {pane_radius}px;
        margin-top: {pane_margin}px;
    }}
    QGroupBox::title {{
        color: #7ce7ff;
        subcontrol-origin: margin;
        left: {title_left}px;
        padding: 0 {title_padding}px;
    }}
    QTabBar::tab {{
        background: #151b24;
        color: #aebbd0;
        padding: {tab_pad_v}px {tab_pad_h}px;
        border: 1px solid #263343;
        border-bottom: none;
    }}
    QTabBar::tab:selected {{
        background: #1d2733;
        color: #7ce7ff;
    }}
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: #0d1219;
        border: 1px solid #293747;
        border-radius: {input_radius}px;
        padding: {input_pad}px;
        selection-background-color: #2563eb;
    }}
    QPushButton {{
        background: #1b2735;
        border: 1px solid #365069;
        border-radius: {button_radius}px;
        padding: {button_pad_v}px {button_pad_h}px;
        color: #d8e3f0;
    }}
    QPushButton:hover {{
        background: #243348;
        border-color: #55d6ff;
    }}
    QPushButton:pressed {{
        background: #182232;
    }}
    QPushButton:disabled {{
        color: #607086;
        border-color: #202a36;
    }}
    QTableWidget, QTableView {{
        background: #0d1219;
        alternate-background-color: #121923;
        border: 1px solid #263343;
        gridline-color: #263343;
    }}
    QHeaderView::section {{
        background: #182231;
        color: #7ce7ff;
        border: 1px solid #263343;
        padding: {header_pad}px;
    }}
    QScrollBar:vertical, QScrollBar:horizontal {{
        background: #0b0f15;
        width: {scrollbar}px;
        height: {scrollbar}px;
    }}
    QScrollBar::handle {{
        background: #2d3b4e;
        border-radius: {handle_radius}px;
    }}
    QLabel[class="blueTeam"] {{ color: #5da8ff; }}
    QLabel[class="orangeTeam"] {{ color: #ff9d4d; }}
    QLabel[class="success"] {{ color: #5ff0a0; }}
    QLabel[class="error"] {{ color: #ff6b6b; }}
    QLabel[class="muted"] {{ color: #8a97aa; }}
    """
