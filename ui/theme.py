"""Colours, type and the stylesheet — in dark and light.

Two palettes, one stylesheet. The mode is "system", "dark" or "light"; system
asks Qt for the OS colour scheme and follows it when Windows changes.

Colour names are module attributes so widgets can read `theme.BG` at build time
and pick up whichever palette is current. Anything painted with an inline style
must therefore be rebuilt or restyled after a mode change — which is why nearly
all styling lives in `sheet()` under object names rather than inline.
"""

from __future__ import annotations

MODE_SYSTEM, MODE_DARK, MODE_LIGHT = "system", "dark", "light"

FONT = "Segoe UI"
MONO = "Consolas"

_DARK = dict(
    BG="#1e1e1e", BG_SIDE="#191919", BG_RAISE="#252525", BG_INPUT="#2b2b2b",
    BG_HOVER="#222222", BG_FOOT="#1b1b1b",
    FG="#f2f4f6", FG2="#a8adb3", FG3="#767b81", FG4="#5a5f65",
    LINE="#2f2f2f", LINE2="#3a3a3a", BORDER="#383838",
    RUN="#40c463", RUN_DIM="#1c3a27", RUN_FG="#9ae6b4",
    STOP="#e5534b", STOP_DIM="#3a2222", STOP_FG="#ff9b95",
    PEND="#e3b341", PEND_DIM="#3a3320", PEND_FG="#f2d489",
    PAUSED="#f2c188", PAUSED_DIM="#3a2c1c", PAUSED_FG="#f2c188",
    NONE="#8b8b8b", NONE_DIM="#2c2c2c", NONE_FG="#c2c2c2",
    ACCENT="#4aa3ff",
    SCROLL="#5a5a5a", SCROLL_HOVER="#7a7a7a",
    PRIMARY_DISABLED_FG="#56605a", PRIMARY_DISABLED_BG="#202320",
    PRIMARY_DISABLED_LINE="#2b302d",
)

_LIGHT = dict(
    BG="#f7f8fa", BG_SIDE="#eceef2", BG_RAISE="#ffffff", BG_INPUT="#ffffff",
    BG_HOVER="#eceff3", BG_FOOT="#f0f2f5",
    FG="#14181c", FG2="#4b535a", FG3="#79818a", FG4="#9aa2aa",
    LINE="#e2e6ea", LINE2="#ccd2d9", BORDER="#c8ced5",
    RUN="#1f9d4d", RUN_DIM="#e4f6ea", RUN_FG="#146c37",
    STOP="#c8372f", STOP_DIM="#fdecea", STOP_FG="#9d2820",
    PEND="#b07d0a", PEND_DIM="#fdf4e1", PEND_FG="#7d5806",
    PAUSED="#a56a12", PAUSED_DIM="#fbefe0", PAUSED_FG="#7d4f0c",
    NONE="#8a929a", NONE_DIM="#eef0f3", NONE_FG="#5c646c",
    ACCENT="#1668c8",
    SCROLL="#b9c0c8", SCROLL_HOVER="#98a1aa",
    PRIMARY_DISABLED_FG="#a3b3a8", PRIMARY_DISABLED_BG="#eef1ee",
    PRIMARY_DISABLED_LINE="#dbe2dc",
)

#: what the app is currently painted in — "dark" or "light", never "system"
resolved = MODE_DARK
mode = MODE_SYSTEM

globals().update(_DARK)


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------
def system_scheme() -> str:
    """What Windows is set to. Qt reports this and signals when it changes."""
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme() if hints else None
        if scheme == Qt.ColorScheme.Light:
            return MODE_LIGHT
        if scheme == Qt.ColorScheme.Dark:
            return MODE_DARK
    except Exception:
        pass
    # Fall back to the registry Windows itself uses for app colours.
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return MODE_LIGHT if light else MODE_DARK
    except OSError:
        return MODE_DARK


def resolve(requested: str) -> str:
    if requested == MODE_SYSTEM:
        return system_scheme()
    return requested if requested in (MODE_DARK, MODE_LIGHT) else MODE_DARK


def set_mode(requested: str) -> str:
    """Switch palettes. Returns the resolved mode."""
    global mode, resolved
    mode = requested if requested in (MODE_SYSTEM, MODE_DARK, MODE_LIGHT) else MODE_SYSTEM
    resolved = resolve(mode)
    globals().update(_LIGHT if resolved == MODE_LIGHT else _DARK)
    return resolved


def is_light() -> bool:
    return resolved == MODE_LIGHT


# ---------------------------------------------------------------------------
# Status colours
# ---------------------------------------------------------------------------
def chip(category: str):
    """(dot, chip background, chip foreground) for a status category."""
    table = {
        "running": (RUN, RUN_DIM, RUN_FG),
        "stopped": (STOP, STOP_DIM, STOP_FG),
        "pending": (PEND, PEND_DIM, PEND_FG),
        "paused":  (PAUSED, PAUSED_DIM, PAUSED_FG),
        "none":    (NONE, NONE_DIM, NONE_FG),
    }
    return table.get(category, table["none"])


def chip_style(category: str) -> str:
    _dot, bg, fg = chip(category)
    return (f"background:{bg}; color:{fg}; border:1px solid {LINE2};"
            f"border-radius:9px; padding:2px 9px; font-size:8pt; font-weight:600;")


def dot_colour(category: str) -> str:
    return chip(category)[0]


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------
def sheet() -> str:
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
        color: {FG3}; font-size: 8pt; font-weight: 600; letter-spacing: 1.4px;
    }}
    QLabel[role="hint"]     {{ color: {FG3}; font-size: 9pt; }}
    QLabel[role="mono"]     {{ color: {FG3}; font-family: "{MONO}"; font-size: 8.5pt; }}
    QLabel[role="strong"]   {{ color: {FG}; }}

    QPushButton {{
        background: transparent; border: 1px solid {LINE2}; border-radius: 5px;
        padding: 6px 12px; color: {FG2};
    }}
    QPushButton:hover   {{ background: {BG_HOVER}; color: {FG}; }}
    QPushButton:pressed {{ background: {BG_RAISE}; }}
    QPushButton:disabled {{ color: {FG4}; border-color: {LINE}; }}
    QPushButton[kind="primary"] {{
        color: {RUN_FG}; border-color: {RUN}; background: {RUN_DIM};
    }}
    QPushButton[kind="primary"]:hover {{ background: {RUN_DIM}; color: {RUN_FG}; }}
    /* A disabled primary button has to *look* disabled: without this the green
       fill stayed and Save looked clickable with nothing to save. */
    QPushButton[kind="primary"]:disabled {{
        color: {PRIMARY_DISABLED_FG}; border-color: {PRIMARY_DISABLED_LINE};
        background: {PRIMARY_DISABLED_BG};
    }}
    QPushButton[kind="quiet"] {{ border-color: transparent; color: {FG3}; }}
    QPushButton[kind="quiet"]:hover {{ color: {FG}; background: {BG_HOVER}; }}
    QPushButton[kind="danger"]:hover {{ color: {STOP_FG}; border-color: {STOP}; }}
    QPushButton[kind="action"] {{
        border: 1px solid {LINE2}; border-radius: 5px; padding: 2px;
        min-width: 26px; max-width: 26px; min-height: 24px; max-height: 24px;
        background: {BG_RAISE};
    }}
    QPushButton[kind="nav"] {{
        border: none; border-left: 2px solid transparent; border-radius: 0;
        padding: 9px 16px; text-align: left; color: {FG2}; background: transparent;
    }}
    QPushButton[kind="nav"]:hover   {{ background: {BG_HOVER}; color: {FG}; }}
    QPushButton[kind="nav"]:checked {{
        background: {BG_RAISE}; color: {FG}; border-left-color: {RUN};
    }}

    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {BG_INPUT}; border: 1px solid {LINE2}; border-radius: 4px;
        padding: 5px 8px; color: {FG}; selection-background-color: {RUN_DIM};
        selection-color: {FG};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
        border-color: {ACCENT};
    }}
    QSpinBox, QDoubleSpinBox {{ font-family: "{MONO}"; }}
    QComboBox::drop-down {{ border: none; width: 16px; }}
    QComboBox QAbstractItemView {{
        background: {BG_RAISE}; border: 1px solid {LINE2};
        selection-background-color: {RUN_DIM}; selection-color: {FG}; color: {FG};
    }}

    QCheckBox {{ spacing: 8px; color: {FG}; }}
    QCheckBox::indicator {{
        width: 15px; height: 15px; border-radius: 3px;
        border: 1px solid {LINE2}; background: {BG_INPUT};
    }}
    QCheckBox::indicator:checked {{ background: {RUN}; border-color: {RUN}; }}
    QCheckBox:disabled {{ color: {FG4}; }}

    QListWidget {{
        background: {BG_RAISE}; border: 1px solid {LINE}; border-radius: 5px;
        color: {FG};
    }}
    QListWidget::item {{ padding: 2px; }}
    QListWidget::item:selected {{ background: {RUN_DIM}; color: {FG}; }}

    QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px 1px; }}
    QScrollBar::handle:vertical {{
        background: {SCROLL}; border-radius: 4px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; }}
    QScrollBar::add-line, QScrollBar::sub-line,
    QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}

    QMenu {{ background: {BG_RAISE}; border: 1px solid {LINE2}; padding: 4px; }}
    QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 4px; color: {FG}; }}
    QMenu::item:selected {{ background: {BG_HOVER}; color: {FG}; }}
    QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 8px; }}

    QToolTip {{
        background: {BG_RAISE}; color: {FG}; border: 1px solid {LINE2};
        padding: 4px 7px;
    }}
    QMessageBox {{ background: {BG}; }}

    /* named surfaces, so a mode change is one setStyleSheet call */
    #shell, #card {{ background: {BG}; border: 1px solid {BORDER}; }}
    #row:hover, #steprow:hover {{ background: {BG_HOVER}; }}
    #navPanel {{ background: {BG_SIDE}; border-right: 1px solid {LINE}; }}
    #footerBar {{ background: {BG_FOOT}; border-top: 1px solid {LINE}; }}
    #columnHeader {{ background: {BG_RAISE}; }}
    #hline {{ background: {LINE}; border: none; }}
    #flyoutTitle {{ color: {FG}; font-size: 11.5pt; font-weight: 600; }}
    #sectionBar {{ background: {BG_SIDE}; }}
    """
