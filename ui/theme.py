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

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
# One place for spacing, type and the handful of fixed sizes. These were literals
# repeated across five files, which is how a 14 becomes a 12 in one row and
# nowhere else. Name them and the drift has nowhere to start.
SP_2, SP_4, SP_6, SP_8, SP_10, SP_12, SP_14, SP_16 = 2, 4, 6, 8, 10, 12, 14, 16

#: (left, top, right, bottom) for the recurring containers
ROW_PAD = (SP_14, SP_8, SP_14, SP_8)        # a service or stack row
BAR_PAD = (SP_12, 5, SP_14, 5)              # a section heading bar
PAGE_PAD = (SP_16, SP_14, SP_16, SP_14)     # dialog bodies
FOOT_PAD = (SP_10, 9, SP_10, 9)             # the footer, and the bulk bar
PANEL_PAD = (28, 24, 28, 20)                # a page inside the main panel

#: Type, in points. Roles live in sheet(); these are the sizes those roles use,
#: so there is one ladder rather than eight literals scattered through the QSS.
T_TITLE, T_H2, T_BODY = 13, 11.5, 10
T_MONO, T_HINT, T_SECTION = 8.5, 9, 8

#: Fixed sizes that two or more places have to agree on
ACTION_BTN = (26, 24)                       # the per-row action buttons
FLYOUT_WIDTH = 466                          # anchored above the tray icon
COL_STATUS_W, COL_ACTIONS_W = 74, 96        # list column headers
CHIP_MIN_W = 70                             # so chips line up down a column

#: Glyphs. All in the Basic Multilingual Plane, deliberately: anything above
#: U+FFFF pulls in Windows' colour-emoji font and costs ~600 ms per process.
GLYPH_START, GLYPH_STOP, GLYPH_RESTART = "▶", "■", "↻"
GLYPH_KILL, GLYPH_REFRESH, GLYPH_SERVICES = "✕", "↻", "▤"
GLYPH_FOLD, GLYPH_FOLDED, GLYPH_GRIP = "▾", "▸", "⁝"
GLYPH_SETTINGS, GLYPH_CRUMB, GLYPH_CHEVRON = "⚙", "›", "▾"

_DARK = dict(
    # Surfaces, and the order they stack in. There used to be six near-blacks
    # doing this with three names for one job, and they drifted apart: the column
    # header ended up *lighter* than the rows while the category headings were
    # *darker*, with the footer a third shade. One ladder now, one direction:
    #   content  <  raised  <  bands  <  editable
    BG="#191919",          # the field: rows, page background
    BG_RAISE="#212121",    # floats on the field: tables, menus, small buttons
    BG_BAND="#242424",     # chrome: column headers, section bars, footer, nav
    BG_INPUT="#2e2e2e",    # where you type, so the brightest
    BG_HOVER="#222222",
    FG="#f2f4f6", FG2="#a8adb3", FG3="#767b81", FG4="#5a5f65",
    LINE="#303030", LINE2="#3d3d3d", BORDER="#3d3d3d",
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
    # The same three roles. White is the ceiling here, so the ladder can only run
    # the other way — content is white and the bands are the grey. What matters is
    # that each theme is internally consistent, which is what was broken.
    BG="#ffffff",
    BG_RAISE="#ffffff",    # distinguished by its border, not by another shade
    BG_BAND="#eef1f4",
    BG_INPUT="#ffffff",
    BG_HOVER="#f3f6f8",
    FG="#14181c", FG2="#4b535a", FG3="#79818a", FG4="#9aa2aa",
    LINE="#e3e7eb", LINE2="#ccd2d9", BORDER="#c8ced5",
    RUN="#158040", RUN_DIM="#dbf2e4", RUN_FG="#0d5228",
    STOP="#b32f27", STOP_DIM="#fbe3e1", STOP_FG="#7d1d17",
    PEND="#946609", PEND_DIM="#fcedd2", PEND_FG="#5f4204",
    PAUSED="#8c5a0f", PAUSED_DIM="#f8e7d3", PAUSED_FG="#603905",
    NONE="#767e86", NONE_DIM="#e7eaee", NONE_FG="#454c53",
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


CHIP_CATEGORIES = ("running", "stopped", "pending", "paused", "none")


def chip_rules() -> str:
    """QSS for every chip state, keyed on a `cat` property.

    Generated rather than applied per widget: a chip used to be a bare QLabel with
    an inline stylesheet at each of seven call sites, so a theme change needed each
    one rebuilt by hand. Now the sheet carries the colours and the widget carries
    only which state it is in.

    In light mode the pale fill needs the status colour as its border to stay
    legible — a grey border on near-white washed it out.
    """
    common = (f"border-radius:9px; padding:2px 9px; "
              f"font-size:{T_SECTION}pt; font-weight:600;")
    out = [f'QLabel[chip="true"] {{ {common} }}']
    for category in CHIP_CATEGORIES:
        dot, bg, fg = chip(category)
        border = dot if is_light() else LINE2
        out.append(f'QLabel[chip="true"][cat="{category}"] {{ background:{bg}; '
                   f'color:{fg}; border:1px solid {border}; }}')
    return "\n    ".join(out)


def chip_style(category: str) -> str:
    """The same thing as an inline string, for the one case that cannot use the
    sheet: a chip inside a widget that is itself styled inline."""
    dot, bg, fg = chip(category)
    border = dot if is_light() else LINE2
    return (f"background:{bg}; color:{fg}; border:1px solid {border};"
            f"border-radius:9px; padding:2px 9px; font-size:{T_SECTION}pt;"
            f" font-weight:600;")


def dot_colour(category: str) -> str:
    return chip(category)[0]


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------
_glyph_dir = None


def _glyph(kind: str, colour: str) -> str:
    """Path to a tick or dash image, for use inside the stylesheet.

    Qt stylesheets take `url(path)` and nothing else — no data URIs — so these
    are written once to a temp file per colour and referenced by path. They are
    needed because styling ::indicator at all replaces the native check mark.
    """
    global _glyph_dir
    import os
    import tempfile
    from PySide6.QtCore import QRectF, Qt as _Qt
    from PySide6.QtGui import QPainter, QPen, QPixmap

    if _glyph_dir is None:
        _glyph_dir = os.path.join(tempfile.gettempdir(), "service-officer-glyphs")
        os.makedirs(_glyph_dir, exist_ok=True)
    path = os.path.join(_glyph_dir, f"{kind}-{colour.lstrip('#')}.png")
    if not os.path.exists(path):
        pix = QPixmap(28, 28)                  # 2x for high-DPI crispness
        pix.fill(_Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(colour)
        pen.setWidthF(3.4)
        pen.setCapStyle(_Qt.RoundCap)
        p.setPen(pen)
        if kind == "tick":
            p.drawLine(7, 15, 12, 20)
            p.drawLine(12, 20, 21, 8)
        elif kind == "chevron":
            p.drawLine(7, 11, 14, 18)
            p.drawLine(14, 18, 21, 11)
        else:
            p.drawLine(7, 14, 21, 14)
        p.end()
        pix.save(path)
    return path.replace("\\", "/")             # QSS wants forward slashes


def sheet() -> str:
    TICK_URL = _glyph("tick", "#ffffff")
    DASH_URL = _glyph("dash", RUN)
    CHEVRON_URL = _glyph("chevron", FG2)
    CHIP_RULES = chip_rules()
    return f"""
    QWidget {{
        background: {BG};
        color: {FG2};
        font-family: "{FONT}";
        font-size: {T_BODY}pt;
    }}
    QLabel {{ background: transparent; }}
    QLabel[role="title"]    {{ color: {FG}; font-size: {T_TITLE}pt; font-weight: 600; }}
    QLabel[role="h2"]       {{ color: {FG}; font-size: {T_H2}pt; font-weight: 600; }}
    QLabel[role="section"]  {{
        color: {FG3}; font-size: {T_SECTION}pt; font-weight: 600; letter-spacing: 1.4px;
    }}
    QLabel[role="hint"]     {{ color: {FG3}; font-size: {T_HINT}pt; }}
    QLabel[role="mono"]     {{ color: {FG3}; font-family: "{MONO}"; font-size: {T_MONO}pt; }}
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
        min-width: {ACTION_BTN[0]}px; max-width: {ACTION_BTN[0]}px;
        min-height: {ACTION_BTN[1]}px; max-height: {ACTION_BTN[1]}px;
        background: {BG_RAISE};
    }}
    /* Only the glyph is red. A red border as well made every running service
       look like it was in trouble, when the button is just an option. */
    QPushButton[kind="kill"] {{
        border: 1px solid {LINE2}; border-radius: 5px; padding: 2px;
        min-width: {ACTION_BTN[0]}px; max-width: {ACTION_BTN[0]}px;
        min-height: {ACTION_BTN[1]}px; max-height: {ACTION_BTN[1]}px;
        background: {BG_RAISE}; color: {STOP}; font-weight: 700;
    }}
    QPushButton[kind="kill"]:hover {{
        background: {STOP}; color: #ffffff; border-color: {STOP};
    }}
    /* Same red, but a button with a word in it — "kill" is pinned to 26px
       because in a service row it holds a single glyph. */
    QPushButton[kind="destructive"] {{
        border: 1px solid {STOP}; background: {STOP_DIM}; color: {STOP_FG};
        font-weight: 600;
    }}
    QPushButton[kind="destructive"]:hover {{ background: {STOP}; color: #ffffff; }}
    QPushButton[kind="kill"]:disabled {{
        border-color: {LINE}; background: transparent; color: {FG4};
    }}
    /* A tab strip inside a page: no box, an underline when chosen. Distinct
       from the left nav so the two levels don't read as the same control. */
    QPushButton[kind="tab"] {{
        border: none; border-bottom: 2px solid transparent; border-radius: 0;
        padding: 7px 14px; color: {FG3}; background: transparent;
        font-weight: 600;
    }}
    QPushButton[kind="tab"]:hover   {{ color: {FG}; }}
    QPushButton[kind="tab"]:checked {{ color: {FG}; border-bottom-color: {RUN}; }}
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
    /* A combo box has to look like one. Styling the frame above gave it the same
       box as a text field, so the only thing left to say "there is a list behind
       this" was the arrow — and Qt draws no arrow once ::drop-down is styled.
       There is also a divider, because on a wide combo the arrow alone sits so
       far from the text that it reads as a separate control. */
    QComboBox::drop-down {{
        subcontrol-origin: padding; subcontrol-position: center right;
        width: 24px; border: none; border-left: 1px solid {LINE2};
    }}
    QComboBox::down-arrow {{ image: url("{CHEVRON_URL}"); width: 9px; height: 9px; }}
    QComboBox::down-arrow:on {{ top: 1px; }}
    QComboBox:hover {{ border-color: {LINE2}; background: {BG_HOVER}; }}
    QComboBox:disabled::drop-down {{ border-left-color: {LINE}; }}
    QComboBox QAbstractItemView {{
        background: {BG_RAISE}; border: 1px solid {LINE2};
        selection-background-color: {RUN_DIM}; selection-color: {FG}; color: {FG};
    }}

    QCheckBox {{ spacing: 8px; color: {FG}; }}
    QCheckBox::indicator {{
        width: 15px; height: 15px; border-radius: 3px;
        border: 1px solid {LINE2}; background: {BG_INPUT};
    }}
    QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
    /* Styling the indicator at all replaces the native one, tick included, so
       the check has to be drawn back in — otherwise a ticked box is a plain
       green square and reads as a colour swatch. */
    QCheckBox::indicator:checked {{
        background: {RUN}; border-color: {RUN};
        image: url("{TICK_URL}");
    }}
    QCheckBox::indicator:indeterminate {{
        background: {BG_INPUT}; border-color: {RUN};
        image: url("{DASH_URL}");
    }}
    QCheckBox:disabled {{ color: {FG4}; }}
    QCheckBox::indicator:disabled {{ border-color: {LINE}; background: {BG}; }}

    QTableWidget {{
        background: {BG_RAISE}; border: 1px solid {LINE}; border-radius: 5px;
        color: {FG}; gridline-color: {LINE};
    }}
    QTableWidget::item {{ padding: 4px 8px; border: none; }}
    QTableWidget::item:selected {{ background: {RUN_DIM}; color: {FG}; }}
    QHeaderView::section {{
        background: {BG_BAND}; color: {FG3}; border: none;
        border-bottom: 1px solid {LINE}; padding: 6px 8px;
        font-size: {T_SECTION}pt; font-weight: 600; letter-spacing: 1px;
    }}
    QTableCornerButton::section {{ background: {BG_BAND}; border: none; }}

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

    {CHIP_RULES}

    /* named surfaces, so a mode change is one setStyleSheet call */
    #shell, #card {{ background: {BG}; border: 1px solid {BORDER}; }}
    #row:hover, #steprow:hover {{ background: {BG_HOVER}; }}
    #navPanel {{ background: {BG_BAND}; border-right: 1px solid {LINE}; }}
    #footerBar {{ background: {BG_BAND}; border-top: 1px solid {LINE}; }}
    #columnHeader {{ background: {BG_BAND}; }}
    #hline {{ background: {LINE}; border: none; }}
    #flyoutTitle {{ color: {FG}; font-size: {T_H2}pt; font-weight: 600; }}
    #sectionBar {{ background: {BG_BAND}; }}
    /* The health schedule: a quiet panel, because it is a statement of fact
       rather than something to act on. */
    #healthStatus {{
        background: {BG_BAND}; border: 1px solid {LINE}; border-radius: 5px;
        color: {FG2};
    }}
    /* The bulk bar carries the same controls as the footer, so its background is
       the only thing saying it acts on a selection rather than on everything. */
    #bulkBar {{ background: {RUN_DIM}; }}
    #bulkBar QLabel {{ color: {RUN_FG}; }}
    """
