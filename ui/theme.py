"""One place for colours, type and the stylesheet.

The palette is the one the tray flyout has always used; what changes with Qt is
that a stylesheet expresses it once instead of every widget being hand-painted.
Glyphs stay inside the BMP — an astral emoji used to cost ~600 ms of font
fallback per window on Windows, and that lesson outlives the toolkit.
"""

BG        = "#1e1e1e"
BG_SIDE   = "#191919"
BG_RAISE  = "#252525"
BG_INPUT  = "#2b2b2b"
BG_HOVER  = "#222222"
FG        = "#f2f4f6"
FG2       = "#a8adb3"
FG3       = "#767b81"
FG4       = "#5a5f65"
LINE      = "#2f2f2f"
LINE2     = "#3a3a3a"

RUN       = "#40c463"
RUN_DIM   = "#1c3a27"
RUN_FG    = "#9ae6b4"
STOP      = "#e5534b"
STOP_DIM  = "#3a2222"
PEND      = "#e3b341"
PEND_DIM  = "#3a3320"
PAUSED    = "#f2c188"
NONE      = "#8b8b8b"
ACCENT    = "#4aa3ff"

FONT      = "Segoe UI"
MONO      = "Consolas"

#: status category -> (dot colour, chip background, chip foreground)
CHIP = {
    "running": (RUN,    RUN_DIM,  RUN_FG),
    "stopped": (STOP,   STOP_DIM, "#ff9b95"),
    "pending": (PEND,   PEND_DIM, "#f2d489"),
    "paused":  (PAUSED, "#3a2c1c", "#f2c188"),
    "none":    (NONE,   "#2c2c2c", "#c2c2c2"),
}


def sheet() -> str:
    """Application-wide stylesheet."""
    return f"""
    QWidget {{
        background: {BG};
        color: {FG2};
        font-family: "{FONT}";
        font-size: 10pt;
    }}
    QLabel {{ background: transparent; }}
    QLabel[role="title"]    {{ color: {FG}; font-size: 13pt; font-weight: 600; }}
    QLabel[role="h2"]       {{ color: {FG}; font-size: 11.5pt; font-weight: 600; }}
    QLabel[role="section"]  {{
        color: {FG3}; font-size: 8pt; font-weight: 600;
        letter-spacing: 1.4px; text-transform: uppercase;
    }}
    QLabel[role="hint"]     {{ color: {FG3}; font-size: 9pt; }}
    QLabel[role="mono"]     {{ color: {FG3}; font-family: "{MONO}"; font-size: 8.5pt; }}
    QLabel[role="strong"]   {{ color: {FG}; }}

    QPushButton {{
        background: transparent;
        border: 1px solid {LINE2};
        border-radius: 5px;
        padding: 6px 12px;
        color: {FG2};
    }}
    QPushButton:hover  {{ background: #2b2b2b; color: {FG}; }}
    QPushButton:pressed{{ background: #333333; }}
    QPushButton:disabled {{ color: #4a4a4a; border-color: #303030; }}
    QPushButton[kind="primary"] {{
        color: {RUN_FG}; border-color: #2c5238; background: {RUN_DIM};
    }}
    QPushButton[kind="primary"]:hover {{ background: #204631; }}
    QPushButton[kind="quiet"] {{ border-color: transparent; color: {FG3}; }}
    QPushButton[kind="quiet"]:hover {{ color: {FG}; background: #262626; }}
    QPushButton[kind="danger"]:hover {{ color: #ff9b95; border-color: #542a2a; }}
    QPushButton[kind="action"] {{
        border: 1px solid {LINE2}; border-radius: 5px; padding: 2px;
        min-width: 26px; max-width: 26px; min-height: 24px; max-height: 24px;
        background: #2d2d2d;
    }}
    QPushButton[kind="nav"] {{
        border: none; border-left: 2px solid transparent; border-radius: 0;
        padding: 9px 16px; text-align: left; color: {FG2};
    }}
    QPushButton[kind="nav"]:hover  {{ background: #1f1f1f; color: {FG}; }}
    QPushButton[kind="nav"]:checked{{
        background: #232323; color: {FG}; border-left-color: {RUN};
    }}

    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {BG_INPUT};
        border: 1px solid {LINE2};
        border-radius: 4px;
        padding: 5px 8px;
        color: {FG};
        selection-background-color: {RUN_DIM};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {ACCENT};
    }}
    QSpinBox, QDoubleSpinBox {{ font-family: "{MONO}"; }}
    QComboBox::drop-down {{ border: none; width: 16px; }}
    QComboBox QAbstractItemView {{
        background: {BG_INPUT}; border: 1px solid {LINE2};
        selection-background-color: {RUN_DIM}; color: {FG};
    }}

    QCheckBox {{ spacing: 8px; color: {FG}; }}
    QCheckBox::indicator {{
        width: 15px; height: 15px; border-radius: 3px;
        border: 1px solid {LINE2}; background: {BG_INPUT};
    }}
    QCheckBox::indicator:checked {{ background: {RUN}; border-color: {RUN}; }}
    QCheckBox:disabled {{ color: {FG4}; }}

    QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; border: none; }}
    QScrollBar:vertical {{
        background: transparent; width: 9px; margin: 2px 1px;
    }}
    QScrollBar::handle:vertical {{
        background: #5a5a5a; border-radius: 4px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: #7a7a7a; }}
    QScrollBar::add-line, QScrollBar::sub-line,
    QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}

    QMenu {{
        background: {BG_RAISE}; border: 1px solid {LINE2}; padding: 4px;
    }}
    QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 4px; }}
    QMenu::item:selected {{ background: #333333; color: {FG}; }}
    QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 8px; }}

    QToolTip {{
        background: {BG_RAISE}; color: {FG};
        border: 1px solid {LINE2}; padding: 4px 7px;
    }}
    """


def chip_style(category: str) -> str:
    _dot, bg, fg = CHIP.get(category, CHIP["none"])
    return (f"background:{bg}; color:{fg}; border:1px solid {LINE2};"
            f"border-radius:9px; padding:2px 9px; font-size:8pt; font-weight:600;")


def dot_colour(category: str) -> str:
    return CHIP.get(category, CHIP["none"])[0]
