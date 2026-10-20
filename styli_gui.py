#!/usr/bin/env python3
"""
styli_gui: the desktop window for Styli (PyQt6).

Keep this file in the same folder as styli.py and open it with `styli gui`
(or run this file directly). Album art comes from the Cover Art Archive and is
cached on disk, so it only downloads once.

Needs PyQt6:  Debian/Ubuntu: sudo apt install python3-pyqt6
              Fedora:        sudo dnf install python3-pyqt6
              Arch:          sudo pacman -S python-pyqt6
              Others:        pip install PyQt6
Qt 6.5 or newer is needed for "Match my desktop" to follow theme changes live.

Licence: MIT. Share freely.
"""

import datetime as dt
import hashlib
import html
import math
import shutil
import re
import sys
import time
from pathlib import Path

from PyQt6.QtCore import (QDate, QDateTime, QEasingCurve, QEvent, QMimeData, QObject, QPoint, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool,
                          QTime, QTimer, QUrl, QVariantAnimation, pyqtSignal)
from PyQt6.QtGui import (QAction, QColor, QDesktopServices, QDrag, QFont, QFontMetrics, QIcon, QImage, QKeySequence, QPainter,
                         QPainterPath, QPalette, QPen, QPixmap, QTextCharFormat)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCalendarWidget, QCheckBox, QSizePolicy, QDateEdit, QDateTimeEdit, QDialog,
                             QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QComboBox, QListWidget, QListWidgetItem, QMainWindow,
                             QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSplitter, QSpinBox, QStackedWidget,
                             QStyle, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTabWidget,
                             QTimeEdit, QToolButton, QToolTip, QVBoxLayout, QWidget)

core = None  # the styli module, handed over by run()
ROLE = Qt.ItemDataRole.UserRole

# ---------------------------------------------------------------------------
# Look and feel
# ---------------------------------------------------------------------------

DARK = {
    "bg": "#111317", "surface": "#191c22", "surface2": "#21252d", "surface3": "#2a2f39",
    "border": "#2f3541", "text": "#eceef2", "muted": "#9299a6", "faint": "#5f6673",
    "accent": "#7c9cff", "accent_hover": "#98b1ff", "accent_dim": "#273560", "on_accent": "#0c1022",
    "ok": "#4cc38a", "warn": "#f0a03c", "over": "#ef5a50",
    "hover": "#353b47", "alt_row": "#1c2026", "scroll_hover": "#3c4250",
    "danger_bg": "#47211f", "danger_text": "#ffd9d6", "cover_edge": "#14ffffff",
    "fill": "#7c9cff", "fill_hover": "#98b1ff", "on_fill": "#0c1022",
}
LIGHT = {
    "bg": "#f3f4f7", "surface": "#ffffff", "surface2": "#f1f3f7", "surface3": "#e5e8ee",
    "border": "#d9dde5", "text": "#16191f", "muted": "#5c6472", "faint": "#9aa1ad",
    "accent": "#3f63e0", "accent_hover": "#5576ea", "accent_dim": "#dfe6fd", "on_accent": "#ffffff",
    "ok": "#1b9a5f", "warn": "#b86e00", "over": "#d23a31",
    "hover": "#d9dde5", "alt_row": "#f8f9fb", "scroll_hover": "#c3c8d2",
    "danger_bg": "#fde4e2", "danger_text": "#9b1c14", "cover_edge": "#1a000000",
    "fill": "#3f63e0", "fill_hover": "#5576ea", "on_fill": "#ffffff",
}
THEMES = {"system": "Match my desktop", "light": "Light", "dark": "Dark"}
# Colour tints: 16 pastels through the rainbow, plus pure black and pure white.
# Each pastel colours buttons, links and highlights, and gives the backgrounds a faint
# wash of the same hue. Shades are adjusted per theme so text stays readable.
TINTS = [  # key, name, hue
    ("red", "Red", 0), ("vermilion", "Vermilion", 14), ("orange", "Orange", 28), ("amber", "Amber", 40),
    ("yellow", "Yellow", 52), ("lime", "Lime", 78), ("green", "Green", 110), ("jade", "Jade", 140),
    ("mint", "Mint", 160), ("teal", "Teal", 180), ("sky", "Sky", 198), ("blue", "Blue", 218),
    ("indigo", "Indigo", 238), ("violet", "Violet", 262), ("purple", "Purple", 285), ("pink", "Pink", 322),
]
TINT_NAMES = dict([("original", "Original")] + [(k, n) for k, n, _ in TINTS] +
                  [("black", "Pure black"), ("white", "Pure white")])
TINT_HUES = {k: h for k, _, h in TINTS}


def hsl(h, s, l, alpha=None):
    c = QColor.fromHslF((h % 360) / 360.0, max(0.0, min(1.0, s / 100.0)), max(0.0, min(1.0, l / 100.0)))
    return c.name() if alpha is None else "#{:02x}{}".format(alpha, c.name()[1:])


def _luminance(hexc):
    hexc = hexc[-6:]
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    """WCAG contrast ratio between two colours."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable_shade(h, s, against, start=46, need=4.8):
    """The lightest shade of a hue whose text is comfortably readable on the given background."""
    l = start
    while l > 15 and contrast(hsl(h, s, l), against) < need:
        l -= 1
    return l


def tint_swatch(key):
    """The colour shown for a tint in Preferences."""
    if key == "black":
        return "#000000"
    if key == "white":
        return "#ffffff"
    if key == "original":
        return DARK["accent"]
    return hsl(TINT_HUES[key], 72, 80)


def tint_palette(key, dark):
    """Colours that replace the standard theme's for a tint (an empty dict for Original)."""
    if key == "black" or key == "white":
        pure = (key == "black") == dark  # black backgrounds in dark mode, white ones in light mode
        if dark:
            base = 0 if pure else 7  # % lightness of the window background
            return {"bg": hsl(0, 0, base), "surface": hsl(0, 0, base + 4), "surface2": hsl(0, 0, base + 8),
                    "surface3": hsl(0, 0, base + 12), "border": hsl(0, 0, base + 17), "hover": hsl(0, 0, base + 16),
                    "alt_row": hsl(0, 0, base + 2.5), "scroll_hover": hsl(0, 0, base + 24), "text": "#f2f2f2",
                    "muted": "#9e9e9e", "faint": "#616161", "accent": "#ffffff", "accent_hover": "#d9d9d9",
                    "accent_dim": hsl(0, 0, base + 20), "on_accent": "#000000"}
        top = 100 if pure else 95.5
        return {"bg": hsl(0, 0, top), "surface": "#ffffff", "surface2": hsl(0, 0, 96 if pure else 95),
                "surface3": hsl(0, 0, 91 if pure else 89.5), "border": hsl(0, 0, 86), "hover": hsl(0, 0, 87),
                "alt_row": hsl(0, 0, 98.5), "scroll_hover": hsl(0, 0, 78), "text": "#111111", "muted": "#5e5e5e",
                "faint": "#9b9b9b", "accent": "#000000", "accent_hover": "#333333", "accent_dim": hsl(0, 0, 88),
                "on_accent": "#ffffff"}
    if key not in TINT_HUES:
        return {}
    h = TINT_HUES[key]
    if dark:
        return {"bg": hsl(h, 16, 7.5), "surface": hsl(h, 14, 10.5), "surface2": hsl(h, 13, 14),
                "surface3": hsl(h, 12, 18), "border": hsl(h, 12, 22), "hover": hsl(h, 12, 24),
                "alt_row": hsl(h, 14, 12), "scroll_hover": hsl(h, 12, 28), "muted": hsl(h, 8, 64),
                "faint": hsl(h, 7, 42), "accent": hsl(h, 78, 78), "accent_hover": hsl(h, 85, 84),
                "accent_dim": hsl(h, 32, 26), "on_accent": hsl(h, 45, 11)}
    link = readable_shade(h, 62, hsl(h, 38, 95.5))  # readable on every light surface, not just white
    return {"bg": hsl(h, 45, 96), "surface": hsl(h, 60, 99.3), "surface2": hsl(h, 38, 95.5),
            "surface3": hsl(h, 30, 90.5), "border": hsl(h, 24, 86), "hover": hsl(h, 26, 87),
            "alt_row": hsl(h, 45, 97.7), "scroll_hover": hsl(h, 20, 77), "muted": hsl(h, 10, 38),
            "faint": hsl(h, 8, 62), "accent": hsl(h, 62, link), "accent_hover": hsl(h, 62, link + 6),
            "accent_dim": hsl(h, 75, 89), "on_accent": "#ffffff",
            "fill": hsl(h, 72, 78), "fill_hover": hsl(h, 76, 72), "on_fill": hsl(h, 50, 14)}

C = dict(DARK)
SYSTEM_PALETTE = None  # the desktop's own palette, captured before the theme is applied

QSS = """
* {{ color: {text}; }}
QMainWindow, QDialog, QWidget#page {{ background: {bg}; }}
QFrame#card {{ background: {surface}; border: 1px solid {border}; border-radius: 16px; }}
QLabel {{ background: transparent; }}
QLabel#h1 {{ font-size: 20pt; font-weight: 700; }}
QLabel#h2 {{ font-size: 14pt; font-weight: 700; }}
QLabel#lead {{ font-size: 11pt; }}
QLabel#muted {{ color: {muted}; }}
QLabel#caption {{ color: {muted}; font-size: 8pt; font-weight: 700; }}
QLabel#statValue {{ font-size: 15pt; font-weight: 700; }}
QLabel#ok {{ color: {ok}; }}
QLabel#warn {{ color: {warn}; }}
QLabel#over {{ color: {over}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QDateTimeEdit, QDateEdit {{
    background: {surface2}; border: 1px solid {border}; border-radius: 9px; padding: 7px 10px;
    selection-background-color: {accent}; selection-color: {on_accent}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateTimeEdit:focus, QDateEdit:focus {{
    border: 1px solid {accent}; }}
QLineEdit:disabled, QDateTimeEdit:disabled {{ color: {faint}; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 18px; border: none; background: transparent; }}
QPushButton {{ background: {surface3}; border: 1px solid {border}; border-radius: 9px; padding: 8px 16px; }}
QPushButton:hover {{ background: {hover}; }}
QPushButton:disabled {{ color: {faint}; background: {surface2}; }}
QPushButton#primary {{ background: {fill}; color: {on_fill}; border: none; font-weight: 700; }}
QPushButton#primary:hover {{ background: {fill_hover}; }}
QPushButton#primary:disabled {{ background: {accent_dim}; color: {muted}; }}
QPushButton#danger:hover {{ background: {danger_bg}; border-color: {over}; color: {danger_text}; }}
QPushButton#ghost {{ background: transparent; border: none; color: {accent}; padding: 6px 8px; }}
QPushButton#ghost:hover {{ color: {accent_hover}; }}
QPushButton#chip {{ border-radius: 12px; padding: 8px 12px; background: {surface2}; color: {muted};
    border: 1px solid {border}; }}
QPushButton#chip:hover {{ border-color: {faint}; color: {text}; }}
QPushButton#chip:checked {{ background: {accent_dim}; border: 1px solid {accent}; color: {text}; font-weight: 700; }}
QProgressBar {{ background: {surface3}; border: none; border-radius: 6px; }}
QProgressBar::chunk {{ border-radius: 6px; background: {ok}; }}
QProgressBar[level="warn"]::chunk {{ background: {warn}; }}
QProgressBar[level="over"]::chunk {{ background: {over}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {muted}; padding: 8px 2px; margin-right: 22px;
    border-bottom: 2px solid transparent; font-weight: 700; font-size: 11pt; }}
QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid {accent}; }}
QTabBar::tab:hover {{ color: {text}; }}
QListWidget, QTableWidget {{ background: {surface}; border: 1px solid {border}; border-radius: 14px;
    outline: 0; padding: 4px; }}
QTableWidget {{ gridline-color: transparent; alternate-background-color: {alt_row}; }}
QTableWidget::item {{ padding: 6px; border: none; }}
QTableWidget::item:selected {{ background: {accent_dim}; color: {text}; }}
QHeaderView::section {{ background: {surface}; color: {muted}; border: none; border-bottom: 1px solid {border};
    padding: 8px 6px; font-weight: 700; }}
QHeaderView::down-arrow {{ image: url("{sort_down}"); width: 11px; height: 8px; subcontrol-position: center right;
    padding-right: 8px; }}
QHeaderView::up-arrow {{ image: url("{sort_up}"); width: 11px; height: 8px; subcontrol-position: center right;
    padding-right: 8px; }}
QTableCornerButton::section {{ background: {surface}; border: none; }}
QScrollArea {{ background: transparent; border: none; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {accent_dim}; border-radius: 3px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 6px 2px; }}
QScrollBar::handle:vertical {{ background: {surface3}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {scroll_hover}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px 6px; }}
QScrollBar::handle:horizontal {{ background: {surface3}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QCheckBox, QRadioButton {{ spacing: 9px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {border};
    background: {surface2}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}
QRadioButton {{ font-size: 11pt; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px; border: 1px solid {faint};
    background: {surface2}; }}
QRadioButton::indicator:checked {{ background: {accent}; border: 4px solid {accent_dim}; }}
QToolTip {{ background: {surface3}; color: {text}; border: 1px solid {border}; padding: 6px; }}
QFrame#card[current="true"] {{ border: 2px solid {accent}; }}
QPushButton#seg {{ background: transparent; border: 1px solid {border}; border-radius: 0; padding: 7px 18px;
    color: {muted}; font-weight: 700; }}
QPushButton#seg[pos="first"] {{ border-top-left-radius: 9px; border-bottom-left-radius: 9px; }}
QPushButton#seg[pos="last"] {{ border-top-right-radius: 9px; border-bottom-right-radius: 9px; border-left: none; }}
QPushButton#seg:checked {{ background: {accent_dim}; color: {text}; }}
QLabel#accentCaption {{ color: {accent}; font-size: 8pt; font-weight: 700; }}
QLabel#cardTitle {{ font-size: 12pt; font-weight: 700; }}
QLabel#trackSide {{ font-weight: 700; padding-top: 4px; }}
QComboBox {{ background: {surface2}; border: 1px solid {border}; border-radius: 9px; padding: 7px 10px; }}
QComboBox:focus {{ border: 1px solid {accent}; }}
QComboBox::drop-down, QDateEdit::drop-down, QDateTimeEdit::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow, QDateEdit::down-arrow, QDateTimeEdit::down-arrow {{ image: url("{arrow}");
    width: 12px; height: 8px; }}
QComboBox QAbstractItemView {{ background: {surface}; border: 1px solid {border}; outline: 0;
    selection-background-color: {accent_dim}; selection-color: {text}; padding: 4px; }}
QMessageBox {{ background: {surface}; }}
QTimeEdit {{ background: {surface2}; border: 1px solid {border}; border-radius: 9px; padding: 7px 10px; }}
QTimeEdit:focus {{ border: 1px solid {accent}; }}
QTimeEdit::up-button, QTimeEdit::down-button {{ width: 18px; border: none; background: transparent; }}
QPushButton#whenBtn {{ text-align: left; background: {surface2}; padding: 8px 12px; }}
QPushButton#whenBtn:hover {{ border-color: {faint}; }}
QPushButton#whenBtn[past="true"] {{ border: 1px solid {accent}; font-weight: 700; }}
QFrame#popupCard {{ background: {surface}; border: 1px solid {border}; border-radius: 14px; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: transparent; }}
QCalendarWidget QToolButton {{ background: transparent; border: none; border-radius: 7px; padding: 4px 8px;
    color: {text}; font-weight: 700; font-size: 11pt; }}
QCalendarWidget QToolButton:hover {{ background: {hover}; }}
QCalendarWidget QToolButton::menu-indicator {{ image: none; width: 0; }}
QCalendarWidget QMenu {{ background: {surface}; border: 1px solid {border}; }}
QCalendarWidget QMenu::item:selected {{ background: {accent_dim}; }}
QCalendarWidget QSpinBox {{ padding: 2px 6px; }}
QCalendarWidget QAbstractItemView {{ background: {surface}; outline: 0; border: none;
    selection-background-color: {accent}; selection-color: {on_accent}; }}
QCalendarWidget QAbstractItemView:disabled {{ color: {faint}; }}
QLabel#statSmall {{ font-size: 12pt; font-weight: 700; }}
QFrame#dropMarker {{ background: {accent}; border-radius: 2px; }}
QFrame#card[dragging="true"] {{ border: 2px dashed {accent}; background: {surface2}; }}
"""


def system_is_dark(app):
    """True when the desktop is set to a dark theme (Qt 6.5+ asks the desktop directly)."""
    try:
        scheme = app.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except AttributeError:  # Qt older than 6.5
        pass
    pal = SYSTEM_PALETTE or app.palette()
    return pal.color(QPalette.ColorRole.Window).lightness() < 128


def arrow_image(colour, up=False):
    """A small arrow drawn for the current theme (stylesheets need an image file)."""
    path = core.cache_dir().parent / "arrow-{}{}.png".format(colour.strip("#"), "-up" if up else "")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        img = QImage(24, 16, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(colour), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline([QPointF(4, 12), QPointF(12, 4), QPointF(20, 12)] if up else
                       [QPointF(4, 4), QPointF(12, 12), QPointF(20, 4)])
        p.end()
        img.save(str(path))
    return path.as_posix()


def apply_theme(app, pref="system", tint="original"):
    dark = pref == "dark" or (pref == "system" and system_is_dark(app))
    C.clear()
    C.update(DARK if dark else LIGHT)
    extra = tint_palette(tint, dark)
    C.update(extra)
    if "fill" not in extra and extra:  # tints that don't set a separate button colour use the accent
        C.update(fill=C["accent"], fill_hover=C["accent_hover"], on_fill=C["on_accent"])
    app.setStyle("Fusion")
    pal = QPalette()
    for role, colour in (
            (QPalette.ColorRole.Window, C["bg"]), (QPalette.ColorRole.WindowText, C["text"]),
            (QPalette.ColorRole.Base, C["surface2"]), (QPalette.ColorRole.AlternateBase, C["surface"]),
            (QPalette.ColorRole.Text, C["text"]), (QPalette.ColorRole.Button, C["surface3"]),
            (QPalette.ColorRole.ButtonText, C["text"]), (QPalette.ColorRole.Highlight, C["accent"]),
            (QPalette.ColorRole.HighlightedText, C["on_accent"]), (QPalette.ColorRole.ToolTipBase, C["surface3"]),
            (QPalette.ColorRole.ToolTipText, C["text"]), (QPalette.ColorRole.PlaceholderText, C["faint"]),
            (QPalette.ColorRole.Mid, C["border"]), (QPalette.ColorRole.Light, C["surface"]),
            (QPalette.ColorRole.Dark, C["surface3"])):
        pal.setColor(role, QColor(colour))
    app.setPalette(pal)
    app.setStyleSheet(QSS.format(arrow=arrow_image(C["muted"]), sort_down=arrow_image(C["accent"]),
                                 sort_up=arrow_image(C["accent"], up=True), **C))
    for w in app.allWidgets():
        w.update()
    return dark


def label(text="", name=None, wrap=False):
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def button(text, slot=None, name=None):
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def card():
    f = QFrame()
    f.setObjectName("card")
    return f


def restyle(widget, name=None, **props):
    """Change a widget's style name or property and make the stylesheet notice."""
    if name is not None:
        widget.setObjectName(name)
    for k, v in props.items():
        widget.setProperty(k, v)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def table(headers, stretch_col):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.setWordWrap(False)
    t.verticalHeader().setDefaultSectionSize(36)
    hh = t.horizontalHeader()
    hh.setHighlightSections(False)
    hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    for i in range(len(headers)):
        hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch if i == stretch_col
                                else QHeaderView.ResizeMode.ResizeToContents)
    return t


def frow(form, text, widget):
    """A form row whose label lines up with the middle of its field."""
    lab = QLabel(text)
    lab.setMinimumHeight(widget.sizeHint().height())
    lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.addRow(lab, widget)


def nice_date(text):
    """'1977-02-04' -> '4 Feb 1977', '1977-02' -> 'Feb 1977'; anything else as it is."""
    text = (text or "").strip()
    try:
        if len(text) == 10:
            d = dt.date.fromisoformat(text)
            return "{} {}".format(d.day, d.strftime("%b %Y"))
        if len(text) == 7:
            return dt.date.fromisoformat(text + "-01").strftime("%b %Y")
    except ValueError:
        pass
    return text


ZWSP = "\u200b"


def breakable(text, chunk=14):
    """Let very long words (web addresses, catalogue numbers) wrap instead of pushing the panel wider."""
    def split(m):
        w = m.group(0)
        return ZWSP.join(w[i:i + chunk] for i in range(0, len(w), chunk))
    return re.sub(r"\S{%d,}" % (chunk * 2), split, text or "")


def make_splitter(main):
    split = QSplitter(Qt.Orientation.Horizontal)
    split.setChildrenCollapsible(False)
    split.setHandleWidth(18)
    split.splitterMoved.connect(lambda pos, idx: main._panel_moved(split))
    return split


def fill_track_grid(grid, tracks, lengths):
    """Track listing grouped by side: a heading with the side's length, then position, title and length."""
    clear_layout(grid)
    row, side = 0, None
    for t in tracks or []:
        if t["side"] != side:
            side = t["side"]
            grid.addWidget(label("Side {}".format(side), "trackSide"), row, 0, 1, 2)
            if lengths.get(side):
                tl = label(core.fmt_dur(lengths[side]), "muted")
                tl.setAlignment(Qt.AlignmentFlag.AlignRight)
                grid.addWidget(tl, row, 2)
            row += 1
        pos = label(t["position"] or "", "muted")
        pos.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        title = label(breakable(t["title"] or ""), wrap=True)
        dur = label(core.fmt_dur(t["seconds"]) if t["seconds"] else "", "muted")
        dur.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        grid.addWidget(pos, row, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(title, row, 1)
        grid.addWidget(dur, row, 2, Qt.AlignmentFlag.AlignTop)
        row += 1


def make_track_grid():
    box = QWidget()
    grid = QGridLayout(box)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(5)
    grid.setColumnStretch(1, 1)
    return box, grid


def clear_layout(lay):
    """Remove every widget from a layout straight away (not just at the next idle moment)."""
    while lay.count():
        w = lay.takeAt(0).widget()
        if w:
            w.hide()
            w.setParent(None)
            w.deleteLater()


SORT_ROLE = Qt.ItemDataRole.UserRole + 1


class SortItem(QTableWidgetItem):
    """A table cell that sorts by a hidden key (dates by date, lengths by seconds, names ignoring 'The')."""

    def __lt__(self, other):
        a, b = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if a is None or b is None:
            return super().__lt__(other)
        return a < b


def cell(text, data=None, key=None):
    it = SortItem(str(text))
    if data is not None:
        it.setData(ROLE, data)
    if key is not None:
        it.setData(SORT_ROLE, key)
    return it


def sort_name(text):
    """Names sort the way record shops file them: case doesn't matter and a leading 'The' is ignored."""
    t = " ".join((text or "").lower().split())
    return t[4:] if t.startswith("the ") else t


LIB_SORTS = [("artist", "Artist (A to Z)"), ("artist_desc", "Artist (Z to A)"), ("album", "Album (A to Z)"),
             ("album_desc", "Album (Z to A)"), ("year", "Year (oldest first)"), ("year_desc", "Year (newest first)"),
             ("added", "Recently added"), ("played", "Recently played"), ("most", "Most played")]


def sort_records(items, how):
    """items: dicts with artist, title, year, added, last, album, sides. Returns them sorted."""
    by_name = lambda i: (sort_name(i["artist"]), sort_name(i["title"]), i["year"] or "")  # noqa: E731
    by_album = lambda i: (sort_name(i["title"]), sort_name(i["artist"]))  # noqa: E731
    year = lambda i: i["year"] or ""  # noqa: E731
    if how == "artist_desc":
        return sorted(items, key=by_name, reverse=True)
    if how == "album":
        return sorted(items, key=by_album)
    if how == "album_desc":
        return sorted(items, key=by_album, reverse=True)
    if how in ("year", "year_desc"):
        dated = sorted([i for i in items if year(i)], key=lambda i: (year(i), by_name(i)), reverse=how == "year_desc")
        return dated + sorted([i for i in items if not year(i)], key=by_name)  # undated last either way
    if how == "added":
        return sorted(items, key=lambda i: i["added"] or "", reverse=True)
    if how == "played":
        played = sorted([i for i in items if i["last"]], key=lambda i: i["last"], reverse=True)
        return played + sorted([i for i in items if not i["last"]], key=by_name)
    if how == "most":
        return sorted(items, key=lambda i: (-i["album"], -i["sides"], by_name(i)))
    return sorted(items, key=by_name)


def sort_combo(options, current, changed):
    box = QComboBox()
    for key, text in options:
        box.addItem(text, key)
    box.setCurrentIndex(max(0, box.findData(current)))
    box.setToolTip("Sort the list")
    box.currentIndexChanged.connect(lambda i: changed(box.itemData(i)))
    return box


# ---------------------------------------------------------------------------
# Drawing: covers and the placeholder record
# ---------------------------------------------------------------------------

def draw_vinyl(p, rect, seed):
    """A stylised record, used when there is no album art."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c, r = rect.center(), rect.width() / 2
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#0a0b0e"))
    p.drawEllipse(c, r, r)
    pen = QPen(QColor(255, 255, 255, 22))
    pen.setWidthF(max(0.8, r / 110))
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    for f in (0.93, 0.85, 0.77, 0.69, 0.61, 0.53, 0.45):
        p.drawEllipse(c, r * f, r * f)
    hue = int(hashlib.md5((seed or "?").encode("utf-8")).hexdigest()[:4], 16) % 360
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor.fromHsl(hue, 140, 125))
    p.drawEllipse(c, r * 0.33, r * 0.33)
    p.setBrush(QColor("#0a0b0e"))
    p.drawEllipse(c, r * 0.045, r * 0.045)
    p.restore()


def draw_cartridge(p, rect, seed):
    """A stylised phono cartridge, used when a cartridge / stylus has no photo."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    w, h = rect.width(), rect.height()
    hue = int(hashlib.md5((seed or "?").encode("utf-8")).hexdigest()[:4], 16) % 360
    body = QRectF(rect.x() + w * 0.2, rect.y() + h * 0.3, w * 0.6, h * 0.34)
    plate = QRectF(rect.x() + w * 0.14, rect.y() + h * 0.22, w * 0.72, h * 0.1)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#3a3f48"))
    p.drawRoundedRect(plate, h * 0.02, h * 0.02)
    p.setBrush(QColor.fromHsl(hue, 150, 110))
    p.drawRoundedRect(body, h * 0.05, h * 0.05)
    p.setBrush(QColor(255, 255, 255, 45))
    p.drawRoundedRect(QRectF(body.x(), body.y(), body.width(), body.height() * 0.28), h * 0.05, h * 0.05)
    p.setBrush(QColor("#1d2026"))
    for fx in (0.32, 0.68):
        p.drawEllipse(QPointF(rect.x() + w * fx, plate.center().y()), w * 0.03, w * 0.03)
    pen = QPen(QColor("#c9ccd2"))
    pen.setWidthF(max(1.2, w * 0.018))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    start = QPointF(body.center().x() + w * 0.05, body.bottom() - h * 0.02)
    tip = QPointF(body.center().x() - w * 0.12, body.bottom() + h * 0.17)
    p.drawLine(start, tip)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#f2f3f5"))
    p.drawEllipse(tip, w * 0.022, w * 0.022)
    p.restore()


def draw_cover(p, rect, pm, seed, radius, placeholder="vinyl", fit=False):
    """A cover or photo in a rounded square. fit=True shows the whole photo on a white card."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    if pm is not None and not pm.isNull():
        p.setClipPath(path)
        if fit:
            p.fillPath(path, QColor("#ffffff"))
            inner = rect.adjusted(rect.width() * 0.04, rect.height() * 0.04, -rect.width() * 0.04,
                                  -rect.height() * 0.04)
            scaled = pm.scaled(inner.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        else:
            scaled = pm.scaled(rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                               Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(QPointF(rect.x() + (rect.width() - scaled.width()) / 2,
                             rect.y() + (rect.height() - scaled.height()) / 2), scaled)
        p.setClipping(False)
        p.setPen(QPen(QColor(C["cover_edge"]), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
    else:
        p.fillPath(path, QColor(C["surface3"]))
        if placeholder == "cartridge":
            draw_cartridge(p, rect, seed)
        else:
            inset = rect.width() * 0.12
            draw_vinyl(p, rect.adjusted(inset, inset, -inset, -inset), seed)
    p.restore()


# ---------------------------------------------------------------------------
# Background work (MusicBrainz and cover downloads never freeze the window)
# ---------------------------------------------------------------------------

class _Signals(QObject):
    done = pyqtSignal(object)
    failed = pyqtSignal(object)


class _Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()
        self.started = False
        self.setAutoDelete(False)

    def run(self):
        self.started = True
        try:
            result, ok = self.fn(), True
        except Exception as e:  # handed back to the window
            result, ok = e, False
        try:
            (self.signals.done if ok else self.signals.failed).emit(result)
        except RuntimeError:
            pass  # the app closed while this was still downloading


def run_task(fn, done, failed, keep, pool=None, priority=0):
    task = _Task(fn)
    keep.add(task)

    def finish(cb):
        def inner(value):
            keep.discard(task)
            cb(value)
        return inner

    task.signals.done.connect(finish(done))
    task.signals.failed.connect(finish(failed))
    (pool or QThreadPool.globalInstance()).start(task, priority)
    return task


def resolve_cover(key):
    """Runs in a worker thread. Returns (path or None, album id found or None)."""
    kind = key[0]
    if kind == "record":
        _, rid, mbid, rgid, size, cover_url, artist, title = key
        return core.ensure_record_cover(rid, mbid, rgid, size, cover_url, artist, title)  # saved with the library
    if kind == "url":
        return core.fetch_image_cached(key[1]), None
    if kind == "pressing":
        _, mbid, rgid, size = key
        return (core.fetch_cover("release", mbid, size)
                or (core.fetch_cover("release-group", rgid, size) if rgid else None)), None
    _, mbid, size = key
    return core.fetch_cover(kind, mbid, size), None


class CoverStore(QObject):
    """Loads covers in the background and hands out pixmaps once they arrive."""
    changed = pyqtSignal()
    rgid_found = pyqtSignal(int, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.pix, self.pending, self.missing, self.tasks, self.queued = {}, set(), set(), set(), {}

    def get(self, key, priority=0):
        """The cover if it's loaded, otherwise None (and it's queued). Higher priority loads first."""
        if not key:
            return None
        pm = self.pix.get(key)
        if pm is None and key not in self.pending and key not in self.missing:
            self.pending.add(key)
            self.queued[key] = run_task(lambda k=key: resolve_cover(k), lambda res, k=key: self._done(k, res),
                                        lambda e, k=key: self._done(k, (None, None)), self.tasks, self.pool,
                                        priority)
        return pm

    def cancel_queued(self):
        """Drop downloads that haven't started yet, so other work isn't kept waiting.

        Anything still on screen asks again when it next repaints.
        """
        self.pool.clear()
        for key, task in list(self.queued.items()):
            if not task.started:
                self.pending.discard(key)
                self.tasks.discard(task)
                del self.queued[key]
        self.changed.emit()

    def forget_previews(self):
        for store in (self.pix,):
            for k in [k for k in store if k[0] != "record"]:
                del store[k]
        self.missing = {k for k in self.missing if k[0] == "record"}

    def loading(self, key):
        return key in self.pending

    def _done(self, key, result):
        path, rgid = result
        self.pending.discard(key)
        self.queued.pop(key, None)
        pm = QPixmap(str(path)) if path else QPixmap()
        if pm.isNull():
            self.missing.add(key)
        else:
            self.pix[key] = pm
        if rgid and key[0] == "record":
            self.rgid_found.emit(key[1], rgid)
        self.changed.emit()

    def forget_record(self, rid):
        """Drop a record's cached cover so a newly chosen image is loaded."""
        for k in [k for k in self.pix if k[0] == "record" and k[1] == rid]:
            del self.pix[k]
        self.missing = {k for k in self.missing if not (k[0] == "record" and k[1] == rid)}


class CoverWidget(QWidget):
    def __init__(self, store, size, placeholder="vinyl", fit=False):
        super().__init__()
        self.store, self.key, self.seed, self.placeholder, self.fit = store, None, "", placeholder, fit
        self.setFixedSize(size, size)
        store.changed.connect(self.update)

    def show_cover(self, key, seed=""):
        self.key, self.seed = key, seed
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        pm = self.store.get(self.key, priority=10)  # the big cover jumps the queue
        draw_cover(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), pm, self.seed, 14 if self.width() > 120 else 10,
                   self.placeholder, self.fit)
        if pm is None and self.key and self.store.loading(self.key):
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(self.rect()).adjusted(0, 0, 0, -10),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, "Loading cover...")
        p.end()


class RowDelegate(QStyledItemDelegate):
    """Paints a list row: cover, title, subtitle and two lines of detail on the right."""

    def __init__(self, store, parent, height=72):
        super().__init__(parent)
        self.store, self.height = store, height

    def sizeHint(self, option, index):
        return QSize(240, self.height)

    def paint(self, p, option, index):
        d = index.data(ROLE) or {}
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(option.rect).adjusted(4, 2, -4, -2)
        state = option.state
        bg = (C["accent_dim"] if state & QStyle.StateFlag.State_Selected
              else C["surface2"] if state & QStyle.StateFlag.State_MouseOver else None)
        if bg:
            path = QPainterPath()
            path.addRoundedRect(r, 10, 10)
            p.fillPath(path, QColor(bg))
        side = r.height() - 14
        art = QRectF(r.left() + 8, r.top() + 7, side, side)
        draw_cover(p, art, self.store.get(d.get("cover")), d.get("title", ""), 7, d.get("placeholder", "vinyl"),
                   d.get("fit", False))
        right_w = 200 if d.get("meta") else 0
        x = art.right() + 14
        w = max(40.0, r.right() - x - right_w - 12)
        half = (r.height() - 18) / 2
        f1 = QFont(option.font)
        f1.setBold(True)
        if f1.pointSizeF() > 0:
            f1.setPointSizeF(f1.pointSizeF() + 1)
        p.setFont(f1)
        p.setPen(QColor(C["text"]))
        p.drawText(QRectF(x, r.top() + 9, w, half), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(f1).elidedText(d.get("title", ""), Qt.TextElideMode.ElideRight, int(w)))
        f2 = QFont(option.font)
        p.setFont(f2)
        p.setPen(QColor(C["muted"]))
        fm2 = QFontMetrics(f2)
        p.drawText(QRectF(x, r.top() + 9 + half, w, half), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   fm2.elidedText(d.get("subtitle", ""), Qt.TextElideMode.ElideRight, int(w)))
        if right_w:
            rx = r.right() - right_w - 10
            align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            p.setPen(QColor(C["text"]))
            p.drawText(QRectF(rx, r.top() + 9, right_w, half), align, d.get("meta", ""))
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(rx, r.top() + 9 + half, right_w, half), align,
                       fm2.elidedText(d.get("meta2", ""), Qt.TextElideMode.ElideRight, right_w))
        p.restore()


def make_list(store, height=72):
    lst = QListWidget()
    lst.setItemDelegate(RowDelegate(store, lst, height))
    lst.setMouseTracking(True)
    lst.setUniformItemSizes(True)
    lst.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    store.changed.connect(lst.viewport().update)
    return lst


class PhotoStore(QObject):
    """Cartridge / stylus photos (local files), with the same interface as CoverStore."""
    changed = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        self.cache = {}

    def key(self, sid):
        path = core.stylus_photo_path(sid)
        return ("stylus", sid, path.stat().st_mtime if path.exists() else 0)

    def get(self, key, priority=0):
        if not key or not key[2]:
            return None
        pm = self.cache.get(key)
        if pm is None:
            pm = self.cache[key] = QPixmap(str(core.stylus_photo_path(key[1])))
        return None if pm.isNull() else pm

    def loading(self, key):
        return False

    def updated(self):
        self.changed.emit()


class PixmapWidget(QWidget):
    """Shows a QPixmap you hand it (used for lookup previews)."""

    def __init__(self, size, placeholder="cartridge"):
        super().__init__()
        self.pm, self.placeholder, self.note = None, placeholder, ""
        self.setFixedSize(size, size)

    def set_pixmap(self, pm, note=""):
        self.pm, self.note = pm, note
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        draw_cover(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), self.pm, "", 14, self.placeholder, fit=True)
        if self.note:
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(self.rect()).adjusted(0, 0, 0, -10),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, self.note)
        p.end()


class MonthBars(QWidget):
    """A small bar chart of hours played per month."""

    def __init__(self):
        super().__init__()
        self.data = []
        self.setMinimumHeight(130)

    def set_data(self, rows):
        self.data = rows[-12:]
        self.setVisible(bool(rows))
        self.update()

    def paintEvent(self, event):
        if not self.data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 18, -2, -22)
        top = max(s for _, s in self.data) or 1
        n = len(self.data)
        slot = r.width() / max(n, 6)
        bw = min(26.0, slot * 0.62)
        f = QFont(self.font())
        if f.pointSizeF() > 0:
            f.setPointSizeF(f.pointSizeF() - 1)
        p.setFont(f)
        for i, (month, secs) in enumerate(self.data):
            x = r.x() + slot * i + (slot - bw) / 2
            h = max(2.0, r.height() * secs / top)
            bar = QRectF(x, r.bottom() - h, bw, h)
            path = QPainterPath()
            path.addRoundedRect(bar, 4, 4)
            p.fillPath(path, QColor(C["accent"]))
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(x - 14, r.bottom() + 4, bw + 28, 16), Qt.AlignmentFlag.AlignHCenter,
                       dt.date(int(month[:4]), int(month[5:7]), 1).strftime("%b"))
            p.setPen(QColor(C["text"]))
            p.drawText(QRectF(x - 18, bar.y() - 17, bw + 36, 15), Qt.AlignmentFlag.AlignHCenter,
                       "{:.1f}".format(secs / 3600))
        p.end()


class StylusCard(QFrame):
    """One cartridge / stylus in use: photo, wear bar and hours. Click to put it on the turntable."""

    def __init__(self, main, s, current):
        super().__init__()
        self.main, self.sid = main, s["id"]
        self.setObjectName("card")
        self.setProperty("current", "true" if current else "false")
        self.setFixedSize(370, 150)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        m = core.stylus_summary(main.con, s)
        g = QGridLayout(self)
        g.setContentsMargins(16, 14, 16, 14)
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(4)
        photo = CoverWidget(main.photos, 92, placeholder="cartridge", fit=True)
        photo.show_cover(main.photos.key(s["id"]), s["name"])
        g.addWidget(photo, 0, 0, 5, 1, Qt.AlignmentFlag.AlignTop)
        self.caption = label("", "caption")
        g.addWidget(self.caption, 0, 1)
        self.set_current(current)
        name = label(s["name"], "cardTitle")
        name.setToolTip(s["name"])
        g.addWidget(name, 1, 1)
        bar = QProgressBar()
        bar.setTextVisible(False)
        bar.setRange(0, 1000)
        bar.setFixedHeight(9)
        bar.setValue(int(min(100, m["pct"]) * 10))
        restyle(bar, level=m["level"])
        g.addWidget(bar, 2, 1)
        g.addWidget(label("{:.1f} of {:g} h  ·  {:.0f}% worn".format(m["used"] / 3600, s["rated_hours"],
                                                                          m["pct"]), "muted"), 3, 1)
        if m["level"] == "over":
            text, style = "Rated life reached: inspect or replace", "over"
        elif m["level"] == "warn":
            text, style = "{:.1f} h left  ·  past {:g}% warning".format(m["remaining"] / 3600, s["warn_pct"]), "warn"
        else:
            text = "{:.1f} h left".format(m["remaining"] / 3600)
            if m["eta"]:
                text += "  ·  until about {}".format(m["eta"].strftime("%b %Y"))
            style = "muted"
        g.addWidget(label(text, style), 4, 1)
        g.setColumnStretch(1, 1)

    def set_current(self, current):
        """Highlight this card (or not) without rebuilding it, so nothing moves."""
        self.caption.setText("ON THE TURNTABLE" if current else "IN USE  \u00b7  CLICK TO SWITCH")
        restyle(self.caption, "accentCaption" if current else "caption")
        restyle(self, current="true" if current else "false")

    def mousePressEvent(self, event):
        self.main.make_current(self.sid)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, con):
        super().__init__()
        self.con, self.current, self.chips = con, None, {}
        self.view, self.lib_sel, self.pending, self.pending_item, self.tasks = "library", None, None, None, set()
        self.detail_loading, self.detail_failed, self.pending_wiki, self.wiki_loading = set(), set(), {}, set()
        self.dg_items, self.dg_shown, self.dg_cache, self.dg_loading = [], [], {}, set()
        self.dg_sel, self.dg_user, self.dg_gen, self.dg_new = None, None, 0, []
        self.theme_pref = core.get_setting(con, "theme", "system")
        self.tint = core.get_setting(con, "tint", "original")
        self.covers = CoverStore(self)
        self.covers.rgid_found.connect(self._store_rgid)
        self.photos = PhotoStore(self)
        self.setWindowTitle("Styli")
        self.resize(1260, 900)
        self.setMinimumSize(960, 620)
        self.panel_save_timer = QTimer(self)
        self.panel_save_timer.setSingleShot(True)
        self.panel_save_timer.setInterval(600)
        self.panel_save_timer.timeout.connect(self._save_panel_width)
        page = QWidget()
        page.setObjectName("page")
        self.setCentralWidget(page)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(16)
        outer.addWidget(self._build_styli_area())
        self.detail = self._build_detail()  # shared by the Library and Discogs tabs
        self.tabs = QTabWidget()
        self.lib_page = self._build_library()
        self.dg_page = self._build_discogs()
        self.hist_page = self._build_history()
        self.log_page = self._build_playlog()
        self.stats_page = self._build_stats()
        self.top_page = self._build_top()
        self.tabs.addTab(self.lib_page, "Library")
        self.tabs.addTab(self.log_page, "Play log")
        self.tabs.addTab(self.hist_page, "History")
        self.tabs.addTab(self.stats_page, "Stats")
        self.tabs.addTab(self.top_page, "Top 10")
        self.update_discogs_tab()
        self.tabs.currentChanged.connect(self.on_tab)
        prefs = button("Preferences", self.open_prefs, "ghost")
        prefs.setToolTip("Theme and album cover settings (Ctrl+,)")
        self.tabs.setCornerWidget(prefs, Qt.Corner.TopRightCorner)
        act = QAction(self)
        act.setShortcuts([QKeySequence("Ctrl+,"), QKeySequence(QKeySequence.StandardKey.Preferences)])
        act.triggered.connect(self.open_prefs)
        self.addAction(act)
        outer.addWidget(self.tabs, 1)
        self.refresh_all()

    # ---- building ---------------------------------------------------------
    def _build_styli_area(self):
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(label("CARTRIDGES / STYLI IN USE", "caption"))
        self.strip_summary = label("", "muted")  # one line instead of the cards, when they're hidden
        head.addWidget(self.strip_summary)
        head.addStretch(1)
        self.strip_toggle = button("", self.toggle_strip, "ghost")
        head.addWidget(self.strip_toggle)
        head.addWidget(button("Manage cartridges / styli", self.open_styli, "ghost"))
        v.addLayout(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(166)
        self.strip_scroll = scroll
        self.strip = QWidget()
        self.strip.setObjectName("detailInner")
        self.strip.setStyleSheet("QWidget#detailInner { background: transparent; }")
        self.strip_lay = QHBoxLayout(self.strip)
        self.strip_lay.setContentsMargins(0, 0, 0, 0)
        self.strip_lay.setSpacing(14)
        scroll.setWidget(self.strip)
        v.addWidget(scroll)
        self._apply_strip(core.get_setting(self.con, "strip_hidden", "0") == "1")
        return wrap

    def toggle_strip(self):
        hidden = self.strip_scroll.isVisibleTo(self.strip_scroll.parentWidget())
        core.set_setting(self.con, "strip_hidden", "1" if hidden else "0")
        self._apply_strip(hidden)

    def _apply_strip(self, hidden):
        """Hide the cartridge / stylus cards to give the tabs more room (handy on smaller screens)."""
        self.strip_scroll.setVisible(not hidden)
        self.strip_summary.setVisible(hidden)
        self.strip_toggle.setText("Show cards" if hidden else "Hide cards")
        self.strip_toggle.setToolTip("Show the cartridge / stylus cards again" if hidden else
                                     "Hide the cartridge / stylus cards to make more room below")

    # ---- resizable list / panel ---------------------------------------------------
    def _split_stretch(self, split):
        split.setStretchFactor(0, 3)  # extra width is shared: 3 parts to the list, 2 to the panel
        if split.count() > 1:
            split.setStretchFactor(1, 2)

    def _panel_fraction(self):
        try:
            return min(0.6, max(0.2, float(core.get_setting(self.con, "panel_fraction", "0.34"))))
        except ValueError:
            return 0.34

    def _apply_panel_width(self, split):
        total = split.width() - split.handleWidth()
        if total > 0 and split.count() > 1:
            right = int(total * self._panel_fraction())
            split.setSizes([total - right, right])

    def _place_detail(self, split):
        """Move the shared panel to this tab's divider, at the width you last chose."""
        if split.indexOf(self.detail) < 0:
            split.addWidget(self.detail)
            self._split_stretch(split)
        self._apply_panel_width(split)

    def _panel_moved(self, split):
        sizes = split.sizes()
        if len(sizes) > 1 and sum(sizes) > 0:
            self._pending_fraction = sizes[1] / float(sum(sizes))
            self.panel_save_timer.start()

    def _save_panel_width(self):
        if getattr(self, "_pending_fraction", None):
            core.set_setting(self.con, "panel_fraction", "{:.3f}".format(self._pending_fraction))

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_sized", False):
            self._sized = True
            QTimer.singleShot(0, lambda: self._apply_panel_width(self.lib_split))

    def eventFilter(self, obj, event):
        if obj is self.detail_scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_cover(event.size().width())
        elif getattr(self, "st_scroll", None) is not None and obj is self.st_scroll.viewport() \
                and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._fit_stats)  # after the resize, never during it
        return super().eventFilter(obj, event)

    def _fit_cover(self, width):
        """Cover art scales with the panel: between 170 and 340 pixels."""
        side = max(170, min(340, int((width - 44) * 0.62)))
        if self.cover.width() != side:
            self.cover.setFixedSize(side, side)

    def _build_library(self):
        page = QWidget()
        h = QHBoxLayout(page)
        h.setContentsMargins(0, 14, 0, 0)
        self.lib_split = make_splitter(self)
        h.addWidget(self.lib_split)
        leftw = QWidget()
        left = QVBoxLayout(leftw)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Search your library")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.apply_filter)
        bar.addWidget(self.filter, 1)
        self.lib_sort = core.get_setting(self.con, "sort_library", "artist")
        self.lib_sort_box = sort_combo(LIB_SORTS, self.lib_sort, self._set_lib_sort)
        bar.addWidget(self.lib_sort_box)
        bar.addWidget(button("+  Add record", self.open_add, "primary"))
        left.addLayout(bar)
        self.lib_empty = label("Your library is empty. Click Add record to find an album on MusicBrainz.",
                               "muted", wrap=True)
        left.addWidget(self.lib_empty)
        self.lib_list = make_list(self.covers, 76)
        self.lib_list.currentItemChanged.connect(lambda cur, prev: self.on_select(cur))
        self.lib_list.itemDoubleClicked.connect(lambda item: self.log_btn.setFocus())
        left.addWidget(self.lib_list, 1)
        leftw.setMinimumWidth(380)
        self.lib_split.addWidget(leftw)
        self.lib_split.addWidget(self.detail)
        self._split_stretch(self.lib_split)
        return page

    def _build_detail(self):
        outer = card()
        outer.setMinimumWidth(372)  # drag the divider to make it wider; it also grows with the window
        outer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("detailInner")
        inner.setStyleSheet("QWidget#detailInner { background: transparent; }")
        scroll.setWidget(inner)
        scroll.viewport().setAutoFillBackground(False)
        ol.addWidget(scroll)
        self.detail_scroll = scroll
        self.detail_inner = inner
        scroll.viewport().installEventFilter(self)  # resize the cover with the panel
        v = QVBoxLayout(inner)
        v.setContentsMargins(22, 22, 22, 22)
        v.setSpacing(8)
        self.cover = CoverWidget(self.covers, 180)
        v.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(4)
        self.d_title = label("", "h2", wrap=True)
        self.d_artist = label("", "lead", wrap=True)
        self.d_meta = label("", "muted", wrap=True)
        self.d_note = label("", "muted", wrap=True)
        for w in (self.d_title, self.d_artist, self.d_meta, self.d_note):
            v.addWidget(w)
        self.btn_addlib = button("+  Add to library", self.add_pending_to_library)
        self.btn_addlib.setToolTip("Add this record to your library without logging a play")
        self.btn_addlib.setVisible(False)
        v.addWidget(self.btn_addlib, 0, Qt.AlignmentFlag.AlignLeft)
        acts = QHBoxLayout()
        acts.setSpacing(4)
        self.btn_edit = button("Edit", self.edit_record, "ghost")
        self.btn_cover = button("Change cover", self.set_cover, "ghost")
        self.btn_remove = button("Remove", self.remove_record, "ghost")
        self.btn_info = button("Album info", self.jump_to_about, "ghost")
        self.btn_info.setToolTip("Jump to the album summary and track listing")
        for b in (self.btn_edit, self.btn_cover, self.btn_remove, self.btn_info):
            acts.addWidget(b)
        acts.addStretch(1)
        v.addLayout(acts)
        self.sides_caption = label("SIDES PLAYED", "caption")
        v.addWidget(self.sides_caption)
        self.chips_box = QWidget()
        self.chips_grid = QGridLayout(self.chips_box)
        self.chips_grid.setContentsMargins(0, 0, 0, 0)
        self.chips_grid.setSpacing(8)
        v.addWidget(self.chips_box)
        self.when_caption = label("WHEN", "caption")
        v.addWidget(self.when_caption)
        self.when = WhenPicker(self.con)
        v.addWidget(self.when)
        self.with_caption = label("PLAYING WITH", "caption")
        v.addWidget(self.with_caption)
        self.stylus_combo = QComboBox()
        self.stylus_combo.setToolTip("The cartridge / stylus this play is logged against. Changing it also puts that cartridge / stylus "
                                     "on the turntable.")
        self.stylus_combo.activated.connect(lambda i: self.make_current(self.stylus_combo.itemData(i)))
        v.addWidget(self.stylus_combo)
        row = QHBoxLayout()
        self.partial = QLineEdit()
        self.partial.setPlaceholderText("Stopped early? mm:ss")
        self.partial.setToolTip("Leave blank when you played the whole side")
        self.times = QSpinBox()
        self.times.setRange(1, 20)
        self.times.setPrefix("x ")
        self.times.setToolTip("How many times you played it")
        row.addWidget(self.partial, 1)
        row.addWidget(self.times)
        v.addLayout(row)
        self.log_btn = button("Log play", self.do_log, "primary")
        self.log_btn.setMinimumHeight(44)
        v.addWidget(self.log_btn)
        self.d_msg = label("", "ok", wrap=True)
        self.d_msg.linkActivated.connect(self._msg_link)  # the Undo link after logging a play
        v.addWidget(self.d_msg)
        self.about_box = self._build_about()
        v.addWidget(self.about_box)
        v.addStretch(1)
        self.detail_controls = [self.btn_edit, self.btn_cover, self.btn_remove, self.btn_info, self.when_caption, self.when, self.partial,
                                self.times, self.log_btn, self.sides_caption, self.with_caption,
                                self.stylus_combo]
        return outer

    # ---- play log ---------------------------------------------------------------
    def _build_playlog(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 14, 0, 0)
        v.setSpacing(12)
        v.addWidget(label("Each time you log a play appears here once. Removing a play takes it out of your history "
                          "and play counts, and off the cartridge / stylus it was logged with. Click a column "
                          "heading to sort; click it again to reverse.", "muted", wrap=True))
        self.plog = table(["Played", "Record", "What was logged", "Length", "Cartridge / stylus", ""], 1)
        self._sortable(self.plog, "sort_playlog", unsortable=5)
        self.plog.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.plog.verticalHeader().setDefaultSectionSize(40)
        self.plog.cellClicked.connect(lambda r, c: self.remove_logs([self._plog_group(r)]) if c == 5 else None)
        self.plog.setMouseTracking(True)
        self.plog.cellEntered.connect(lambda r, c: self.plog.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if c == 5 else Qt.CursorShape.ArrowCursor))
        delete = QAction(self.plog)
        delete.setShortcuts([QKeySequence(QKeySequence.StandardKey.Delete), QKeySequence("Backspace")])
        delete.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        delete.triggered.connect(self.remove_selected_logs)
        self.plog.addAction(delete)
        v.addWidget(self.plog, 1)
        bar = QHBoxLayout()
        bar.addWidget(button("Remove selected", self.remove_selected_logs, "danger"))
        self.plog_count = label("", "muted")
        bar.addWidget(self.plog_count)
        bar.addStretch(1)
        v.addLayout(bar)
        return page

    def refresh_playlog(self):
        self.plog_groups = core.play_log(self.con, limit=500)
        t = self.plog
        t.setRowCount(len(self.plog_groups))
        for i, g in enumerate(self.plog_groups):
            t.setItem(i, 0, cell(core.to_dt(g["played_at"]).strftime("%d %b %Y  %H:%M"), i, (g["played_at"], max(g["ids"]))))
            t.setItem(i, 1, cell(g["record_label"], None, sort_name(g["record_label"])))
            t.setItem(i, 2, cell(g["what"], None, g["what"].lower()))
            t.setItem(i, 3, cell(core.fmt_dur(g["seconds"]), None, g["seconds"]))
            t.setItem(i, 4, cell(g["stylus"] or "", None, (g["stylus"] or "").lower()))
            rm = cell("Remove")
            rm.setForeground(QColor(C["accent"]))
            rm.setToolTip("Remove this play")
            f = rm.font()
            f.setBold(True)
            rm.setFont(f)
            t.setItem(i, 5, rm)
        self._resort(t)
        total = self.con.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
        self.plog_count.setText("{} logged play{}{}".format(
            len(self.plog_groups), "" if len(self.plog_groups) == 1 else "s",
            " (the latest 500)" if len(self.plog_groups) == 500 else "") if total else "No plays logged yet.")

    def remove_selected_logs(self):
        rows = sorted({i.row() for i in self.plog.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "Play log", "Select the plays to remove first.")
            return
        self.remove_logs([self._plog_group(r) for r in rows])

    def _plog_group(self, row):
        """The logged play shown on a row (rows move when the table is sorted)."""
        return self.plog_groups[self.plog.item(row, 0).data(ROLE)]

    # ---- sortable tables -----------------------------------------------------------
    def _sortable(self, t, setting, unsortable=None):
        """Click a column heading to sort by it; click again to reverse. The choice is remembered."""
        col, order = 0, "desc"
        saved = core.get_setting(self.con, setting, "")
        if ":" in saved:
            c, o = saved.split(":", 1)
            if c.isdigit() and int(c) < t.columnCount() and int(c) != unsortable:
                col, order = int(c), o
        t.setProperty("sort_setting", setting)
        t.setProperty("sort_col", col)
        t.setProperty("sort_order", order)
        t.setProperty("unsortable", -1 if unsortable is None else unsortable)
        hh = t.horizontalHeader()
        hh.setSortIndicatorShown(True)
        hh.setSectionsClickable(True)
        hh.setSortIndicator(col, Qt.SortOrder.DescendingOrder if order == "desc" else Qt.SortOrder.AscendingOrder)
        hh.sortIndicatorChanged.connect(lambda c, o, tt=t: self._sort_changed(tt, c, o))

    def _sort_changed(self, t, col, order):
        if col == t.property("unsortable"):  # the Remove column: keep the current sort
            t.horizontalHeader().blockSignals(True)
            t.horizontalHeader().setSortIndicator(t.property("sort_col"), Qt.SortOrder.DescendingOrder
                                                  if t.property("sort_order") == "desc" else Qt.SortOrder.AscendingOrder)
            t.horizontalHeader().blockSignals(False)
            return
        name = "desc" if order == Qt.SortOrder.DescendingOrder else "asc"
        t.setProperty("sort_col", col)
        t.setProperty("sort_order", name)
        core.set_setting(self.con, t.property("sort_setting"), "{}:{}".format(col, name))
        self._resort(t)

    def _resort(self, t):
        order = Qt.SortOrder.DescendingOrder if t.property("sort_order") == "desc" else Qt.SortOrder.AscendingOrder
        t.sortItems(t.property("sort_col"), order)

    def remove_logs(self, groups, ask=True):
        if ask:
            if len(groups) == 1:
                g = groups[0]
                text = "Remove this play?\n\n{}\n{} ({})\nPlayed {}{}\n\nIt comes out of your history and play " \
                       "counts{}.".format(g["record_label"], g["what"], core.fmt_dur(g["seconds"]),
                                         core.to_dt(g["played_at"]).strftime("%d %b %Y at %H:%M"),
                                         " with " + g["stylus"] if g["stylus"] else "",
                                         ", and {} comes off {}'s hours".format(core.fmt_dur(g["seconds"]), g["stylus"])
                                         if g["stylus"] else "")
            else:
                total = sum(g["seconds"] for g in groups)
                text = "Remove these {} plays?\n\nTogether they add up to {}, which comes out of your history, " \
                       "play counts and the hours of the cartridges / styli they were logged with.".format(
                           len(groups), core.fmt_dur(total))
            if QMessageBox.question(self, "Remove play" if len(groups) == 1 else "Remove plays", text) \
                    != QMessageBox.StandardButton.Yes:
                return False
        core.remove_plays(self.con, [i for g in groups for i in g["ids"]])
        self.refresh_all()
        return True

    def _msg_link(self, link):
        if link.startswith("undo:"):
            log_id = int(link.split(":", 1)[1])
            ids = [r[0] for r in self.con.execute("SELECT id FROM plays WHERE log_id = ?", (log_id,))]
            if ids:
                core.remove_plays(self.con, ids)
                self.refresh_all()
            self.d_msg.setText("Removed that play.")
            restyle(self.d_msg, "muted")

    def _build_history(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 14, 0, 0)
        v.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label("Show plays with", "muted"))
        self.hist_filter = QComboBox()
        self.hist_filter.setMinimumWidth(280)
        self.hist_filter.currentIndexChanged.connect(lambda i: self.refresh_history())
        top.addWidget(self.hist_filter)
        top.addStretch(1)
        v.addLayout(top)
        self.hist = table(["When", "Record", "Side", "Length", "Cartridge / stylus"], 1)
        self._sortable(self.hist, "sort_history")
        self.hist.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        v.addWidget(self.hist, 1)
        bar = QHBoxLayout()
        bar.addWidget(button("Delete selected", self.delete_plays, "danger"))
        self.hist_count = label("", "muted")
        self.hist_count.setToolTip("Click a column heading to sort; click it again to reverse")
        bar.addWidget(self.hist_count)
        bar.addStretch(1)
        bar.addWidget(button("Export CSV", self.export_csv))
        v.addLayout(bar)
        return page

    # ---- stats: lifetime charts and the weekly heatmap ----------------------------
    def _build_stats(self):
        self.stats_sid = None  # None: follow the cartridge / stylus on the turntable
        self.stats_dirty = True
        self.stats_measure = core.get_setting(self.con, "stats_measure", "sides")
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 14, 0, 0)
        outer.setSpacing(12)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.st_combo = QComboBox()
        self.st_combo.setMinimumWidth(300)
        self.st_combo.setToolTip("Shows the cartridge / stylus on the turntable unless you pick another one")
        self.st_combo.activated.connect(self._stats_pick)
        bar.addWidget(self.st_combo)
        bar.addStretch(1)
        self.st_seg = QButtonGroup(self)
        for key, text, pos in (("sides", "Sides played", "first"), ("hours", "Hours", "last")):
            b = button(text, None, "seg")
            b.setCheckable(True)
            b.setProperty("pos", pos)
            b.setProperty("key", key)
            b.setChecked(key == self.stats_measure)
            self.st_seg.addButton(b)
            bar.addWidget(b)
        self.st_seg.buttonClicked.connect(self._stats_measure)
        bar.addSpacing(6)
        self.st_unit = QComboBox()
        for text, key in (("Group automatically", ""), ("By day", "day"), ("By week", "week"), ("By month", "month")):
            self.st_unit.addItem(text, key)
        self.st_unit.setCurrentIndex(max(0, self.st_unit.findData(core.get_setting(self.con, "stats_unit", ""))))
        self.st_unit.activated.connect(self._stats_unit)
        bar.addWidget(self.st_unit)
        outer.addLayout(bar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("detailInner")
        inner.setStyleSheet("QWidget#detailInner { background: transparent; }")
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        self.st_scroll = scroll
        scroll.viewport().installEventFilter(self)  # fit the charts to the space available
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 6, 4)
        v.setSpacing(12)
        self.st_empty = label("", "muted", wrap=True)
        self.st_empty.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        v.addWidget(self.st_empty)
        self.st_body = QWidget()
        bv = QVBoxLayout(self.st_body)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(10)
        v.addWidget(self.st_body, 1)  # the charts take whatever height the window has
        self.st_compact = False
        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.st_tiles = []
        for _ in range(4):
            c = card()
            cl = QVBoxLayout(c)
            cl.setContentsMargins(16, 10, 16, 10)
            cl.setSpacing(1)
            cap, val, sub = label("", "caption"), label("", "statValue"), label("", "muted", wrap=True)
            for w in (cap, val, sub):
                cl.addWidget(w)
            cl.addStretch(1)
            tiles.addWidget(c, 1)
            self.st_tiles.append((cap, val, sub))
        bv.addLayout(tiles)

        self.st_charts = ReorderBox()  # drag a chart by its handle, or use its arrows, to reorder
        bv.addWidget(self.st_charts, 1)
        self.st_cum, self.st_per, self.st_heat = CumulativeChart(), PeriodChart(), HeatmapChart()
        cards = {}
        for key, chart in (("lifetime", self.st_cum), ("periods", self.st_per), ("heatmap", self.st_heat)):
            cards[key] = ChartCard(key, chart, self.st_charts)
            self.st_charts.add(cards[key])
        self.st_cards = cards
        self.st_cum_cap, self.st_per_cap, self.st_heat_cap = (cards[k].cap for k in ("lifetime", "periods", "heatmap"))
        self.st_charts.set_order(core.get_setting(self.con, "stats_order", "lifetime,periods,heatmap").split(","))
        self.st_charts.moved.connect(lambda order: core.set_setting(self.con, "stats_order", ",".join(order)))
        return page

    # ---- top 10: the most played albums as a grid of covers ----------------------
    def _build_top(self):
        self.top_dirty = True
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 14, 0, 0)
        v.setSpacing(12)
        bar = QHBoxLayout()
        self.top_note = label("Your most played albums, by full album plays. Click a cover to see its stats, "
                              "and click it again to turn it back.", "muted", wrap=True)
        bar.addWidget(self.top_note, 1)
        self.top_period = QComboBox()
        for key, name, _ in core.TOP_PERIODS:
            self.top_period.addItem(name, key)
        self.top_period.setCurrentIndex(max(0, self.top_period.findData(core.get_setting(self.con, "top_period", "all"))))
        self.top_period.activated.connect(self._top_period)
        bar.addWidget(self.top_period)
        v.addLayout(bar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # always shown, so the grid's width doesn't jump as rows appear and the scroll bar comes and goes
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.top_scroll = scroll
        inner = QWidget()
        inner.setObjectName("detailInner")
        inner.setStyleSheet("QWidget#detailInner { background: transparent; }")
        scroll.setWidget(inner)
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 4, 8, 8)
        self.top_empty = label("", "muted", wrap=True)
        iv.addWidget(self.top_empty)
        self.top_grid = TopGrid()
        iv.addWidget(self.top_grid)
        iv.addStretch(1)
        v.addWidget(scroll, 1)
        return page

    def _top_period(self, i):
        core.set_setting(self.con, "top_period", self.top_period.itemData(i))
        self.refresh_top(force=True)

    def refresh_top(self, force=False):
        """Rebuild the Top 10 (straight away if it's open, otherwise when it's next opened)."""
        if self.tabs.currentWidget() is not self.top_page and not force:
            self.top_dirty = True
            return
        self.top_dirty = False
        key = self.top_period.currentData() or "all"
        name = self.top_period.currentText()
        since = core.period_since(key)
        top = core.top_albums(self.con, 10, since)
        tiles = [AlbumTile(self.covers, i, t, core.album_stats(self.con, t["record"]["id"], since),
                           self.record_cover_key(t["record"]), name)
                 for i, t in enumerate(top, 1)]
        self.top_grid.set_tiles(tiles)
        self.top_empty.setText("" if top else ("Nothing played in that time yet." if since else
                               "Log some plays and your most played albums will appear here."))
        self.top_empty.setVisible(not top)

    def _fit_stats(self, force=False):
        """On a short window, tuck each chart's explanation into its tooltip so the charts get the room."""
        vp = self.st_scroll.viewport().height()
        compact = vp < 640
        if compact == self.st_compact and not force:
            return
        self.st_compact = compact
        for key, c in self.st_cards.items():
            c.set_sub(getattr(self, "st_subs", {}).get(key, ""), not compact)
        for _, _, sub in self.st_tiles:
            sub.setVisible(not compact and bool(sub.text()))

    def _stats_pick(self, i):
        sid = self.st_combo.itemData(i)
        cur = core.active_stylus(self.con)
        self.stats_sid = None if cur and sid == cur["id"] else sid
        self.refresh_stats(force=True)

    def _stats_measure(self, b):
        self.stats_measure = b.property("key")
        core.set_setting(self.con, "stats_measure", self.stats_measure)
        self.refresh_stats(force=True)

    def _stats_unit(self, i):
        core.set_setting(self.con, "stats_unit", self.st_unit.itemData(i) or "")
        self.refresh_stats(force=True)

    def refresh_stats(self, force=False):
        """Redraw the Stats tab (straight away if it's open, otherwise when it's next opened)."""
        if self.tabs.currentWidget() is not self.stats_page and not force:
            self.stats_dirty = True
            return
        self.stats_dirty = False
        self.st_charts._refresh_buttons()  # arrow colours follow the theme
        rows = self.con.execute("SELECT * FROM styli ORDER BY active DESC, id DESC").fetchall()
        cur = core.active_stylus(self.con)
        ids = [r["id"] for r in rows]
        sid = self.stats_sid if self.stats_sid in ids else (cur["id"] if cur else (ids[0] if ids else None))
        self.st_combo.clear()
        for r in rows:
            state = "on the turntable" if cur and r["id"] == cur["id"] else ("in use" if r["active"] else "retired")
            self.st_combo.addItem("{}  ({})".format(r["name"], state), r["id"])
        self.st_combo.setCurrentIndex(max(0, self.st_combo.findData(sid)))
        for w in (self.st_combo, self.st_unit):
            w.setVisible(bool(rows))
        for b in self.st_seg.buttons():
            b.setVisible(bool(rows))
        if sid is None:
            self.st_empty.setText("Add a cartridge / stylus (Manage cartridges / styli, above) and log some plays "
                                  "to see its lifetime charts here.")
            self.st_empty.setVisible(True)
            self.st_body.setVisible(False)
            return
        s = self.con.execute("SELECT * FROM styli WHERE id = ?", (sid,)).fetchone()
        t = core.stylus_timeline(self.con, s, self.st_unit.currentData() or None)
        grid = core.play_heatmap(self.con, sid)
        m = self.stats_measure
        self.st_empty.setVisible(False)
        self.st_body.setVisible(True)
        unit = t["unit"]
        if not t["sides"]:
            self.st_empty.setText("Nothing has been played with {} yet. Its charts fill in as you log plays.".format(s["name"]))
            self.st_empty.setVisible(True)
        summary = core.stylus_summary(self.con, s)
        tiles = [("SIDES PLAYED", str(t["sides"]), "since {}".format(t["installed"].strftime("%d %b %Y").lstrip("0"))),
                 ("HOURS", core.fmt_hours(t["seconds"]),
                  "{:.0f}% of its {:g} h rated life".format(summary["pct"], s["rated_hours"]) +
                  (" (includes {} from before)".format(core.fmt_hours(t["initial"])) if t["initial"] else ""))]
        busiest = max(t["buckets"], key=lambda b: measure_value(m, b["sides"], b["seconds"])) if t["buckets"] else None
        if busiest and busiest["sides"]:
            tiles.append(("BUSIEST " + unit.upper(), fmt_measure(m, measure_value(m, busiest["sides"], busiest["seconds"])),
                          period_label(busiest["start"], unit, long=True)))
        else:
            tiles.append(("BUSIEST " + unit.upper(), "None yet", ""))
        gap = t["gap"]
        if gap:
            ongoing = gap[2] >= core.now_local().date() and not s["retired_at"]
            tiles.append(("LONGEST BREAK", "{} day{}".format(gap[0], "" if gap[0] == 1 else "s"),
                          ("since {} (still going)" if ongoing else "{} to {}").format(
                              *(d.strftime("%d %b %Y").lstrip("0") for d in gap[1:]))))
        else:
            tiles.append(("LONGEST BREAK", "None", "You've played something every day"
                          if t["sides"] else ""))
        for (cap, val, sub), (a, b, c) in zip(self.st_tiles, tiles):
            cap.setText(a)
            val.setText(b)
            sub.setText(c)
            sub.setVisible(bool(c) and not self.st_compact)
            cap.parentWidget().setToolTip(c)
        what = "sides played" if m == "sides" else "hours"
        self.st_cum_cap.setText("LIFETIME: {} SO FAR".format(what.upper()))
        subs = {}
        subs["lifetime"] = (
            "Every {} logged with {}, from {} to {}.".format(
                "side" if m == "sides" else "hour", s["name"], t["start"].strftime("%d %b %Y").lstrip("0"),
                "its retirement" if s["retired_at"] else "today") +
            (" The dashed lines mark the wear warning and rated life." if m == "hours" and
             s["rated_hours"] * s["warn_pct"] / 100.0 <= max(1e-9, t["seconds"] / 3600.0) * 1.6 else ""))
        self.st_per_cap.setText("{} EACH {}".format(what.upper(), unit.upper()))
        subs["periods"] = "Tall bars are your busiest {0}s; gaps are {0}s when nothing was played.".format(unit)
        fav = core.favourite_time(grid)
        self.st_heat_cap.setText("WHEN YOU PLAY")
        subs["heatmap"] = (("You play most on {} {}s. ".format(fav[0], fav[1]) if fav else "") +
                           "Each square is one hour of the week, going by the time each play was logged for.")
        self.st_subs = subs
        self._fit_stats(force=True)
        self.st_cum.set_data(t, m)
        self.st_per.set_data(t, m)
        self.st_heat.set_data(grid if t["sides"] else None, m)
        self.st_heat.empty = "No plays to show yet."

    # ---- refreshing --------------------------------------------------------
    def refresh_all(self):
        self.refresh_status()
        self.refresh_library()
        self.refresh_history()
        self.refresh_playlog()
        self.refresh_stats()
        self.refresh_top()
        self.refresh_dg_marks()
        if self.view == "discogs":
            self.on_dg_select(keep_msg=True)

    def refresh_status(self):
        """Rebuild the row of cartridge / stylus cards and the 'playing with' picker."""
        bar = self.strip_scroll.horizontalScrollBar()
        scroll_pos = bar.value()
        clear_layout(self.strip_lay)
        in_use = core.styli_in_use(self.con)
        cur = core.active_stylus(self.con)
        for s in in_use:
            self.strip_lay.addWidget(StylusCard(self, s, cur is not None and s["id"] == cur["id"]))
        if not in_use:
            empty = card()
            empty.setFixedSize(420, 150)
            ev = QVBoxLayout(empty)
            ev.setContentsMargins(20, 16, 20, 16)
            ev.addWidget(label("No cartridge / stylus set up", "cardTitle"))
            ev.addWidget(label("Add the cartridge / stylus on your turntable and its rated life from the maker to start "
                               "tracking wear.", "muted", wrap=True))
            ev.addWidget(button("Add a cartridge / stylus", self.open_styli, "primary"), 0, Qt.AlignmentFlag.AlignLeft)
            self.strip_lay.addWidget(empty)
        self.strip_lay.addStretch(1)
        if cur:
            m = core.stylus_summary(self.con, cur)
            self.strip_summary.setText("{}  ·  {} of {:g} h  ·  {:.0f}% worn".format(
                cur["name"], core.fmt_hours(m["used"]).replace(" h", ""), cur["rated_hours"], m["pct"]))
        else:
            self.strip_summary.setText("None set up yet")
        combo = self.stylus_combo
        combo.blockSignals(True)
        combo.clear()
        for s in in_use:
            combo.addItem(s["name"], s["id"])
        if not in_use:
            combo.addItem("No cartridge / stylus set up", None)
        combo.setCurrentIndex(max(0, combo.findData(cur["id"])) if cur else 0)
        combo.blockSignals(False)
        QTimer.singleShot(0, lambda: bar.setValue(scroll_pos))
        self.refresh_history_filter()

    def record_cover_key(self, r):
        return ("record", r["id"], r["mbid"], r["rgid"], 500, r["cover_url"], r["artist"], r["title"])

    def refresh_library(self):
        keep = self.lib_sel
        lst = self.lib_list
        lst.blockSignals(True)
        lst.clear()
        rows = self.con.execute(
            "SELECT r.*, "
            "(SELECT MAX(played_at) FROM plays p WHERE p.record_id = r.id) last "
            "FROM records r ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE").fetchall()
        selected = None
        counts = core.play_counts(self.con)
        items = [dict(r=r, artist=r["artist"], title=r["title"], year=r["year"], added=r["added_at"], last=r["last"],
                      album=counts.get(r["id"], {}).get("album", 0), sides=counts.get(r["id"], {}).get("sides", 0))
                 for r in rows]
        for r in (i["r"] for i in sort_records(items, self.lib_sort)):
            sub = r["artist"] + ("  ·  {}".format(r["year"]) if r["year"] else "")
            item = QListWidgetItem()
            item.setData(ROLE, {
                "id": r["id"], "title": r["title"], "subtitle": sub, "cover": self.record_cover_key(r),
                "meta": core.fmt_plays(counts.get(r["id"], {}).get("album", 0), counts.get(r["id"], {}).get("sides", 0)),
                "meta2": "last " + core.to_dt(r["last"]).strftime("%d %b %Y") if r["last"] else "not played yet",
                "search": "{} {}".format(r["artist"], r["title"]).lower()})
            lst.addItem(item)
            if r["id"] == keep:
                selected = item
        lst.blockSignals(False)
        self.apply_filter()
        self.lib_empty.setVisible(not rows)
        if selected is not None and not selected.isHidden():
            lst.blockSignals(True)
            lst.setCurrentItem(selected)
            lst.blockSignals(False)
            lst.scrollToItem(selected)
            if self.view == "library":
                self.show_detail(keep, keep_msg=True)
        else:
            lst.setCurrentItem(None)
            self.lib_sel = None
            if self.view == "library":
                self.show_detail(None)

    def _set_lib_sort(self, how):
        self.lib_sort = how
        core.set_setting(self.con, "sort_library", how)
        self.refresh_library()

    def apply_filter(self):
        words = self.filter.text().lower().split()
        for i in range(self.lib_list.count()):
            item = self.lib_list.item(i)
            item.setHidden(bool(words) and not all(w in item.data(ROLE)["search"] for w in words))

    def refresh_history_filter(self):
        f = self.hist_filter
        keep = f.currentData()
        f.blockSignals(True)
        f.clear()
        f.addItem("All cartridges / styli", None)
        rows = self.con.execute("SELECT * FROM styli ORDER BY active DESC, id DESC").fetchall()
        for s in rows:
            f.addItem("{}{}".format(s["name"], "" if s["active"] else "  (retired)"), s["id"])
        idx = f.findData(keep) if keep is not None else 0
        f.setCurrentIndex(max(0, idx))
        f.blockSignals(False)

    def show_stylus_plays(self, sid):
        self.refresh_history_filter()
        self.hist_filter.setCurrentIndex(max(0, self.hist_filter.findData(sid)))
        self.tabs.setCurrentWidget(self.hist_page)
        self.refresh_history()

    def refresh_history(self):
        sid = self.hist_filter.currentData() if hasattr(self, "hist_filter") else None
        where, params = ("WHERE p.stylus_id = ?", [sid]) if sid is not None else ("", [])
        rows = self.con.execute(
            "SELECT p.*, s.name stylus FROM plays p LEFT JOIN styli s ON s.id = p.stylus_id {} "
            "ORDER BY p.played_at DESC, p.id DESC LIMIT 2000".format(where), params).fetchall()
        t = self.hist
        t.setRowCount(len(rows))
        for i, p in enumerate(rows):
            t.setItem(i, 0, cell(core.to_dt(p["played_at"]).strftime("%d %b %Y  %H:%M"), p["id"], (p["played_at"], p["id"])))
            t.setItem(i, 1, cell(p["record_label"], None, sort_name(p["record_label"])))
            t.setItem(i, 2, cell(p["side"], None, p["side"].lower()))
            t.setItem(i, 3, cell(core.fmt_dur(p["seconds"]), None, p["seconds"]))
            t.setItem(i, 4, cell(p["stylus"] or "", None, (p["stylus"] or "").lower()))
        self._resort(t)
        total = self.con.execute("SELECT COUNT(*), COALESCE(SUM(seconds), 0) FROM plays p {}".format(where),
                                 params).fetchone()
        self.hist_count.setText("{} entries, {} in total{}".format(
            total[0], core.fmt_hours(total[1]), " (latest 2000 shown)" if total[0] > 2000 else ""))

    # ---- library actions ---------------------------------------------------
    def on_select(self, item):
        rid = item.data(ROLE)["id"] if item is not None else None
        self.lib_sel = rid
        if self.view == "library" and rid != self.current:
            self.show_detail(rid)

    def _clear_chips(self):
        clear_layout(self.chips_grid)
        self.chips = {}

    def _set_chips(self, sides):
        self._clear_chips()
        for i, s in enumerate(sides):
            text = "Side {}   {}".format(s["label"], core.fmt_dur(s["seconds"]) if s["seconds"] else "?:??")
            chip = QPushButton(text)
            chip.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)  # never wider than the panel
            chip.setMinimumWidth(60)
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.setChecked(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            if s["missing"]:
                chip.setToolTip("Some track lengths were missing, so this may be short. Use Edit to correct it.")
            self.chips_grid.addWidget(chip, i // 2, i % 2)
            self.chips[s["label"]] = chip
        self.log_btn.setEnabled(bool(self.chips))

    def show_detail(self, rid, keep_msg=False, empty=None):
        self.current = rid
        self.pending = None
        if self.view == "library":
            self.lib_sel = rid
        self._clear_chips()
        if not keep_msg:
            self.d_msg.setText("")
        self.d_note.setVisible(False)
        rec = self.con.execute("SELECT * FROM records WHERE id = ?", (rid,)).fetchone() if rid else None
        for w in self.detail_controls:
            w.setVisible(rec is not None)
        self.btn_remove.setVisible(rec is not None and self.view == "library")
        self.btn_addlib.setVisible(False)
        if not rec:
            self.cover.show_cover(None, "")
            self.d_title.setText("Select a record")
            self.d_artist.setText("")
            self.d_meta.setText(empty or "Pick an album from your library to log a play.")
            self.log_btn.setEnabled(False)
            self.about_box.setVisible(False)
            return
        self.cover.show_cover(self.record_cover_key(rec), rec["title"])
        self.d_title.setText(breakable(rec["title"]))
        self.d_artist.setText(breakable(rec["artist"]))
        c = core.play_counts(self.con, rid).get(rid)
        played = "Not played yet" if not c else "{}, last {}".format(core.fmt_plays(c["album"], c["sides"]),
                                                                      core.to_dt(c["last"]).strftime("%d %b %Y"))
        self.d_meta.setText(breakable("   ·   ".join(x for x in (rec["year"], rec["format"]) if x)) + "\n" + played)
        self._set_chips(core.record_sides(self.con, rid))
        self.show_about_record(rec)

    def _level(self, sid=None):
        s = self.con.execute("SELECT * FROM styli WHERE id = ?", (sid,)).fetchone() if sid else \
            core.active_stylus(self.con)
        return core.stylus_summary(self.con, s)["level"] if s else None

    def make_current(self, sid):
        """Put a cartridge / stylus on the turntable: new plays are logged against it.

        The cards stay where they are; only the highlight moves.
        """
        if not sid:
            return
        core.set_current_stylus(self.con, sid)
        for i in range(self.strip_lay.count()):
            w = self.strip_lay.itemAt(i).widget()
            if isinstance(w, StylusCard):
                w.set_current(w.sid == sid)
        idx = self.stylus_combo.findData(sid)
        if idx >= 0:
            self.stylus_combo.setCurrentIndex(idx)
        self.stats_sid = None  # the Stats tab follows the turntable
        self.refresh_stats()

    def do_log(self):
        if self.pending is not None:  # a Discogs record: add it to the library first
            if not self.save_pending():
                return
        if self.current is None:
            return
        chosen = [k for k, c in self.chips.items() if c.isChecked()]
        if not chosen:
            self.d_msg.setText("Choose at least one side.")
            restyle(self.d_msg, "warn")
            return
        try:
            back_dated = not self.when.is_now()
            when = self.when.when()
            text = self.partial.text().strip()
            custom = core.parse_duration(text) if text else None
        except ValueError as e:
            QMessageBox.warning(self, "Log a play", str(e))
            return
        times = self.times.value()
        rec = self.con.execute("SELECT * FROM records WHERE id = ?", (self.current,)).fetchone()
        sides = {s["label"]: s for s in core.record_sides(self.con, rec["id"])}
        sid = self.stylus_combo.currentData()
        chosen_stylus = self.con.execute("SELECT * FROM styli WHERE id = ?", (sid,)).fetchone() if sid else None
        before = self._level(sid)
        stylus, total = core.log_play(self.con, rec, core.play_entries(sides, chosen, custom), when, times,
                                      chosen_stylus)
        self.partial.clear()
        self.times.setValue(1)
        self.when.set_now()  # the next play is "now" again unless you pick another time
        msg = "Logged side{} {}{} ({}){}{}.".format("s" if len(chosen) > 1 else "", " + ".join(chosen),
                                                      " x{}".format(times) if times > 1 else "", core.fmt_dur(total),
                                                      " for " + friendly_when(when) if back_dated else "",
                                                      " with {}".format(stylus["name"]) if stylus else "")
        if stylus and when < core.to_dt(stylus["installed_at"]):
            msg += " That's before {} was fitted ({}), so check the date.".format(
                stylus["name"], core.to_dt(stylus["installed_at"]).strftime("%d %b %Y"))
        if not stylus:
            msg += " No cartridge / stylus is set up, so it isn't counted against one."
        log_id = self.con.execute("SELECT MAX(log_id) FROM plays").fetchone()[0]
        self.refresh_all()
        self.d_msg.setText('{}&nbsp;&nbsp;<a href="undo:{}" style="color:{}">Undo</a>'.format(
            html.escape(msg), log_id, C["accent"]))
        restyle(self.d_msg, "ok")
        after = self._level(sid)
        if stylus and after != before and after in ("warn", "over"):
            QMessageBox.warning(self, "Cartridge / stylus wear", core.stylus_alert(
                self.con, self.con.execute("SELECT * FROM styli WHERE id = ?", (stylus["id"],)).fetchone()))

    def _record(self):
        return self.con.execute("SELECT * FROM records WHERE id = ?", (self.current,)).fetchone() \
            if self.current else None

    def edit_record(self):
        if self.pending is not None:
            self.edit_pending()
            return
        rec = self._record()
        if not rec:
            return
        dlg = SidesEditor(self, "Edit record", rec["artist"], rec["title"], rec["year"] or "",
                          [dict(s) for s in core.record_sides(self.con, rec["id"])],
                          note="Plays you have already logged keep the lengths they were logged with.")
        if dlg.exec():
            sides, artist, title, year = dlg.result_data
            self.con.execute("UPDATE records SET artist = ?, title = ?, year = ? WHERE id = ?",
                             (artist, title, year, rec["id"]))
            core.replace_sides(self.con, rec["id"], sides)
            self.refresh_all()

    def set_cover(self):
        rec = self._record()
        if not rec:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose a cover image", str(Path.home()),
                                              "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        if not path:
            return
        if QPixmap(path).isNull():
            QMessageBox.warning(self, "Cover image", "That file couldn't be read as an image.")
            return
        dest = core.library_cover_path(rec["id"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, str(dest))
        self.covers.forget_record(rec["id"])
        self.refresh_library()

    def remove_record(self):
        rec = self._record()
        if not rec:
            return
        if QMessageBox.question(self, "Remove record", "Remove {} by {} from your library?\n\nPlays you have "
                                "logged are kept, so cartridge / stylus hours don't change.".format(rec["title"], rec["artist"])
                                ) != QMessageBox.StandardButton.Yes:
            return
        self.con.execute("DELETE FROM records WHERE id = ?", (rec["id"],))
        self.con.commit()
        core.remove_record_cover(rec["id"])
        self.covers.forget_record(rec["id"])
        self.current = self.lib_sel = None
        self.refresh_all()

    def _store_rgid(self, rid, rgid):
        self.con.execute("UPDATE records SET rgid = ? WHERE id = ? AND rgid IS NULL", (rgid, rid))
        self.con.commit()

    def open_add(self):
        dlg = AddDialog(self)
        dlg.exec()
        self.update_discogs_tab()
        if dlg.saved_id:
            self.lib_sel = dlg.saved_id
            self.filter.clear()
            self.tabs.setCurrentWidget(self.lib_page)
            self.refresh_all()

    # ---- Discogs tab -----------------------------------------------------------
    def _build_discogs(self):
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 14, 0, 0)
        self.dg_split = make_splitter(self)
        row.addWidget(self.dg_split)
        leftw = QWidget()
        left = QVBoxLayout(leftw)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        bar = QHBoxLayout()
        self.dgf = QLineEdit()
        self.dgf.setPlaceholderText("Search your Discogs collection: artist, album, label or catalogue number")
        self.dgf.setClearButtonEnabled(True)
        self.dgf.textChanged.connect(lambda t: self.fill_dg_list())
        bar.addWidget(self.dgf, 1)
        self.dgv = QCheckBox("Vinyl only")
        self.dgv.setChecked(True)
        self.dgv.toggled.connect(lambda on: self.fill_dg_list())
        bar.addWidget(self.dgv)
        self.dg_sort = core.get_setting(self.con, "sort_discogs", "artist")
        dg_sorts = [x for x in LIB_SORTS if x[0] not in ("added", "played")] + [("added", "Recently added to Discogs")]
        self.dg_sort_box = sort_combo(dg_sorts, self.dg_sort, self._set_dg_sort)
        bar.addWidget(self.dg_sort_box)
        self.dg_refresh_btn = button("Refresh", lambda: self.load_dg_collection(refresh=True))
        self.dg_refresh_btn.setToolTip("Download your collection from Discogs again")
        bar.addWidget(self.dg_refresh_btn)
        left.addLayout(bar)
        self.dg_status = label("", "muted", wrap=True)
        left.addWidget(self.dg_status)
        self.dgl = make_list(self.covers, 76)
        self.dgl.currentItemChanged.connect(lambda cur, prev: self.on_dg_select())
        self.dgl.itemDoubleClicked.connect(lambda item: self.log_btn.setFocus())
        left.addWidget(self.dgl, 1)
        leftw.setMinimumWidth(380)
        self.dg_split.addWidget(leftw)
        self.dg_fetch_timer = QTimer(self)
        self.dg_fetch_timer.setSingleShot(True)
        self.dg_fetch_timer.setInterval(350)  # wait until you stop moving through the list
        self.dg_fetch_timer.timeout.connect(self.fetch_dg_sides)
        return page

    def update_discogs_tab(self):
        """Show the Discogs tab while an account is linked, between Library and History."""
        token, user = core.discogs_account(self.con)
        idx = self.tabs.indexOf(self.dg_page)
        if token and idx < 0:
            self.tabs.insertTab(1, self.dg_page, "Discogs collection")
            self.dg_user = None  # load the collection when the tab is first opened
        elif token and self.dg_user and self.dg_user != user:
            self.dg_user = None
            if self.view == "discogs":
                self.load_dg_collection()
        elif not token and idx >= 0:
            if self.tabs.currentWidget() is self.dg_page:
                self.tabs.setCurrentWidget(self.lib_page)
            self.tabs.removeTab(self.tabs.indexOf(self.dg_page))
            self.dg_items, self.dg_shown, self.dg_cache, self.dg_user = [], [], {}, None
            self.dgl.clear()

    def on_tab(self, idx):
        w = self.tabs.widget(idx)
        if w is self.lib_page:
            self.view = "library"
            self._place_detail(self.lib_split)
            self.pending = None
            self.show_detail(self.lib_sel)
        elif w is self.dg_page:
            self.view = "discogs"
            self._place_detail(self.dg_split)
            if self.dg_user is None:
                self.load_dg_collection()
            self.on_dg_select()
        else:
            self.view = "history"
            if w is self.stats_page and self.stats_dirty:
                self.refresh_stats()
            if w is self.top_page and self.top_dirty:
                self.refresh_top()

    def load_dg_collection(self, refresh=False):
        """Show the saved copy of the collection straight away, then refresh it from Discogs if it's old."""
        token, user = core.discogs_account(self.con)
        if not token:
            return
        self.dg_user = user
        if not refresh:
            items, age = core.load_discogs_cache(user)
            if items is not None:
                self.dg_items = items
                self.fill_dg_list()
                if age < core.DISCOGS_STALE_HOURS:
                    self._dg_count_status()
                    return
        self.dg_gen += 1
        gen, self.dg_new = self.dg_gen, []
        self.dg_refresh_btn.setEnabled(False)
        self._dg_status("Reading your Discogs collection...")
        run_task(lambda: core.discogs_collection_page(token, user, 1), lambda v: self._dg_page(v, gen, 1),
                 self._dg_failed, self.tasks)

    def _dg_page(self, val, gen, page):
        if gen != self.dg_gen:
            return
        batch, pages, total = val
        self.dg_new.extend(batch)
        token, user = core.discogs_account(self.con)
        if not token:
            return
        if page >= pages or not batch:
            self.dg_items = self.dg_new
            core.save_discogs_cache(user, self.dg_items)
            self.dg_refresh_btn.setEnabled(True)
            self.fill_dg_list()
            self._dg_count_status()
            return
        if page == 1 and not self.dg_items:
            self.dg_items = list(self.dg_new)
            self.fill_dg_list()
        self._dg_status("Reading your Discogs collection: {} of {}...".format(len(self.dg_new), total))
        run_task(lambda: core.discogs_collection_page(token, user, page + 1), lambda v: self._dg_page(v, gen, page + 1),
                 self._dg_failed, self.tasks)

    def _dg_failed(self, e):
        self.dg_refresh_btn.setEnabled(True)
        self._dg_status(str(e) if isinstance(e, core.MBError) else repr(e), "warn")

    def _dg_status(self, text, style="muted"):
        self.dg_status.setText(text)
        restyle(self.dg_status, style)

    def _dg_count_status(self):
        _, user = core.discogs_account(self.con)
        n = len(self.dg_items)
        shown = len(self.dg_shown)
        text = "{} record{} in {}'s Discogs collection".format(n, "" if n == 1 else "s", user)
        if shown != n:
            text += ", {} shown".format(shown)
        self._dg_status(text + ". Use Add to library, or log a play, to add a record to your library.")

    def _dg_play_info(self):
        """Discogs release number -> (record id, play counts) for records already in the library."""
        counts = core.play_counts(self.con)
        rows = self.con.execute("SELECT id, discogs_id FROM records WHERE discogs_id IS NOT NULL").fetchall()
        return {r["discogs_id"]: (r["id"], counts.get(r["id"], {"album": 0, "sides": 0, "last": None})) for r in rows}

    def _dg_row(self, n, i, lib):
        fmt = i["format"].split(";")[0]
        if i["id"] in lib:
            c = lib[i["id"]][1]
            meta2 = core.fmt_plays(c["album"], c["sides"]) if c["sides"] else "In your library"
        else:
            meta2 = i["label"]
        return {"i": n, "title": i["title"], "cover": ("url", i["cover"], 250) if i["cover"] else None,
                "subtitle": i["artist"] + ("  ·  " + i["year"] if i["year"] else ""),
                "meta": fmt if len(fmt) <= 24 else fmt[:23] + "...", "meta2": meta2}

    def _set_dg_sort(self, how):
        self.dg_sort = how
        core.set_setting(self.con, "sort_discogs", how)
        self.fill_dg_list()

    def fill_dg_list(self):
        infos = [core.discogs_item_info(it) for it in self.dg_items]
        plays = self._dg_play_info()
        for i in infos:
            c = plays.get(i["id"], (None, {}))[1]
            i.update(last=c.get("last"), album=c.get("album", 0), sides=c.get("sides", 0))
        infos = sort_records(infos, self.dg_sort)
        if self.dgv.isChecked():
            infos = [i for i in infos if i["vinyl"]]
        words = self.dgf.text().lower().split()
        if words:
            infos = [i for i in infos if all(w in "{} {} {} {}".format(i["artist"], i["title"], i["label"],
                                                                         i["year"]).lower() for w in words)]
        self.dg_shown = infos
        lib = self._dg_play_info()
        lst = self.dgl
        lst.blockSignals(True)
        lst.clear()
        select = None
        for n, i in enumerate(infos):
            item = QListWidgetItem()
            item.setData(ROLE, self._dg_row(n, i, lib))
            lst.addItem(item)
            if i["id"] == self.dg_sel:
                select = n
        lst.blockSignals(False)
        if select is not None:
            lst.setCurrentRow(select)
            lst.scrollToItem(lst.item(select))
        if self.dg_items and self.dg_refresh_btn.isEnabled():
            self._dg_count_status()
        if self.view == "discogs":
            self.on_dg_select()

    def refresh_dg_marks(self):
        """Update play counts in the Discogs list without rebuilding it (keeps your place)."""
        if not self.dg_shown:
            return
        lib = self._dg_play_info()
        for n in range(self.dgl.count()):
            item = self.dgl.item(n)
            item.setData(ROLE, self._dg_row(n, self.dg_shown[n], lib))

    def _dg_selected(self):
        item = self.dgl.currentItem()
        if item is None or not self.dg_shown:
            return None
        return self.dg_shown[item.data(ROLE)["i"]]

    def on_dg_select(self, keep_msg=False):
        i = self._dg_selected()
        if i is None:
            self.pending = None
            self.show_detail(None, empty="Pick a record from your Discogs collection to log a play.")
            return
        if i["id"] != self.dg_sel:
            keep_msg = False
        self.dg_sel = i["id"]
        row = self.con.execute("SELECT id FROM records WHERE discogs_id = ?", (i["id"],)).fetchone()
        if row:
            self.pending = None
            self.show_detail(row["id"], keep_msg=keep_msg)
            return
        self.show_pending(i, self.dg_cache.get(i["id"]), keep_msg)
        if i["id"] not in self.dg_cache:
            self.dg_fetch_timer.start()

    def fetch_dg_sides(self):
        """Fetch the track list of the record you've stopped on, so its sides can be shown."""
        i = self._dg_selected()
        if i is None or i["id"] in self.dg_cache or i["id"] in self.dg_loading:
            return
        token, _ = core.discogs_account(self.con)
        if not token:
            return
        did = i["id"]
        self.dg_loading.add(did)

        def done(info):
            self.dg_loading.discard(did)
            self.dg_cache[did] = info
            if self.view == "discogs" and self.dg_sel == did and self.pending_item and self.pending_item["id"] == did:
                self.show_pending(i, info)

        def failed(e):
            self.dg_loading.discard(did)
            if self.view == "discogs" and self.dg_sel == did:
                self.d_msg.setText("Couldn't get the track list: {}".format(str(e) if isinstance(e, core.MBError)
                                                                             else repr(e)))
                restyle(self.d_msg, "warn")

        run_task(lambda: core.prepare_discogs_release(token, did), done, failed, self.tasks)

    def show_pending(self, i, info, keep_msg=False):
        """A Discogs record that isn't in the library yet. info is None while its sides are loading."""
        self.current, self.pending_item, self.pending = None, i, info
        self._clear_chips()
        if not keep_msg:
            self.d_msg.setText("")
        for w in self.detail_controls:
            w.setVisible(True)
        self.btn_cover.setVisible(False)
        self.btn_remove.setVisible(False)
        self.btn_addlib.setVisible(True)
        self.btn_addlib.setEnabled(info is not None)
        self.cover.show_cover(("url", i["cover"], 500) if i["cover"] else None, i["title"])
        title, artist = (info["title"], info["artist"]) if info else (i["title"], i["artist"])
        self.d_title.setText(breakable(title))
        self.d_artist.setText(breakable(artist))
        self.d_meta.setText(breakable("   ·   ".join(x for x in (i["year"], i["format"], i["label"]) if x)))
        if info is None:
            self.btn_edit.setVisible(False)
            self.log_btn.setEnabled(False)
            self.d_note.setText("Getting the sides from Discogs...")
            restyle(self.d_note, "muted")
            self.d_note.setVisible(True)
            self.show_about_pending(None)
            return
        self._set_chips(info["sides"])
        notes = list(info["notes"])
        warn = bool(notes) or not info["sides"]
        if not info["sides"]:
            notes = ["Neither Discogs nor MusicBrainz has a track list for this release. Click Edit to enter "
                     "the side lengths from the sleeve or label."]
        notes.append("Add it to your library now, or it's added when you log a play.")
        self.d_note.setText(" ".join(notes))
        restyle(self.d_note, "warn" if warn else "muted")
        self.d_note.setVisible(True)
        self.log_btn.setEnabled(True)
        self.show_about_pending(info)

    def edit_pending(self):
        info = self.pending
        dlg = SidesEditor(self, "Check the sides", info["artist"], info["title"] or "", info["year"],
                          [dict(s) for s in info["sides"]], " ".join(info["notes"]),
                          "warn" if info["notes"] else "muted")
        if not dlg.exec():
            return False
        sides, artist, title, year = dlg.result_data
        info.update(sides=sides, artist=artist, title=title, year=year, notes=[], estimated=False)
        chosen = {k for k, c in self.chips.items() if c.isChecked()}
        self.show_pending(self.pending_item, info, keep_msg=True)
        for k, c in self.chips.items():
            c.setChecked(k in chosen or not chosen)
        return True

    def add_pending_to_library(self):
        if self.pending is None:
            return
        title = self.pending["title"]
        rid = self.save_pending(all_sides=True)
        if rid:
            self.refresh_all()
            self.d_msg.setText("Added {} to your library.".format(title))
            restyle(self.d_msg, "ok")

    def save_pending(self, all_sides=False):
        """Add the Discogs record on show to the library (checking its sides first if needed)."""
        info = self.pending
        dup = self.con.execute("SELECT id FROM records WHERE discogs_id = ?", (info["discogs_id"],)).fetchone()
        if dup:
            return dup["id"]
        chosen = [s for s in info["sides"] if all_sides or (s["label"] in self.chips and self.chips[s["label"]].isChecked())]
        if not info["sides"] or any(s["missing"] or not s["seconds"] for s in chosen):
            QMessageBox.information(self, "Check the sides", "Some track lengths are missing on Discogs and "
                                    "MusicBrainz, so please check the side lengths against the sleeve or label "
                                    "before logging.")
            if not self.edit_pending():
                return None
            info = self.pending
        about = dict(info.get("about") or {})
        if info["discogs_id"] in self.pending_wiki:  # the summary has been looked up already
            about.update(self.pending_wiki[info["discogs_id"]])
            about.update(fetched=time.time(), tracks_tried=True)
        rid = core.save_record(self.con, info["mbid"], info["artist"], info["title"], info["year"], info["format"],
                               info["sides"], info["rgid"], info["discogs_id"], info["cover_url"],
                               info.get("tracks"), about or None)
        self.pending, self.current = None, rid
        return rid

    # ---- album details: summary and track listing -------------------------------
    def _build_about(self):
        box = QWidget()
        av = QVBoxLayout(box)
        av.setContentsMargins(0, 10, 0, 0)
        av.setSpacing(6)
        av.addWidget(label("ABOUT THIS ALBUM", "caption"))
        self.ab_facts = label("", "muted", wrap=True)
        self.ab_text = label("", wrap=True)
        self.ab_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ab_link = label("", wrap=True)
        self.ab_link.setOpenExternalLinks(True)
        self.ab_notes_caption = label("PRESSING NOTES", "caption")
        self.ab_notes = label("", "muted", wrap=True)
        self.ab_notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for w in (self.ab_facts, self.ab_text, self.ab_link, self.ab_notes_caption, self.ab_notes):
            av.addWidget(w)
        av.addSpacing(6)
        self.tr_caption = label("TRACK LISTING", "caption")
        av.addWidget(self.tr_caption)
        self.tr_box = QWidget()
        self.tr_grid = QGridLayout(self.tr_box)
        self.tr_grid.setContentsMargins(0, 0, 0, 0)
        self.tr_grid.setHorizontalSpacing(10)
        self.tr_grid.setVerticalSpacing(5)
        self.tr_grid.setColumnStretch(1, 1)
        av.addWidget(self.tr_box)
        self.ab_status = label("", "muted", wrap=True)
        av.addWidget(self.ab_status)
        self.ab_refresh = button("Refresh album details", lambda: self.fetch_details_now(force=True), "ghost")
        av.addWidget(self.ab_refresh, 0, Qt.AlignmentFlag.AlignLeft)
        self.detail_timer = QTimer(self)
        self.detail_timer.setSingleShot(True)
        self.detail_timer.setInterval(400)  # wait until you stop moving through the list
        self.detail_timer.timeout.connect(self.fetch_details_now)
        return box

    def jump_to_about(self):
        bar = self.detail_scroll.verticalScrollBar()
        bar.setValue(min(bar.maximum(), self.about_box.y() - 12))

    def render_about(self, about, tracks, lengths, status="", can_refresh=False):
        about = about or {}
        self.about_box.setVisible(True)
        facts = []
        if about.get("genres"):
            facts.append("  ·  ".join(about["genres"][:4]))
        released = about.get("first_released") or about.get("released")
        if released:
            facts.append("First released {}".format(nice_date(released)) if about.get("first_released")
                         else "Released {}".format(nice_date(released)))
        if about.get("label"):
            facts.append(about["label"] + ("  ·  " + about["country"] if about.get("country") else ""))
        self.ab_facts.setText(breakable("\n".join(facts)))
        self.ab_facts.setVisible(bool(facts))
        self.ab_text.setText(breakable(about.get("text", "")))
        self.ab_text.setVisible(bool(about.get("text")))
        links = []
        if about.get("url"):
            links.append('<a href="{}" style="color:{}">Read more on Wikipedia</a>'.format(about["url"], C["accent"]))
        if about.get("discogs_url"):
            links.append('<a href="{}" style="color:{}">View on Discogs</a>'.format(about["discogs_url"], C["accent"]))
        self.ab_link.setText("&nbsp;&nbsp;·&nbsp;&nbsp;".join(links))
        self.ab_link.setVisible(bool(links))
        self.ab_notes.setText(breakable(about.get("notes", "")))
        self.ab_notes.setVisible(bool(about.get("notes")))
        self.ab_notes_caption.setVisible(bool(about.get("notes")))
        fill_track_grid(self.tr_grid, tracks, lengths)
        self.tr_caption.setVisible(bool(tracks))
        self.tr_box.setVisible(bool(tracks))
        self.ab_status.setText(status)
        self.ab_status.setVisible(bool(status))
        self.ab_refresh.setVisible(can_refresh)

    def show_about_record(self, rec, error=None):
        token, _ = core.discogs_account(self.con)
        tracks = core.get_tracks(self.con, rec["id"])
        about = core.get_about(rec)
        lengths = {s["label"]: s["seconds"] for s in core.record_sides(self.con, rec["id"])}
        need_t, need_a = core.details_needed(self.con, rec, token)
        online = bool(rec["mbid"] or rec["discogs_id"])
        loading = (need_t or need_a) and rec["id"] not in self.detail_failed
        if error:
            status = "Couldn't get album details ({}). Click Refresh album details to try again.".format(error)
        elif loading:
            status = "Getting album details..."
        elif not tracks and not (about or {}).get("text"):
            status = "No summary or track listing was found for this record." if online else \
                "No summary was found on Wikipedia, and records entered by hand have no track listing."
        elif not tracks:
            status = "No track listing was found for this record." if online else \
                "Records entered by hand have no track listing."
        else:
            status = ""
        self.render_about(about, tracks, lengths, status, can_refresh=not loading)
        if loading:
            self.detail_timer.start()

    def fetch_details_now(self, force=False):
        """Fetch the track listing and summary for the record on show, in the background."""
        rid = self.current
        if not rid or self.pending is not None or rid in self.detail_loading:
            return
        rec = self.con.execute("SELECT * FROM records WHERE id = ?", (rid,)).fetchone()
        if not rec:
            return
        token, _ = core.discogs_account(self.con)
        need_t, need_a = core.details_needed(self.con, rec, token)
        if force:
            self.detail_failed.discard(rid)
            need_t, need_a = bool(rec["mbid"] or (rec["discogs_id"] and token)), True
        if not (need_t or need_a) or rid in self.detail_failed:
            return
        self.detail_loading.add(rid)
        if force:
            self.ab_status.setText("Getting album details...")
            self.ab_status.setVisible(True)
            self.ab_refresh.setVisible(False)
        data = {k: rec[k] for k in rec.keys()}

        def done(res):
            self.detail_loading.discard(rid)
            core.store_details(self.con, rid, res)
            if res["about"] is None:
                self.detail_failed.add(rid)  # offline: don't keep retrying this session
            if self.current == rid and self.pending is None:
                self.show_about_record(self.con.execute("SELECT * FROM records WHERE id = ?", (rid,)).fetchone(),
                                       res["error"] if res["about"] is None else None)

        def failed(e):
            self.detail_loading.discard(rid)
            self.detail_failed.add(rid)
            if self.current == rid and self.pending is None:
                self.show_about_record(rec, str(e) if isinstance(e, core.MBError) else repr(e))

        run_task(lambda: core.fetch_details(data, token, need_t, need_a), done, failed, self.tasks)

    def show_about_pending(self, info):
        """Album details for a Discogs record that isn't in the library yet."""
        if info is None:
            self.render_about(None, [], {}, "Getting album details...")
            return
        did = info["discogs_id"]
        about = dict(info.get("about") or {})
        about.update(self.pending_wiki.get(did) or {})
        lengths = {s["label"]: s["seconds"] for s in info["sides"]}
        loading = did not in self.pending_wiki
        self.render_about(about, info.get("tracks") or [], lengths, "Looking for a summary..." if loading else "")
        if loading and did not in self.wiki_loading:
            self.wiki_loading.add(did)
            artist, title = info["artist"], info["title"] or ""

            def done(wiki):
                self.wiki_loading.discard(did)
                self.pending_wiki[did] = wiki or {}
                if self.pending is not None and self.pending.get("discogs_id") == did:
                    self.show_about_pending(self.pending)

            def failed(e):
                self.wiki_loading.discard(did)
                self.pending_wiki[did] = {}
                if self.pending is not None and self.pending.get("discogs_id") == did:
                    self.show_about_pending(self.pending)

            run_task(lambda: core.lookup_album_wikipedia(artist, title), done, failed, self.tasks)

    def open_prefs(self):
        PrefsDialog(self).exec()

    def set_theme(self, pref, save=True, tint=None):
        self.theme_pref = pref
        if tint is not None:
            self.tint = tint
        apply_theme(QApplication.instance(), pref, self.tint)
        self.refresh_status()
        if save:
            core.set_setting(self.con, "theme", pref)
            core.set_setting(self.con, "tint", self.tint)

    def follow_desktop(self, *args):
        if self.theme_pref == "system":
            self.set_theme("system", save=False)

    def prefetch_covers(self):
        """Queue a saved cover for every record that hasn't got one yet. Returns how many."""
        n = 0
        for r in self.con.execute("SELECT * FROM records").fetchall():
            if not core.library_cover_path(r["id"]).exists():
                key = self.record_cover_key(r)
                self.covers.missing.discard(key)
                self.covers.get(key, priority=5)
                n += 1
        return n

    def open_styli(self):
        StyliDialog(self).exec()
        self.refresh_all()

    # ---- history actions ----------------------------------------------------
    def delete_plays(self):
        rows = sorted({i.row() for i in self.hist.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "History", "Select the entries to delete first.")
            return
        if QMessageBox.question(self, "Delete plays", "Delete {} selected entr{}? This takes the time off "
                                "the cartridge / stylus total.".format(len(rows), "y" if len(rows) == 1 else "ies")
                                ) != QMessageBox.StandardButton.Yes:
            return
        ids = [self.hist.item(r, 0).data(ROLE) for r in rows]
        self.con.executemany("DELETE FROM plays WHERE id = ?", [(i,) for i in ids])
        self.con.commit()
        self.refresh_all()

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export plays", str(Path.home() / "vinyl-plays.csv"),
                                              "CSV files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            n = core.write_csv(self.con, fh)
        QMessageBox.information(self, "Export", "Exported {} plays to\n{}".format(n, path))


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------

def dialog_layout(dlg, title, min_width=None):
    dlg.setWindowTitle(title)
    if min_width:
        dlg.setMinimumWidth(min_width)
    v = QVBoxLayout(dlg)
    v.setContentsMargins(22, 20, 22, 18)
    v.setSpacing(12)
    return v


# ---------------------------------------------------------------------------
# When a play happened: "Now", or a day and time picked on a calendar
# ---------------------------------------------------------------------------

def calendar_icon(colour, size=16):
    """A small calendar drawn for the current theme."""
    img = QImage(size * 2, size * 2, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(colour), 2.6)
    p.setPen(pen)
    p.drawRoundedRect(QRectF(4, 7, 24, 21), 4, 4)
    p.drawLine(QPointF(4, 13.5), QPointF(28, 13.5))
    p.drawLine(QPointF(11, 3.5), QPointF(11, 9))
    p.drawLine(QPointF(21, 3.5), QPointF(21, 9))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(colour))
    for x, y in ((10, 19), (16, 19), (22, 19), (10, 24), (16, 24)):
        p.drawEllipse(QPointF(x, y), 1.6, 1.6)
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def friendly_when(when):
    today = core.now_local().date()
    day = when.date()
    if day == today:
        return "Today, " + when.strftime("%H:%M")
    if day == today - dt.timedelta(days=1):
        return "Yesterday, " + when.strftime("%H:%M")
    return when.strftime("%a %d %b %Y, %H:%M").replace(" 0", " ", 1)


class WhenPopup(QFrame):
    """A calendar and a time, opened from the When button. Days you've played records on are marked."""

    def __init__(self, picker, marked):
        super().__init__(picker, Qt.WindowType.Popup)
        self.picker = picker
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = card()
        box.setObjectName("popupCard")
        outer.addWidget(box)
        v = QVBoxLayout(box)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(10)
        quick = QHBoxLayout()
        quick.setSpacing(6)
        quick.addWidget(label("WHEN DID YOU PLAY IT?", "caption"))
        quick.addStretch(1)
        for text, slot in (("Now", self.pick_now), ("Yesterday", self.pick_yesterday)):
            b = button(text, slot, "chip")
            quick.addWidget(b)
        v.addLayout(quick)
        now = core.now_local()
        start = picker.value or now
        self.cal = QCalendarWidget()
        self.cal.setGridVisible(False)
        self.cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.cal.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.cal.setMaximumDate(QDate(now.year, now.month, now.day))  # no plays in the future
        self.cal.setSelectedDate(QDate(start.year, start.month, start.day))
        self.cal.setMinimumSize(300, 230)
        plain = QTextCharFormat()
        plain.setForeground(QColor(C["text"]))
        for d in range(1, 8):  # every day alike: weekends aren't red here
            self.cal.setWeekdayTextFormat(Qt.DayOfWeek(d), plain)
        head = QTextCharFormat()
        head.setForeground(QColor(C["muted"]))
        head.setFontWeight(QFont.Weight.Bold)
        self.cal.setHeaderTextFormat(head)
        mark = QTextCharFormat()
        mark.setForeground(QColor(C["accent"]))
        mark.setFontWeight(QFont.Weight.Bold)
        mark.setFontUnderline(True)
        for d in marked:
            self.cal.setDateTextFormat(QDate(d.year, d.month, d.day), mark)
        later = QTextCharFormat()
        later.setForeground(QColor(C["faint"]))
        today = QDate(now.year, now.month, now.day)
        for n in range(1, 43):  # days still to come can't be picked, so grey them out
            self.cal.setDateTextFormat(today.addDays(n), later)
        for name, text in (("qt_calendar_prevmonth", "‹"), ("qt_calendar_nextmonth", "›")):
            b = self.cal.findChild(QToolButton, name)
            if b:
                b.setIcon(QIcon())
                b.setText(text)
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.cal.activated.connect(lambda d: self.done_clicked())  # double-click a day
        v.addWidget(self.cal)
        if marked:
            v.addWidget(label("Underlined days already have plays logged.", "muted"))
        row = QHBoxLayout()
        row.addWidget(label("Time", "muted"))
        self.time = QTimeEdit(QTime(start.hour, start.minute))
        self.time.setDisplayFormat("HH:mm")
        row.addWidget(self.time)
        row.addStretch(1)
        row.addWidget(button("Done", self.done_clicked, "primary"))
        v.addLayout(row)
        self.note = label("", "warn", wrap=True)
        self.note.setVisible(False)
        v.addWidget(self.note)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.done_clicked()
            return
        super().keyPressEvent(event)

    def pick_now(self):
        self.picker.set_now()
        self.close()

    def pick_yesterday(self):
        self.cal.setSelectedDate(QDate.currentDate().addDays(-1))

    def chosen(self):
        d, t = self.cal.selectedDate(), self.time.time()
        return dt.datetime(d.year(), d.month(), d.day(), t.hour(), t.minute()).astimezone()

    def done_clicked(self):
        when = self.chosen()
        now = core.now_local()
        if when > now:
            self.note.setText("That time hasn't happened yet. Pick a time up to {}.".format(now.strftime("%H:%M")))
            self.note.setVisible(True)
            self.adjustSize()
            return
        self.picker.set_value(when)
        self.close()


class WhenPicker(QWidget):
    """Shows "Now" until you pick an earlier day and time for the play."""

    changed = pyqtSignal()

    def __init__(self, con):
        super().__init__()
        self.con = con
        self.value = None  # None means now
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.btn = QPushButton()
        self.btn.setObjectName("whenBtn")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setToolTip("Logging a play from earlier? Pick the day and time here.")
        self.btn.clicked.connect(self.open_popup)
        h.addWidget(self.btn, 1)
        self.now_btn = button("Use now", self.set_now, "ghost")
        h.addWidget(self.now_btn)
        self.popup = None
        self._render()

    def when(self):
        return self.value or core.now_local()

    def is_now(self):
        return self.value is None

    def set_now(self):
        self.value = None
        self._render()
        self.changed.emit()

    def set_value(self, when):
        self.value = when.replace(second=0, microsecond=0)
        self._render()
        self.changed.emit()

    def _render(self):
        self.btn.setIcon(calendar_icon(C["accent"] if self.value else C["muted"]))
        self.btn.setText("  Now" if self.value is None else "  " + friendly_when(self.value))
        self.btn.setProperty("past", self.value is not None)
        self.btn.style().unpolish(self.btn)
        self.btn.style().polish(self.btn)
        self.now_btn.setVisible(self.value is not None)

    def changeEvent(self, event):
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self.btn.setIcon(calendar_icon(C["accent"] if self.value else C["muted"]))
        super().changeEvent(event)

    def open_popup(self):
        self.popup = WhenPopup(self, core.play_dates(self.con))
        self.popup.adjustSize()
        pos = self.btn.mapToGlobal(QPoint(0, self.btn.height() + 4))
        screen = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        size = self.popup.sizeHint()
        x = max(screen.left(), min(pos.x(), screen.right() - size.width()))
        y = pos.y() if pos.y() + size.height() <= screen.bottom() else \
            self.btn.mapToGlobal(QPoint(0, 0)).y() - size.height() - 4
        self.popup.move(x, max(screen.top(), y))
        self.popup.show()
        self.popup.cal.setFocus()


# ---------------------------------------------------------------------------
# Charts for the Stats tab (drawn with QPainter, coloured from the theme)
# ---------------------------------------------------------------------------

def nice_ticks(top, count=4):
    """Round numbers from 0 up to at least top, about count steps."""
    if top <= 0:
        return [0, 1]
    raw = top / float(count)
    mag = 10 ** math.floor(math.log10(raw))
    step = mag * 10
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            step = m * mag
            break
    return [i * step for i in range(int(math.ceil(top / step - 1e-9)) + 1)]


def mix(a, b, t):
    """A colour t of the way from a to b."""
    a, b = QColor(a), QColor(b)
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def faded(colour, alpha):
    c = QColor(colour)
    c.setAlpha(alpha)
    return c


def measure_value(measure, sides, seconds):
    return sides if measure == "sides" else seconds / 3600.0


def fmt_measure(measure, value, short=False):
    if measure == "sides":
        n = int(round(value))
        return str(n) if short else "{} side{}".format(n, "" if n == 1 else "s")
    if short:
        return "{:g} h".format(round(value, 1)) if value < 100 else "{:,.0f} h".format(value)
    return "{:.1f} hours".format(value) if value < 100 else "{:,.0f} hours".format(value)


PERIOD_NAMES = {"day": "day", "week": "week", "month": "month"}


def period_label(start, unit, long=False):
    if unit == "month":
        return start.strftime("%B %Y" if long else "%b %Y")
    if unit == "week":
        return ("Week of " if long else "") + start.strftime("%d %b %Y" if long else "%d %b").lstrip("0")
    return start.strftime("%A %d %b %Y" if long else "%d %b").replace(" 0", " ").lstrip("0")


def date_ticks(start, end, width):
    """Calendar-aligned dates to label along a time axis, spaced at least ~90 pixels apart."""
    days = max(1, (end.date() - start.date()).days)
    room = max(2, int(width / 92))
    for step in (1, 2, 7, 14):
        if days / step <= room:
            first = start.date()
            if step == 7:
                first += dt.timedelta(days=(7 - first.weekday()) % 7)  # Mondays
            out, d = [], first
            while d <= end.date():
                out.append((d, d.strftime("%d %b").lstrip("0")))
                d += dt.timedelta(days=step)
            return out
    months = days / 30.4
    for step in (1, 2, 3, 6, 12, 24, 60):
        if months / step <= room:
            break
    out = []
    d = (start.date().replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    while d <= end.date():
        if (d.year * 12 + d.month - 1) % step == 0:
            out.append((d, d.strftime("%Y") if step >= 12 else d.strftime("%b %Y")))
        d = (d + dt.timedelta(days=32)).replace(day=1)
    return out


class ChartBase(QWidget):
    LEFT, RIGHT, TOP, BOTTOM = 52, 14, 18, 24

    def __init__(self, min_height=110, preferred=240):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumHeight(min_height)  # charts grow and shrink with the window, down to this
        self.preferred = preferred
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.hover = None
        self.measure = "sides"
        self.empty = "No plays logged with this cartridge / stylus yet."

    def sizeHint(self):
        # Ask only for the minimum: the page then fits the window whenever it can, and the spare
        # height is shared between the charts (in proportion to self.preferred) by the layout.
        return QSize(600, self.minimumHeight())

    def small_font(self, bold=False):
        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() * 0.86))
        f.setBold(bold)
        return f

    def plot_rect(self):
        return QRectF(self.LEFT, self.TOP, max(10, self.width() - self.LEFT - self.RIGHT),
                      max(10, self.height() - self.TOP - self.BOTTOM))

    def tick_count(self):
        """Fewer gridlines on a short chart, so the axis labels never run into each other."""
        return max(1, min(4, int(self.plot_rect().height() // 24)))

    def leaveEvent(self, event):
        self.hover = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def draw_y_axis(self, p, r, ticks, top):
        p.setFont(self.small_font())
        for v in ticks:
            y = r.bottom() - r.height() * v / top
            p.setPen(QPen(faded(C["border"], 255 if v == 0 else 150), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(0, y - 9, self.LEFT - 10, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       fmt_measure(self.measure, v, short=True))

    def draw_empty(self, p):
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, self.empty)

    def show_tip(self, event, text):
        QToolTip.showText(event.globalPosition().toPoint(), text, self)


class CumulativeChart(ChartBase):
    """Sides (or hours) played so far, over the whole life of the cartridge / stylus."""

    def __init__(self):
        super().__init__(95, 260)
        self.t = None

    def set_data(self, t, measure):
        self.t, self.measure, self.hover = t, measure, None
        self.update()

    def _series(self):
        t = self.t
        return [(w, measure_value(self.measure, s, sec)) for w, s, sec in t["points"]]

    def _limits(self):
        """(y top, threshold lines to draw) for the current data."""
        t = self.t
        top = max(v for _, v in self._series())
        lines = []
        if self.measure == "hours" and t["rated"]:
            rated = t["rated"] / 3600.0
            warn = rated * t["warn_pct"] / 100.0
            if warn <= top * 1.6:  # only when it's close enough not to flatten the line
                lines.append((warn, "{:g}% of rated life".format(t["warn_pct"]), C["warn"]))
            if rated <= top * 1.6:
                lines.append((rated, "Rated life, {:g} h".format(rated), C["over"]))
        top = max([top] + [v for v, _, _ in lines])
        ticks = nice_ticks(top * 1.05 if top else 1, self.tick_count())
        return ticks, lines

    def _x(self, r, when):
        t = self.t
        span = max(1.0, (t["end"] - t["start"]).total_seconds())
        return r.left() + r.width() * (when - t["start"]).total_seconds() / span

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.t:
            self.draw_empty(p)
            return
        r = self.plot_rect()
        ticks, lines = self._limits()
        top = ticks[-1]
        self.draw_y_axis(p, r, ticks, top)
        p.setFont(self.small_font())
        p.setPen(QColor(C["muted"]))
        for d, text in date_ticks(self.t["start"], self.t["end"], r.width()):
            x = self._x(r, dt.datetime.combine(d, dt.time()).astimezone())
            if r.left() - 1 <= x <= r.right() + 1:
                p.drawText(QRectF(x - 46, r.bottom() + 4, 92, 18), Qt.AlignmentFlag.AlignHCenter, text)
        series = self._series()
        y = lambda v: r.bottom() - r.height() * v / top
        path = QPainterPath(QPointF(r.left(), y(series[0][1])))
        last = series[0][1]
        for when, v in series[1:]:
            x = self._x(r, when)
            path.lineTo(QPointF(x, y(last)))
            path.lineTo(QPointF(x, y(v)))
            last = v
        path.lineTo(QPointF(r.right(), y(last)))
        area = QPainterPath(path)
        area.lineTo(QPointF(r.right(), r.bottom()))
        area.lineTo(QPointF(r.left(), r.bottom()))
        area.closeSubpath()
        p.fillPath(area, faded(C["accent"], 38))
        pen = QPen(QColor(C["accent"]), 2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        for v, text, colour in lines:
            pen = QPen(QColor(colour), 1.5, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(r.left(), y(v)), QPointF(r.right(), y(v)))
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(r.left() + 6, y(v) - 18, r.width() - 12, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, text)
        if self.hover is not None:
            x, when, v = self.hover
            p.setPen(QPen(faded(C["muted"], 160), 1))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(QPen(QColor(C["surface"]), 2))
            p.setBrush(QColor(C["accent"]))
            p.drawEllipse(QPointF(x, y(v)), 5, 5)
        p.end()

    def mouseMoveEvent(self, event):
        if not self.t:
            return
        r = self.plot_rect()
        x = min(max(event.position().x(), r.left()), r.right())
        span = (self.t["end"] - self.t["start"]).total_seconds()
        when = self.t["start"] + dt.timedelta(seconds=span * (x - r.left()) / max(1.0, r.width()))
        value = 0
        for w, v in self._series():
            if w <= when:
                value = v
            else:
                break
        self.hover = (x, when, value)
        self.update()
        self.show_tip(event, "{}\n{} so far".format(when.strftime("%a %d %b %Y").replace(" 0", " "),
                                                     fmt_measure(self.measure, value)))


class PeriodChart(ChartBase):
    """Sides (or hours) played each day, week or month: peaks stand out and quiet spells show as gaps."""

    def __init__(self):
        super().__init__(90, 240)
        self.t = None

    def set_data(self, t, measure):
        self.t, self.measure, self.hover = t, measure, None
        self.update()

    def _values(self):
        return [measure_value(self.measure, b["sides"], b["seconds"]) for b in self.t["buckets"]]

    def _slot(self, r):
        return r.width() / max(1, len(self.t["buckets"]))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.t or not self.t["buckets"]:
            self.draw_empty(p)
            return
        r = self.plot_rect()
        values = self._values()
        peak = max(values)
        ticks = nice_ticks(peak * 1.12 if peak else 1, self.tick_count())
        top = ticks[-1]
        self.draw_y_axis(p, r, ticks, top)
        slot = self._slot(r)
        gap = 2 if slot >= 6 else (1 if slot >= 3 else 0)
        radius = 4 if slot - gap >= 8 else (2 if slot - gap >= 4 else 0)
        peak_i = values.index(peak) if peak else None
        if self.hover is not None:
            p.fillRect(QRectF(r.left() + self.hover * slot, r.top(), slot, r.height()), faded(C["accent"], 22))
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(values):
            if v <= 0:
                continue
            h = max(2.0, r.height() * v / top)
            bar = QRectF(r.left() + i * slot + gap / 2.0, r.bottom() - h, max(1.0, slot - gap), h)
            p.setBrush(QColor(C["accent_hover"] if i == self.hover else C["accent"]))
            if radius and bar.height() > radius:  # rounded at the top, square on the baseline
                p.drawRoundedRect(bar, radius, radius)
                p.drawRect(bar.adjusted(0, radius, 0, 0))
            else:
                p.drawRect(bar)
        p.setFont(self.small_font())
        p.setPen(QColor(C["muted"]))
        unit = self.t["unit"]
        every = max(1, int(math.ceil(78 / max(1.0, slot))))
        if unit == "month" and every > 1:
            every = next(n for n in (1, 2, 3, 6, 12, 24, 60) if n >= every)
        for i, b in enumerate(self.t["buckets"]):
            if unit == "month":
                show = (b["start"].month - 1) % every == 0 if every in (2, 3, 6, 12) else i % every == 0
                text = b["start"].strftime("%Y") if every >= 12 else b["start"].strftime("%b %Y")
            else:
                show = i % every == 0
                text = period_label(b["start"], unit)
            if show:
                x = r.left() + (i + 0.5) * slot
                p.drawText(QRectF(x - 46, r.bottom() + 4, 92, 18), Qt.AlignmentFlag.AlignHCenter, text)
        if peak_i is not None:  # label the busiest period only
            x = r.left() + (peak_i + 0.5) * slot
            ytop = r.bottom() - r.height() * peak / top
            p.setFont(self.small_font(bold=True))
            p.setPen(QColor(C["text"]))
            text = "Peak: " + fmt_measure(self.measure, peak, short=True)
            w = QFontMetrics(p.font()).horizontalAdvance(text) + 8
            lx = min(max(x - w / 2, r.left()), r.right() - w)
            p.drawText(QRectF(lx, ytop - 18, w, 16), Qt.AlignmentFlag.AlignCenter, text)
        p.end()

    def mouseMoveEvent(self, event):
        if not self.t or not self.t["buckets"]:
            return
        r = self.plot_rect()
        i = int((event.position().x() - r.left()) / self._slot(r))
        if not 0 <= i < len(self.t["buckets"]) or not r.top() - 4 <= event.position().y() <= r.bottom() + 4:
            if self.hover is not None:
                self.hover = None
                self.update()
                QToolTip.hideText()
            return
        if i != self.hover:
            self.hover = i
            self.update()
        b = self.t["buckets"][i]
        if b["sides"]:
            text = "{}\n{} ({})".format(period_label(b["start"], self.t["unit"], long=True),
                                        fmt_measure("sides", b["sides"]), core.fmt_dur(b["seconds"]))
        else:
            text = "{}\nNothing played".format(period_label(b["start"], self.t["unit"], long=True))
        self.show_tip(event, text)


class HeatmapChart(ChartBase):
    """When in the week you play: a row for each day, a column for each hour."""

    LEFT, RIGHT, TOP, BOTTOM = 44, 44, 20, 26
    MIN_ROW, MAX_ROW = 13, 34

    def __init__(self):
        super().__init__(self.TOP + 7 * self.MIN_ROW + self.BOTTOM, self.TOP + 7 * 26 + self.BOTTOM)
        self.setMaximumHeight(self.TOP + 7 * self.MAX_ROW + self.BOTTOM)
        self.grid = None

    @property
    def ROW(self):
        """Row height: the heatmap grows and shrinks with the space it's given."""
        return max(self.MIN_ROW, min(self.MAX_ROW, (self.height() - self.TOP - self.BOTTOM) / 7.0))

    def set_data(self, grid, measure):
        self.grid, self.measure, self.hover = grid, measure, None
        self.update()

    def _value(self, d, h):
        c = self.grid[d][h]
        return measure_value(self.measure, c[0], c[1])

    def _cell(self):
        return (self.width() - self.LEFT - self.RIGHT) / 24.0

    def colour(self, v, top):
        if v <= 0:
            return QColor(C["surface2"])
        return mix(C["surface3"], C["accent"], 0.22 + 0.78 * v / top)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.grid:
            self.draw_empty(p)
            return
        top = max(self._value(d, h) for d in range(7) for h in range(24))
        cw = self._cell()
        p.setFont(self.small_font())
        p.setPen(QColor(C["muted"]))
        for h in range(0, 24, 3):
            p.drawText(QRectF(self.LEFT + h * cw - 4, 0, 40, self.TOP - 4),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, "{:02d}:00".format(h))
        for d in range(7):
            y = self.TOP + d * self.ROW
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(0, y, self.LEFT - 8, self.ROW - 2), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       core.WEEKDAYS[d][:3])
            total = sum(self._value(d, h) for h in range(24))
            p.drawText(QRectF(self.width() - self.RIGHT + 6, y, self.RIGHT - 6, self.ROW - 2),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fmt_measure(self.measure, total, short=True) if total else "")
            p.setPen(Qt.PenStyle.NoPen)
            for h in range(24):
                v = self._value(d, h)
                cell = QRectF(self.LEFT + h * cw + 1, y + 1, cw - 2, self.ROW - (4 if self.ROW > 18 else 2))
                p.setBrush(self.colour(v, top or 1))
                p.drawRoundedRect(cell, 4, 4)
                if self.hover == (d, h):
                    p.setPen(QPen(QColor(C["text"]), 1.5))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(cell.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
                    p.setPen(Qt.PenStyle.NoPen)
        # key: fewer to more
        y = self.TOP + 7 * self.ROW + 5
        p.setPen(QColor(C["muted"]))
        fm = QFontMetrics(p.font())
        x = self.width() - self.RIGHT
        steps = [0, 0.25, 0.5, 0.75, 1.0]
        more_w = fm.horizontalAdvance("More")
        x -= more_w
        p.drawText(QRectF(x, y, more_w, 16), Qt.AlignmentFlag.AlignVCenter, "More")
        x -= 6 + 5 * 16
        sx = x
        p.setPen(Qt.PenStyle.NoPen)
        for s in steps:
            p.setBrush(self.colour(s * (top or 1), top or 1) if s else QColor(C["surface2"]))
            p.drawRoundedRect(QRectF(sx, y + 2, 13, 13), 3, 3)
            sx += 16
        fewer_w = fm.horizontalAdvance("None")
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(x - fewer_w - 6, y, fewer_w, 16), Qt.AlignmentFlag.AlignVCenter, "None")
        p.end()

    def mouseMoveEvent(self, event):
        if not self.grid:
            return
        cw = self._cell()
        h = int((event.position().x() - self.LEFT) / cw) if event.position().x() >= self.LEFT else -1
        d = int((event.position().y() - self.TOP) / self.ROW) if event.position().y() >= self.TOP else -1
        if not (0 <= h < 24 and 0 <= d < 7):
            if self.hover is not None:
                self.hover = None
                self.update()
                QToolTip.hideText()
            return
        if (d, h) != self.hover:
            self.hover = (d, h)
            self.update()
        sides, secs = self.grid[d][h]
        text = "{}s, {:02d}:00 to {:02d}:00\n{}".format(
            core.WEEKDAYS[d], h, (h + 1) % 24,
            "{} ({})".format(fmt_measure("sides", sides), core.fmt_dur(secs)) if sides else "Nothing played")
        self.show_tip(event, text)


# ---------------------------------------------------------------------------
# Stats tab: chart cards you can drag (or nudge with the arrows) into any order
# ---------------------------------------------------------------------------

CARD_MIME = "application/x-styli-chart"


class DragGrip(QWidget):
    """The dotted handle a chart card is dragged by."""

    def __init__(self, card):
        super().__init__()
        self.card, self.press, self.hover = card, None, False
        self.setFixedSize(16, 22)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to move this chart")

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["accent"] if self.hover else C["faint"]))
        for col in (5, 11):
            for row in (5, 11, 17):
                p.drawEllipse(QPointF(col, row), 1.8, 1.8)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self.press is not None and \
                (event.position().toPoint() - self.press).manhattanLength() >= QApplication.startDragDistance():
            self.press = None
            self.card.box.start_drag(self.card)

    def mouseReleaseEvent(self, event):
        self.press = None


class ElidedLabel(QLabel):
    """One line of text that shortens itself with "..." instead of wrapping (so it never changes
    the height of the card it's in), with the whole text in its tooltip."""

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self):
        return QSize(20, super().minimumSizeHint().height())

    def sizeHint(self):
        return QSize(20, super().sizeHint().height())

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        p.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width()))
        p.end()


class ChartCard(QFrame):
    def __init__(self, key, chart, box):
        super().__init__()
        self.setObjectName("card")
        self.key, self.chart, self.box = key, chart, box
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 7, 16, 8)
        v.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.grip = DragGrip(self)
        head.addWidget(self.grip)
        self.cap = label("", "caption")
        head.addWidget(self.cap)
        head.addStretch(1)
        self.up = button("", lambda: box.step(self, -1), "ghost")
        self.down = button("", lambda: box.step(self, 1), "ghost")
        self.up.setToolTip("Move this chart up")
        self.down.setToolTip("Move this chart down")
        for b in (self.up, self.down):
            b.setFixedSize(28, 24)
            head.addWidget(b)
        v.addLayout(head)
        self.sub = ElidedLabel("")
        self.sub.setObjectName("muted")
        self.sub.setContentsMargins(24, 0, 0, 2)
        v.addWidget(self.sub)
        v.addWidget(chart, 1)

    def set_sub(self, text, show):
        """The one-line explanation: under the title when there's room, otherwise in its tooltip."""
        self.sub.setText(text)
        self.sub.setVisible(show and bool(text))
        self.cap.setToolTip(text)


class ReorderBox(QWidget):
    """Holds the chart cards in a column; drop a card between two others to move it there."""

    moved = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(10)
        self.cards = []
        self.marker = QFrame(self)
        self.marker.setObjectName("dropMarker")
        self.marker.setFixedHeight(4)
        self.marker.hide()
        self.dragging = None

    def add(self, card):
        self.cards.append(card)
        self.lay.addWidget(card, card.chart.preferred)
        self._refresh_buttons()

    def order(self):
        return [c.key for c in self.cards]

    def set_order(self, keys):
        rank = {k: i for i, k in enumerate(keys)}
        self.cards.sort(key=lambda c: rank.get(c.key, len(rank)))
        self._relayout()

    def _relayout(self):
        for c in self.cards:
            self.lay.removeWidget(c)
        for c in self.cards:
            self.lay.addWidget(c, c.chart.preferred)  # extra height is shared in proportion
        self._refresh_buttons()

    def _refresh_buttons(self):
        up, down = QIcon(arrow_image(C["muted"], up=True)), QIcon(arrow_image(C["muted"]))
        for i, c in enumerate(self.cards):
            c.up.setIcon(up)
            c.down.setIcon(down)
            c.up.setEnabled(i > 0)
            c.down.setEnabled(i < len(self.cards) - 1)

    def move_card(self, card, index):
        old = self.cards.index(card)
        index = max(0, min(len(self.cards) - 1, index))
        if index == old:
            return
        self.cards.insert(index, self.cards.pop(old))
        self._relayout()
        self.moved.emit(self.order())

    def step(self, card, delta):
        self.move_card(card, self.cards.index(card) + delta)

    def start_drag(self, card):
        drag = QDrag(card)
        mime = QMimeData()
        mime.setData(CARD_MIME, card.key.encode("utf-8"))
        drag.setMimeData(mime)
        shot = card.grab()
        scale = min(1.0, 420.0 / max(1, shot.width()))
        shot = shot.scaledToWidth(int(shot.width() * scale), Qt.TransformationMode.SmoothTransformation)
        ghost = QPixmap(shot.size())
        ghost.fill(Qt.GlobalColor.transparent)
        p = QPainter(ghost)
        p.setOpacity(0.85)
        p.drawPixmap(0, 0, shot)
        p.end()
        drag.setPixmap(ghost)
        drag.setHotSpot(QPoint(int(20 * scale), int(18 * scale)))
        self.dragging = card
        restyle(card, dragging=True)
        drag.exec(Qt.DropAction.MoveAction)
        self.dragging = None
        restyle(card, dragging=False)
        self.marker.hide()

    def drop_index(self, y):
        """Where a card dropped at height y goes (0 = top), counting the card being moved."""
        for i, c in enumerate(self.cards):
            g = c.geometry()
            if y < g.top() + g.height() / 2:
                return i
        return len(self.cards)

    def _show_marker(self, index):
        if index < len(self.cards):
            y = self.cards[index].geometry().top() - 9
        else:
            y = self.cards[-1].geometry().bottom() + 5
        self.marker.setGeometry(0, max(0, y), self.width(), 4)
        self.marker.raise_()
        self.marker.show()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(CARD_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(CARD_MIME):
            self._show_marker(self.drop_index(event.position().y()))
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.marker.hide()

    def dropEvent(self, event):
        self.marker.hide()
        key = bytes(event.mimeData().data(CARD_MIME)).decode("utf-8")
        card = next((c for c in self.cards if c.key == key), None)
        if card is None:
            return
        index = self.drop_index(event.position().y())
        if index > self.cards.index(card):
            index -= 1  # the card leaves its old place first
        self.move_card(card, index)
        event.acceptProposedAction()


# ---------------------------------------------------------------------------
# Top 10 tab: a grid of covers that flip over to show each album's stats
# ---------------------------------------------------------------------------

class AlbumTile(QWidget):
    """One album in the Top 10. Click to turn the cover over and see its stats; click again to turn it back."""

    clicked = pyqtSignal(object)
    CAPTION = 50

    def __init__(self, store, rank, item, stats, key, period):
        super().__init__()
        self.store, self.rank, self.item, self.stats, self.key, self.period = store, rank, item, stats, key, period
        self.flipped, self.p, self.hover = False, 0.0, False
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(340)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.anim.valueChanged.connect(self._set_p)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        rec = item["record"]
        self.setToolTip("{} - {}\nClick to see its stats".format(rec["artist"], rec["title"]))
        self.setAccessibleName("Number {}: {} by {}".format(rank, rec["title"], rec["artist"]))
        store.changed.connect(self.update)

    def _set_p(self, v):
        self.p = float(v)
        self.update()

    def set_flipped(self, on, animate=True):
        if on == self.flipped:
            return
        self.flipped = on
        rec = self.item["record"]
        self.setToolTip("{} - {}\n{}".format(rec["artist"], rec["title"],
                                             "Click to see the cover again" if on else "Click to see its stats"))
        self.anim.stop()
        if animate:
            self.anim.setStartValue(self.p)
            self.anim.setEndValue(1.0 if on else 0.0)
            self.anim.start()
        else:
            self._set_p(1.0 if on else 0.0)

    def enterEvent(self, event):
        self.hover = True
        self.update()

    def leaveEvent(self, event):
        self.hover = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit(self)
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.update()
        super().focusOutEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        side = float(self.width())
        art = QRectF(0, 0, side, side)
        squash = abs(math.cos(self.p * math.pi))  # the card turning edge-on halfway through
        p.save()
        p.translate(side / 2, 0)
        p.scale(max(0.02, squash), 1)
        p.translate(-side / 2, 0)
        if self.p > 0.5:
            self.paint_back(p, art)
        else:
            self.paint_front(p, art)
        p.restore()
        if (self.hover or self.hasFocus()) and self.p in (0.0, 1.0):
            p.setPen(QPen(QColor(C["accent"]), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(art.adjusted(1, 1, -1, -1), 12, 12)
        rec = self.item["record"]
        f = QFont(self.font())
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        fm = QFontMetrics(f)
        p.drawText(QRectF(2, side + 8, side - 4, fm.height()), Qt.AlignmentFlag.AlignLeft,
                   fm.elidedText(rec["title"], Qt.TextElideMode.ElideRight, int(side - 4)))
        f2 = QFont(self.font())
        p.setFont(f2)
        p.setPen(QColor(C["muted"]))
        fm2 = QFontMetrics(f2)
        p.drawText(QRectF(2, side + 10 + fm.height(), side - 4, fm2.height()), Qt.AlignmentFlag.AlignLeft,
                   fm2.elidedText(rec["artist"], Qt.TextElideMode.ElideRight, int(side - 4)))
        p.end()

    def paint_front(self, p, r):
        rec = self.item["record"]
        draw_cover(p, r, self.store.get(self.key), rec["title"], 12, "vinyl", False)
        d = max(26.0, min(44.0, r.width() * 0.15))
        badge = QRectF(r.left() + 10, r.top() + 10, d, d)
        p.setPen(QPen(QColor(C["surface"]), 2))
        p.setBrush(QColor(C["accent"]))
        p.drawEllipse(badge)
        f = QFont(self.font())
        f.setBold(True)
        f.setPixelSize(int(d * 0.48))
        p.setFont(f)
        p.setPen(QColor(C["on_accent"]))
        p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(self.rank))

    def paint_back(self, p, r):
        s, rec = self.stats, self.item["record"]
        path = QPainterPath()
        path.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        p.fillPath(path, QColor(C["surface3"]))
        p.setPen(QPen(QColor(C["border"]), 1))
        p.drawPath(path)
        side = r.width()
        pad = max(10.0, side * 0.07)
        x, w = r.left() + pad, r.width() - 2 * pad
        px = max(10, min(15, int(side / 17)))
        small, body, bold = QFont(self.font()), QFont(self.font()), QFont(self.font())
        small.setPixelSize(max(9, px - 2))
        small.setBold(True)
        body.setPixelSize(px)
        bold.setPixelSize(px + 1)
        bold.setBold(True)
        y = r.top() + pad
        p.setFont(small)
        p.setPen(QColor(C["accent"]))
        fs = QFontMetrics(small)
        p.drawText(QRectF(x, y, w, fs.height()), Qt.AlignmentFlag.AlignLeft,
                   fs.elidedText("#{}  ·  {}".format(self.rank, self.period.upper()), Qt.TextElideMode.ElideRight, int(w)))
        y += fs.height() + 4
        p.setFont(bold)
        p.setPen(QColor(C["text"]))
        fb = QFontMetrics(bold)
        p.drawText(QRectF(x, y, w, fb.height()), Qt.AlignmentFlag.AlignLeft,
                   fb.elidedText(rec["title"], Qt.TextElideMode.ElideRight, int(w)))
        y += fb.height() + max(6, int(side * 0.035))  # the artist is shown under the tile already
        p.setFont(body)
        fm = QFontMetrics(body)
        date = lambda d: d.strftime("%d %b %Y").lstrip("0") if d else "-"
        rows = [("Album plays", str(s["album"])), ("Sides played", str(s["sides"])),
                ("Time listened", core.fmt_hours(s["seconds"])), ("Last played", date(s["last"]))]
        if s["favourite_time"]:
            rows.append(("Usually", "{} {}s".format(s["favourite_time"][0][:3], s["favourite_time"][1])))
        if s["favourite_side"]:
            rows.append(("Favourite side", "{} ({} plays)".format(*s["favourite_side"])))
        rows.append(("First played", date(s["first"])))
        spark_h = max(16.0, side * 0.09)
        line = fm.height() + max(2, int(px * 0.3))
        spark_room = spark_h + fs.height() + 12
        # the mini chart only if it fits below at least five rows
        spark = any(n for _, n in s["months"]) and y + 5 * line + spark_room <= r.bottom() - pad
        bottom = r.bottom() - pad - (spark_room if spark else 0)
        for name, value in rows:
            if y + fm.height() > bottom:
                break
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(x, y, w, fm.height()), Qt.AlignmentFlag.AlignLeft, name)
            p.setPen(QColor(C["text"]))
            vw = w - fm.horizontalAdvance(name) - 8
            p.drawText(QRectF(x, y, w, fm.height()), Qt.AlignmentFlag.AlignRight,
                       fm.elidedText(value, Qt.TextElideMode.ElideRight, int(max(10, vw))))
            y += line
        if spark:  # times it went on the turntable, each of the last 12 months
            top = max(n for _, n in s["months"])
            base = r.bottom() - pad
            p.setFont(small)
            p.setPen(QColor(C["faint"]))
            p.drawText(QRectF(x, base - spark_h - fs.height() - 4, w, fs.height()), Qt.AlignmentFlag.AlignLeft,
                       "PLAYS, LAST 12 MONTHS")
            slot = w / 12.0
            p.setPen(Qt.PenStyle.NoPen)
            for i, (_, n) in enumerate(s["months"]):
                bx = x + i * slot + 1
                if n:
                    h = max(3.0, spark_h * n / top)
                    p.setBrush(QColor(C["accent"]))
                    p.drawRoundedRect(QRectF(bx, base - h, slot - 2, h), 2, 2)
                else:
                    p.setBrush(QColor(C["border"]))
                    p.drawRect(QRectF(bx, base - 1, slot - 2, 1))


class TopGrid(QWidget):
    """Lays the Top 10 out up to five across, wrapping onto more rows as the window narrows."""

    MAX_COLS, MIN_TILE, GAP = 5, 200, 18

    def __init__(self):
        super().__init__()
        self.tiles, self.open = [], None
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.timeout.connect(self._fit_height)

    def set_tiles(self, tiles):
        for t in self.tiles:
            t.hide()
            t.setParent(None)
            t.deleteLater()
        self.tiles, self.open = tiles, None
        for t in tiles:
            t.setParent(self)
            t.clicked.connect(self.toggle)
            t.show()
        self.relayout()

    def toggle(self, tile):
        """Turn this album over; any other album showing its stats turns back to its cover."""
        if self.open is tile:
            tile.set_flipped(False)
            self.open = None
            return
        if self.open is not None:
            self.open.set_flipped(False)
        tile.set_flipped(True)
        self.open = tile

    def columns(self, width=None):
        width = self.width() if width is None else width
        return max(1, min(self.MAX_COLS, int((width + self.GAP) // (self.MIN_TILE + self.GAP))))

    def _side(self, width, n):
        return max(40, int((width - self.GAP * (n - 1)) / n))

    def heightForWidth(self, width):
        if not self.tiles:
            return 0
        n = self.columns(width)
        rows = (len(self.tiles) + n - 1) // n
        return rows * (self._side(width, n) + AlbumTile.CAPTION + self.GAP) - self.GAP

    def sizeHint(self):
        return QSize(900, self.heightForWidth(900))

    def minimumSizeHint(self):
        return QSize(self.MIN_TILE, 0)

    def relayout(self):
        """Place the tiles for the current width. The grid's own height is fitted afterwards."""
        if not self.tiles:
            self._height_timer.start(0)
            return
        n = self.columns()
        side = self._side(self.width(), n)
        step = side + AlbumTile.CAPTION + self.GAP
        for i, t in enumerate(self.tiles):
            row, col = divmod(i, n)
            t.setGeometry(col * (side + self.GAP), row * step, side, side + AlbumTile.CAPTION)
        self._height_timer.start(0)

    def _fit_height(self):
        """Grow or shrink to fit every row. Done just after a resize, never during one: changing the
        height inside a resize starts another resize, and that loop is what used to crash the app."""
        h = self.heightForWidth(self.width())
        if self.minimumHeight() != h or self.maximumHeight() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.relayout()


class SidesEditor(QDialog):
    """Check or correct a record's details and side lengths. Result in .result_data."""

    def __init__(self, parent, title, artist, album, year, sides, note="", note_style="muted"):
        super().__init__(parent)
        v = dialog_layout(self, title, 560)
        self.result_data = None
        v.addWidget(label(title, "h2"))
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.artist, self.album, self.year = QLineEdit(artist), QLineEdit(album), QLineEdit(year)
        self.year.setMaximumWidth(90)
        frow(form, "Artist", self.artist)
        frow(form, "Album", self.album)
        frow(form, "Year", self.year)
        v.addLayout(form)
        if note:
            v.addWidget(label(note, note_style, wrap=True))
        self.table = table(["Side", "Length (mm:ss)", "Tracks"], 2)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        self.table.setMinimumHeight(200)
        v.addWidget(self.table, 1)
        for s in sides:
            self.add_row(s["label"], core.fmt_dur(s["seconds"]) if s["seconds"] else "",
                         s.get("tracks", 0), s.get("missing", 0))
        if not sides:
            self.add_row("A")
            self.add_row("B")
        row = QHBoxLayout()
        row.addWidget(button("Add side", self.add_next))
        row.addWidget(button("Remove side", self.remove_selected))
        row.addStretch(1)
        row.addWidget(button("Cancel", self.reject))
        save = button("Save", self.save, "primary")
        save.setDefault(True)
        row.addWidget(save)
        v.addLayout(row)

    def add_row(self, side="", length="", tracks=0, missing=0):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, cell(side, {"orig": length, "tracks": tracks, "missing": missing}))
        self.table.setItem(r, 1, cell(length))
        info = "{} track{}".format(tracks, "" if tracks == 1 else "s") if tracks else ""
        if missing:
            info += ", {} without a length".format(missing)
        it = cell(info)
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        it.setForeground(QColor(C["warn"] if missing else C["muted"]))
        self.table.setItem(r, 2, it)
        return r

    def add_next(self):
        used = {(self.table.item(r, 0).text() or "").strip().upper() for r in range(self.table.rowCount())}
        nxt = next((core.side_letter(i) for i in range(40) if core.side_letter(i) not in used), "")
        r = self.add_row(nxt)
        self.table.setCurrentCell(r, 1)
        self.table.editItem(self.table.item(r, 1))

    def remove_selected(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def save(self):
        out, seen = [], set()
        try:
            for r in range(self.table.rowCount()):
                meta = self.table.item(r, 0).data(ROLE) or {}
                lab = (self.table.item(r, 0).text() or "").strip().upper()
                text = (self.table.item(r, 1).text() or "").strip()
                if not lab:
                    raise ValueError("Every side needs a label such as A or B.")
                if lab in seen:
                    raise ValueError("Side {} appears twice.".format(lab))
                seen.add(lab)
                try:
                    secs = core.parse_duration(text)
                except ValueError:
                    raise ValueError("Side {}: enter the length as mm:ss, for example 21:34.".format(lab))
                if secs <= 0:
                    raise ValueError("Side {} needs a length.".format(lab))
                out.append({"label": lab, "seconds": secs, "tracks": meta.get("tracks", 0),
                            "missing": meta.get("missing", 0) if text == meta.get("orig") else 0})
            if not out:
                raise ValueError("Add at least one side.")
            artist, album = self.artist.text().strip(), self.album.text().strip()
            if not artist or not album:
                raise ValueError("Artist and album are needed.")
        except ValueError as e:
            QMessageBox.warning(self, "Check the details", str(e))
            return
        self.result_data = (out, artist, album, self.year.text().strip())
        self.accept()


class AddDialog(QDialog):
    """Find the album, pick your pressing (with cover previews), then confirm the sides."""

    def __init__(self, main):
        super().__init__(main)
        self.main, self.con, self.covers = main, main.con, main.covers
        self.tasks, self.closed, self.busy, self.saved_id = set(), False, False, None
        self.albums, self.pressings, self.shown, self.album, self.press_total = [], [], [], None, 0
        self.mode, self.press_gen, self.loading_more = "albums", 0, False
        self.track_cache, self.track_loading, self.rel_cache, self.track_key = {}, set(), {}, None
        self.mb_mode, self.dg_items, self.dg_shown, self.dg_gen, self.dg_new = "albums", [], [], 0, []
        v = dialog_layout(self, "Add a record")
        self.resize(1160, 740)
        head = QHBoxLayout()
        head.addWidget(label("Add a record", "h2"))
        head.addStretch(1)
        self.seg = QButtonGroup(self)
        for key, text, pos in (("mb", "Search MusicBrainz", "first"), ("discogs", "My Discogs collection", "last")):
            b = button(text, None, "seg")
            b.setCheckable(True)
            b.setProperty("pos", pos)
            b.setProperty("key", key)
            b.setChecked(key == "mb")
            self.seg.addButton(b)
            head.addWidget(b)
        self.seg.buttonClicked.connect(lambda b: self.set_source(b.property("key")))
        v.addLayout(head)

        self.dg_bar = QWidget()
        dgl = QHBoxLayout(self.dg_bar)
        dgl.setContentsMargins(0, 0, 0, 0)
        dgl.setSpacing(10)
        self.dg_filter = QLineEdit()
        self.dg_filter.setPlaceholderText("Filter your collection: artist, album, label or catalogue number")
        self.dg_filter.setClearButtonEnabled(True)
        self.dg_filter.textChanged.connect(lambda t: self.fill_discogs())
        dgl.addWidget(self.dg_filter, 1)
        self.dg_vinyl = QCheckBox("Vinyl only")
        self.dg_vinyl.setChecked(True)
        self.dg_vinyl.toggled.connect(lambda on: self.fill_discogs())
        dgl.addWidget(self.dg_vinyl)
        self.dg_refresh = button("Refresh from Discogs", lambda: self.load_discogs(refresh=True))
        dgl.addWidget(self.dg_refresh)
        self.dg_bar.setVisible(False)
        v.addWidget(self.dg_bar)

        self.mb_bar = QWidget()
        top = QHBoxLayout(self.mb_bar)
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)
        self.artist, self.title, self.barcode = QLineEdit(), QLineEdit(), QLineEdit()
        self.artist.setPlaceholderText("Artist")
        self.title.setPlaceholderText("Album (leave blank to list all their albums)")
        self.barcode.setPlaceholderText("or barcode")
        self.barcode.setFixedWidth(160)
        for e in (self.artist, self.title, self.barcode):
            e.returnPressed.connect(self.search)
        self.vinyl = QCheckBox("Vinyl only")
        self.vinyl.setChecked(True)
        self.vinyl.toggled.connect(self.refilter)
        self.search_btn = button("Search", self.search, "primary")
        top.addWidget(self.artist, 2)
        top.addWidget(self.title, 3)
        top.addWidget(self.barcode)
        top.addWidget(self.vinyl)
        top.addWidget(self.search_btn)
        v.addWidget(self.mb_bar)

        body = QHBoxLayout()
        body.setSpacing(18)
        self.stack = QStackedWidget()
        p0 = QWidget()
        l0 = QVBoxLayout(p0)
        l0.setContentsMargins(0, 0, 0, 0)
        self.album_list = make_list(self.covers, 72)
        self.album_list.currentItemChanged.connect(lambda cur, prev: self.preview_album())
        self.album_list.itemActivated.connect(lambda item: self.next_step())
        l0.addWidget(self.album_list)
        p1 = QWidget()
        l1 = QVBoxLayout(p1)
        l1.setContentsMargins(0, 0, 0, 0)
        l1.setSpacing(10)
        head = QHBoxLayout()
        self.back_btn = button("< Albums", self.show_albums, "ghost")
        head.addWidget(self.back_btn)
        self.press_head = label("", "lead")
        head.addWidget(self.press_head, 1)
        self.pfilter = QLineEdit()
        self.pfilter.setPlaceholderText("Filter: country, label, catalogue no. or barcode")
        self.pfilter.setClearButtonEnabled(True)
        self.pfilter.setFixedWidth(330)
        self.pfilter.textChanged.connect(self.refilter)
        head.addWidget(self.pfilter)
        l1.addLayout(head)
        self.press_table = table(["Released", "Country", "Format", "Label / cat. no.", "Barcode", "Notes"], 3)
        self.press_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.press_table.currentCellChanged.connect(lambda *a: self.preview_pressing())
        self.press_table.cellDoubleClicked.connect(lambda *a: self.next_step())
        l1.addWidget(self.press_table)
        self.stack.addWidget(p0)
        self.stack.addWidget(p1)
        p2 = QWidget()
        l2 = QVBoxLayout(p2)
        l2.setContentsMargins(0, 0, 0, 0)
        self.dg_stack = QStackedWidget()
        unlinked = card()
        ul = QVBoxLayout(unlinked)
        ul.setContentsMargins(28, 28, 28, 28)
        ul.addWidget(label("Your Discogs collection", "h2"))
        ul.addWidget(label("Link your Discogs account to pick records straight from your collection. Their covers, "
                           "labels and catalogue numbers come along too.", "muted", wrap=True))
        ul.addWidget(button("Link Discogs account", self.link_discogs, "primary"), 0, Qt.AlignmentFlag.AlignLeft)
        ul.addStretch(1)
        self.dg_stack.addWidget(unlinked)
        self.dg_list = make_list(self.covers, 72)
        self.dg_list.currentItemChanged.connect(lambda cur, prev: self.preview_discogs())
        self.dg_list.itemActivated.connect(lambda item: self.next_step())
        self.dg_stack.addWidget(self.dg_list)
        l2.addWidget(self.dg_stack)
        self.stack.addWidget(p2)
        body.addWidget(self.stack, 1)

        pc = card()
        pc.setFixedWidth(360)
        pcl = QVBoxLayout(pc)
        pcl.setContentsMargins(0, 0, 0, 0)
        pscroll = QScrollArea()  # long track listings scroll inside the card
        pscroll.setWidgetResizable(True)
        pscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pinner = QWidget()
        pinner.setObjectName("detailInner")
        pinner.setStyleSheet("QWidget#detailInner { background: transparent; }")
        pscroll.setWidget(pinner)
        pcl.addWidget(pscroll)
        self.pscroll = pscroll
        pv = QVBoxLayout(pinner)
        pv.setContentsMargins(20, 20, 20, 20)
        pv.setSpacing(8)
        self.pcover = CoverWidget(self.covers, 240)
        pv.addWidget(self.pcover, 0, Qt.AlignmentFlag.AlignHCenter)
        pv.addSpacing(6)
        self.ptitle = label("", "h2", wrap=True)
        self.psub = label("", "muted", wrap=True)
        pv.addWidget(self.ptitle)
        pv.addWidget(self.psub)
        pv.addSpacing(6)
        self.ptr_caption = label("TRACK LISTING", "caption")
        self.ptr_source = label("", "muted", wrap=True)
        self.ptr_box, self.ptr_grid = make_track_grid()
        self.ptr_status = label("", "muted", wrap=True)
        for w in (self.ptr_caption, self.ptr_source, self.ptr_box, self.ptr_status):
            pv.addWidget(w)
            w.setVisible(False)
        pv.addStretch(1)
        body.addWidget(pc)
        self.track_timer = QTimer(self)
        self.track_timer.setSingleShot(True)
        self.track_timer.setInterval(350)  # wait until you stop moving through the list
        self.track_timer.timeout.connect(self._fetch_tracks)
        v.addLayout(body, 1)

        bottom = QHBoxLayout()
        self.status = label("Type the artist and album, then Search.", "muted", wrap=True)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(button("Enter manually", self.manual))
        bottom.addWidget(button("Cancel", self.reject))
        self.next_btn = button("Open album", self.next_step, "primary")
        bottom.addWidget(self.next_btn)
        v.addLayout(bottom)
        self.artist.setFocus()
        self.show_albums()

    # ---- helpers -----------------------------------------------------------
    def done(self, result):
        self.closed = True
        self.press_gen += 1
        self.covers.cancel_queued()
        super().done(result)

    def _task(self, fn, ok):
        def alive(cb):
            return lambda val: None if self.closed else cb(val)
        run_task(fn, alive(ok), alive(self._failed), self.tasks)

    def _set_status(self, text, style="muted"):
        self.status.setText(text)
        restyle(self.status, style)

    def _set_busy(self, busy, text=None):
        self.busy = busy
        if busy:
            self.setCursor(Qt.CursorShape.BusyCursor)
        else:
            self.unsetCursor()
        if text is not None:
            self._set_status(text)
        self._buttons()

    def _buttons(self):
        self.search_btn.setEnabled(not self.busy)
        if self.mode == "discogs":
            has = self.dg_list.currentItem() is not None and self.dg_stack.currentIndex() == 1
            self.next_btn.setText("Use this record")
        elif self.mode == "albums":
            has = self.album_list.currentItem() is not None
            self.next_btn.setText("Open album")
        else:
            has = self.press_table.currentRow() >= 0 and bool(self.shown)
            self.next_btn.setText("Use this pressing")
        self.next_btn.setEnabled(has and not self.busy)

    def _failed(self, e):
        self._set_busy(False, "")
        QMessageBox.warning(self, "Discogs" if isinstance(e, core.DiscogsError) else "MusicBrainz",
                            str(e) if isinstance(e, core.MBError) else repr(e))

    def _preview(self, key, title, sub, seed="", tracks=None):
        self.pcover.show_cover(key, seed or title)
        self.ptitle.setText(breakable(title))
        self.psub.setText(breakable(sub))
        self.pscroll.verticalScrollBar().setValue(0)
        self.show_tracks(tracks)

    # ---- track listing preview --------------------------------------------------
    def show_tracks(self, key):
        """Show the track listing for ("album", id), ("release", id) or ("discogs", id); None hides it."""
        self.track_key = key
        if key is None:
            self.track_timer.stop()
            for w in (self.ptr_caption, self.ptr_source, self.ptr_box, self.ptr_status):
                w.setVisible(False)
            return
        if key in self.track_cache:
            self._render_tracks(*self.track_cache[key])
            return
        self._render_tracks([], {}, "", "Getting the track listing...")
        self.track_timer.start()

    def _render_tracks(self, tracks, lengths, source="", status=""):
        fill_track_grid(self.ptr_grid, tracks, lengths)
        self.ptr_caption.setVisible(True)
        self.ptr_box.setVisible(bool(tracks))
        self.ptr_source.setText(source)
        self.ptr_source.setVisible(bool(source) and bool(tracks))
        if not tracks and not status:
            status = "No track listing is available for this one."
        self.ptr_status.setText(status)
        self.ptr_status.setVisible(bool(status))

    def _fetch_tracks(self):
        key = self.track_key
        if key is None or key in self.track_cache or key in self.track_loading:
            return
        kind, ident = key
        token, _ = core.discogs_account(self.con)
        self.track_loading.add(key)

        def work():
            if kind == "album":
                rel = core.album_sample_release(ident)
                if not rel:
                    return [], {}, "", None
                bits = [x for x in (rel.get("date", "")[:4], rel.get("country"), core.media_summary(rel.get("media"))) if x]
                return core.release_tracklist(rel) + ("From the {} release. Pick your pressing to see its own."
                                                      .format(" ".join(bits)) if bits else "", None)
            if kind == "release":
                rel = core.fetch_release(ident)
                return core.release_tracklist(rel) + ("", rel)
            rel = core.discogs_get("/releases/{}".format(int(ident)), token)
            return core.release_tracklist(core.discogs_to_release(rel)) + ("", None)

        def done(val):
            self.track_loading.discard(key)
            tracks, lengths, source, rel = val
            if rel is not None:
                self.rel_cache[ident] = rel  # no need to fetch this pressing again when you add it
            self.track_cache[key] = (tracks, lengths, source)
            if self.track_key == key:
                self._render_tracks(tracks, lengths, source)

        def failed(e):
            self.track_loading.discard(key)
            if self.track_key == key:
                self._render_tracks([], {}, "", "Couldn't get the track listing: {}".format(
                    str(e) if isinstance(e, core.MBError) else repr(e)))

        run_task(work, lambda v: None if self.closed else done(v),
                 lambda e: None if self.closed else failed(e), self.tasks)

    # ---- searching -----------------------------------------------------------
    def search(self):
        artist, title, bc = self.artist.text().strip(), self.title.text().strip(), self.barcode.text().strip()
        if bc:
            self._set_busy(True, "Looking up the barcode...")
            self._task(lambda: core.search_releases(barcode=bc, vinyl=False, limit=25)[0], self._got_barcode)
            return
        if not artist and not title:
            self._set_status("Type the artist and album, or just the artist to list their albums.", "warn")
            return
        self._set_busy(True, "Searching MusicBrainz...")
        self._task(lambda: core.search_albums(artist or None, title or None),
                   lambda res: self._got_albums(res, bool(title)))

    def _got_albums(self, albums, had_title):
        self.albums = albums
        lst = self.album_list
        lst.blockSignals(True)
        lst.clear()
        for i, g in enumerate(albums):
            types = [g.get("primary-type") or "Other"] + list(g.get("secondary-types") or [])
            year = (g.get("first-release-date") or "")[:4]
            item = QListWidgetItem()
            item.setData(ROLE, {"i": i, "title": g.get("title", ""), "cover": ("release-group", g["id"], 250),
                                "subtitle": core.album_artist(g) + ("  ·  " + year if year else ""),
                                "meta": " + ".join(types)})
            lst.addItem(item)
        lst.blockSignals(False)
        self.show_albums()
        if not albums:
            self._set_busy(False, "No album found by that artist with that title. Check the spelling, or leave "
                                  "the album blank to list all of the artist's albums.")
            self._set_status(self.status.text(), "warn")
            return
        lst.setCurrentRow(0)
        if len(albums) == 1:
            self._set_busy(False)
            self.open_album(albums[0])
            return
        extra = "" if had_title else " Albums only: add a title to find singles and EPs."
        self._set_busy(False, "{} matches, best first. Pick yours and click Open album.{}".format(len(albums), extra))

    def show_albums(self):
        self.mode = self.mb_mode = "albums"
        self.press_gen += 1  # stop loading pressings of an album we've left
        self.stack.setCurrentIndex(0)
        self.preview_album()
        self._buttons()

    def preview_album(self):
        item = self.album_list.currentItem()
        if item is None:
            self._preview(None, "", "Album art appears here when you pick an album.")
        else:
            g = self.albums[item.data(ROLE)["i"]]
            types = [g.get("primary-type") or "Other"] + list(g.get("secondary-types") or [])
            year = (g.get("first-release-date") or "")[:4]
            self._preview(("release-group", g["id"], 500), g.get("title", ""),
                          "{}\n{}".format(core.album_artist(g), "   ·   ".join(
                              x for x in (year, " + ".join(types)) if x)), tracks=("album", g["id"]))
        self._buttons()

    def open_album(self, g):
        # Thumbnails still waiting to download would only slow this down, so drop them.
        self.covers.cancel_queued()
        self.track_timer.stop()
        self.album, self.pressings, self.press_total = g, [], 0
        self.press_gen += 1
        gen = self.press_gen
        self._set_busy(True, "Loading pressings of {}...".format(g.get("title")))
        self._task(lambda: core.list_pressings_page(g["id"], 0), lambda val: self._got_page(val, g, gen, 0))

    def _got_page(self, val, g, gen, offset):
        """Show the first page of pressings straight away and keep loading the rest behind it."""
        if gen != self.press_gen:
            return
        batch, total = val
        self.pressings.extend(batch)
        self.pressings.sort(key=core.pressing_sort_key)
        self.press_total = total
        nxt = len(self.pressings)
        self.loading_more = bool(batch) and nxt < total and nxt < core.PRESSING_CAP
        if offset == 0:
            self._set_busy(False)
            self.show_pressings()
        elif self.mode == "pressings":
            self.refilter()
        if self.loading_more:
            self._task(lambda: core.list_pressings_page(g["id"], nxt), lambda v: self._got_page(v, g, gen, nxt))

    def _got_barcode(self, items):
        self._set_busy(False)
        if not items:
            self._set_status("No release on MusicBrainz has that barcode. Try the artist and album.", "warn")
            return
        self.album, self.press_total, self.loading_more = None, len(items), False
        self.pressings = sorted(items, key=core.pressing_sort_key)
        self.show_pressings()

    def show_pressings(self):
        self.mode = self.mb_mode = "pressings"
        self.stack.setCurrentIndex(1)
        self.back_btn.setVisible(bool(self.albums) and self.album is not None)
        self.pfilter.blockSignals(True)
        self.pfilter.clear()
        self.pfilter.blockSignals(False)
        self.refilter()

    def refilter(self, *args):
        if self.mode != "pressings":
            return
        row = self.press_table.currentRow()
        keep = self.shown[row]["id"] if 0 <= row < len(self.shown) else None
        items, note, style = self.pressings, "", "muted"
        if self.vinyl.isChecked():
            vinyl = [r for r in items if core.is_vinyl(r)]
            if vinyl:
                items = vinyl
            elif items:
                note, style = " MusicBrainz lists no vinyl pressings of this album, so all formats are shown.", "warn"
        title = self.album.get("title") if self.album else None
        words = self.pfilter.text().lower().split()
        if words:
            items = [r for r in items if all(w in core.describe_pressing(r, title).lower() for w in words)]
        self.shown = items
        t = self.press_table
        t.blockSignals(True)
        t.setRowCount(len(items))
        for i, r in enumerate(items):
            vals = (r.get("date") or "", r.get("country") or "", core.media_summary(r.get("media")),
                    core.release_label(r), r.get("barcode") or "", core.pressing_notes(r, title))
            for c, val in enumerate(vals):
                t.setItem(i, c, cell(val))
        t.blockSignals(False)
        kind = "vinyl pressing" if self.vinyl.isChecked() and not note else "pressing"
        if self.album:
            self.press_head.setText("{} by {}:  {} {}{}".format(
                title, core.album_artist(self.album), len(items), kind, "" if len(items) == 1 else "s"))
        else:
            self.press_head.setText("Releases with that barcode:  {}".format(len(items)))
        text = "Pick the one you own. The label, catalogue number and barcode on your copy tell them apart."
        if self.loading_more:
            text += " Loading more ({} of {} so far)...".format(len(self.pressings), self.press_total)
        elif self.press_total > len(self.pressings):
            text += " This album has {} releases; the first {} are listed.".format(self.press_total,
                                                                                len(self.pressings))
        self._set_status(text + note, style)
        if items:
            ids = [r["id"] for r in items]
            t.setCurrentCell(ids.index(keep) if keep in ids else 0, 0)
        self.preview_pressing()

    def preview_pressing(self):
        row = self.press_table.currentRow()
        if self.mode != "pressings" or not (0 <= row < len(self.shown)):
            self._buttons()
            return
        r = self.shown[row]
        rgid = self.album["id"] if self.album else None
        artist = core.album_artist(self.album) if self.album else core.artist_credit_str(r.get("artist-credit"))
        lines = [artist, "   ·   ".join(x for x in (r.get("date"), r.get("country"),
                                                          core.media_summary(r.get("media"))) if x)]
        if core.release_label(r):
            lines.append(core.release_label(r))
        if r.get("barcode"):
            lines.append("Barcode " + r["barcode"])
        self._preview(("pressing", r["id"], rgid, 500), r.get("title", ""), "\n".join(lines),
                      tracks=("release", r["id"]))
        self._buttons()

    # ---- choosing --------------------------------------------------------------
    def next_step(self):
        if self.busy:
            return
        if self.mode == "discogs":
            self.use_discogs()
            return
        if self.mode == "albums":
            item = self.album_list.currentItem()
            if item is not None:
                self.open_album(self.albums[item.data(ROLE)["i"]])
            return
        row = self.press_table.currentRow()
        if not (0 <= row < len(self.shown)):
            return
        mbid = self.shown[row]["id"]
        dup = self.con.execute("SELECT title, artist FROM records WHERE mbid = ?", (mbid,)).fetchone()
        if dup:
            QMessageBox.information(self, "Add a record", "That pressing is already in your library:\n{} by {}"
                                    .format(dup["title"], dup["artist"]))
            return
        if mbid in self.rel_cache:  # already fetched for the track listing preview
            self._got_release(self.rel_cache[mbid], mbid)
            return
        self._set_busy(True, "Fetching the track list...")
        self._task(lambda: core.fetch_release(mbid), lambda rel: self._got_release(rel, mbid))

    def _got_release(self, rel, mbid):
        self._set_busy(False, "Check the side lengths, then Save.")
        sides = core.build_sides(rel, allow_prompt=False)
        notes, style = [], "muted"
        if not sides:
            notes.append("MusicBrainz has no track list for this release, so enter the side lengths from the "
                         "sleeve or label.")
            style = "warn"
        elif not core.release_has_side_letters(rel):
            notes.append("MusicBrainz doesn't mark the sides for this release, so the split between sides has "
                         "been estimated. Check against the sleeve or label.")
            style = "warn"
        if any(s["missing"] for s in sides):
            notes.append("Some tracks have no length on MusicBrainz, so those sides may be short.")
            style = "warn"
        if not notes:
            notes.append("Side lengths from MusicBrainz. Correct any that look wrong, then Save.")
        dlg = SidesEditor(self, "Confirm record", core.artist_credit_str(rel.get("artist-credit")),
                          rel.get("title") or "", (rel.get("date") or "")[:4], sides, " ".join(notes), style)
        if dlg.exec():
            new_sides, artist, title, year = dlg.result_data
            rgid = (rel.get("release-group") or {}).get("id") or (self.album or {}).get("id")
            self.saved_id = core.save_record(self.con, rel.get("id", mbid), artist, title, year,
                                             core.media_summary(rel.get("media")), new_sides, rgid,
                                             tracks=core.tracks_from_sides(sides))
            self.accept()

    # ---- Discogs ---------------------------------------------------------------
    def set_source(self, key):
        self.covers.cancel_queued()
        discogs = key == "discogs"
        self.mb_bar.setVisible(not discogs)
        self.dg_bar.setVisible(discogs)
        if discogs:
            self.mode = "discogs"
            self.stack.setCurrentIndex(2)
            self.load_discogs()
            self.dg_filter.setFocus()
        else:
            self.mode = self.mb_mode
            self.stack.setCurrentIndex(0 if self.mode == "albums" else 1)
            if self.mode == "albums":
                self.preview_album()
            else:
                self.preview_pressing()
            self._set_status("Type the artist and album, then Search." if not self.albums else "")
        self._buttons()

    def link_discogs(self):
        if DiscogsLinkDialog(self, self.main).exec():
            self.load_discogs(refresh=True)

    def load_discogs(self, refresh=False):
        token, user = core.discogs_account(self.con)
        self.dg_refresh.setEnabled(bool(token))
        if not token:
            self.dg_stack.setCurrentIndex(0)
            self._preview(None, "", "Covers appear here when you pick a record.")
            self._set_status("Link your Discogs account to browse your collection here.")
            self._buttons()
            return
        self.dg_stack.setCurrentIndex(1)
        if not refresh:
            items, age = core.load_discogs_cache(user)
            if items is not None:
                self.dg_items = items
                self.fill_discogs()
                if age < core.DISCOGS_STALE_HOURS:
                    self._set_status("{} items in {}'s collection. Refresh to pick up anything added on Discogs "
                                     "since.".format(len(items), user))
                    return
        self.dg_gen += 1
        gen, self.dg_new = self.dg_gen, []
        self.dg_refresh.setEnabled(False)
        self._set_status("Reading your Discogs collection...")
        self._task(lambda: core.discogs_collection_page(token, user, 1), lambda val: self._got_dg_page(val, gen, 1))

    def _got_dg_page(self, val, gen, page):
        """Collection pages arrive a hundred at a time; show them as they come."""
        if gen != self.dg_gen:
            return
        batch, pages, total = val
        self.dg_new.extend(batch)
        token, user = core.discogs_account(self.con)
        if page >= pages or not batch:
            self.dg_items = self.dg_new
            core.save_discogs_cache(user, self.dg_items)
            self.dg_refresh.setEnabled(True)
            self.fill_discogs()
            self._set_status("{} items in {}'s collection.".format(len(self.dg_items), user))
            return
        if not self.dg_items or page == 1:
            self.dg_items = list(self.dg_new)
            self.fill_discogs()
        self._set_status("Reading your Discogs collection: {} of {}...".format(len(self.dg_new), total))
        self._task(lambda: core.discogs_collection_page(token, user, page + 1),
                   lambda v: self._got_dg_page(v, gen, page + 1))

    def fill_discogs(self):
        cur = self.dg_list.currentItem()
        keep = self.dg_shown[cur.data(ROLE)["i"]]["id"] if cur is not None and self.dg_shown else None
        infos = [core.discogs_item_info(it) for it in self.dg_items]
        if self.dg_vinyl.isChecked():
            infos = [i for i in infos if i["vinyl"]]
        words = self.dg_filter.text().lower().split()
        if words:
            infos = [i for i in infos if all(w in "{} {} {} {}".format(i["artist"], i["title"], i["label"],
                                                                         i["year"]).lower() for w in words)]
        self.dg_shown = infos
        have = {r[0] for r in self.con.execute("SELECT discogs_id FROM records WHERE discogs_id IS NOT NULL")}
        lst = self.dg_list
        lst.blockSignals(True)
        lst.clear()
        select = 0
        for n, d in enumerate(discogs_info_rows(self.covers, infos, have)):
            item = QListWidgetItem()
            item.setData(ROLE, d)
            lst.addItem(item)
            if infos[n]["id"] == keep:
                select = n
        lst.blockSignals(False)
        if infos:
            lst.setCurrentRow(select)
        self.preview_discogs()

    def preview_discogs(self):
        item = self.dg_list.currentItem()
        if item is None or not self.dg_shown:
            self._preview(None, "", "Nothing matches the filter." if self.dg_items else "")
        else:
            i = self.dg_shown[item.data(ROLE)["i"]]
            inlib = self.con.execute("SELECT 1 FROM records WHERE discogs_id = ?", (i["id"],)).fetchone()
            lines = [i["artist"], "   ·   ".join(x for x in (i["year"], i["format"]) if x)]
            if i["label"]:
                lines.append(i["label"])
            if inlib:
                lines.append("Already in your library")
            self._preview(("url", i["cover"], 500) if i["cover"] else None, i["title"], "\n".join(lines),
                          tracks=("discogs", i["id"]))
        self._buttons()

    def use_discogs(self):
        item = self.dg_list.currentItem()
        if item is None or not self.dg_shown:
            return
        i = self.dg_shown[item.data(ROLE)["i"]]
        dup = self.con.execute("SELECT title, artist FROM records WHERE discogs_id = ?", (i["id"],)).fetchone()
        if dup:
            QMessageBox.information(self, "Add a record", "That record is already in your library:\n{} by {}".format(
                dup["title"], dup["artist"]))
            return
        token, _ = core.discogs_account(self.con)
        self.covers.cancel_queued()
        self._set_busy(True, "Fetching the track list from Discogs...")
        self._task(lambda: core.prepare_discogs_release(token, i["id"]), self._got_discogs_release)

    def _got_discogs_release(self, info):
        self._set_busy(False, "Check the side lengths, then Save.")
        notes = list(info["notes"])
        warn = any(s["missing"] for s in info["sides"]) or info["estimated"] or not info["sides"]
        if not info["sides"]:
            notes.append("Neither Discogs nor MusicBrainz has a track list for this release, so enter the side "
                         "lengths from the sleeve or label.")
        if not notes:
            notes.append("Side lengths from Discogs. Correct any that look wrong, then Save.")
        dlg = SidesEditor(self, "Confirm record", info["artist"], info["title"] or "", info["year"], info["sides"],
                          " ".join(notes), "warn" if warn else "muted")
        if dlg.exec():
            sides, artist, title, year = dlg.result_data
            self.saved_id = core.save_record(self.con, info["mbid"], artist, title, year, info["format"], sides,
                                             info["rgid"], info["discogs_id"], info["cover_url"],
                                             info.get("tracks"), info.get("about"))
            self.accept()


    def manual(self):
        dlg = SidesEditor(self, "Enter a record", self.artist.text().strip(), self.title.text().strip(), "", [],
                          "Type the side lengths from the sleeve or label.")
        if dlg.exec():
            sides, artist, title, year = dlg.result_data
            self.saved_id = core.save_record(self.con, None, artist, title, year, "Vinyl", sides)
            self.accept()


class DiscogsLinkDialog(QDialog):
    """Link a Discogs account with a personal access token."""

    def __init__(self, parent, main):
        super().__init__(parent)
        self.main, self.tasks, self.closed, self.user = main, set(), False, None
        v = dialog_layout(self, "Link your Discogs account", 560)
        v.addWidget(label("Link your Discogs account", "h2"))
        v.addWidget(label("Linking lets you pick records straight from your Discogs collection when adding them.",
                          "muted", wrap=True))
        step1 = label('1.  Open your <a href="{0}" style="color:{1}">Discogs developer settings</a> and click '
                      '<b>Generate new token</b>.'.format(core.DISCOGS_TOKEN_PAGE, C["accent"]), wrap=True)
        step1.setOpenExternalLinks(True)
        v.addWidget(step1)
        v.addWidget(label("2.  Copy the token and paste it here.", wrap=True))
        row = QHBoxLayout()
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Personal access token")
        self.token.returnPressed.connect(self.link)
        row.addWidget(self.token, 1)
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.token.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        row.addWidget(show)
        v.addLayout(row)
        v.addWidget(label("The token only lets this app read your Discogs data. It's kept in your library file on "
                          "this computer. You can unlink in Preferences, and delete the token on Discogs at any time.",
                          "muted", wrap=True))
        self.status = label("", "muted", wrap=True)
        v.addWidget(self.status)
        v.addStretch(1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(button("Cancel", self.reject))
        self.link_btn = button("Link account", self.link, "primary")
        bottom.addWidget(self.link_btn)
        v.addLayout(bottom)

    def done(self, result):
        self.closed = True
        super().done(result)

    def link(self):
        token = self.token.text().strip()
        if not token:
            self.status.setText("Paste your token first.")
            restyle(self.status, "warn")
            return
        self.link_btn.setEnabled(False)
        self.status.setText("Checking the token with Discogs...")
        restyle(self.status, "muted")

        def ok(user):
            if self.closed:
                return
            core.save_discogs_account(self.main.con, token, user)  # into the system keyring if there is one
            self.user = user
            self.main.update_discogs_tab()
            self.accept()

        def failed(e):
            if not self.closed:
                self.link_btn.setEnabled(True)
                self.status.setText(str(e) if isinstance(e, core.MBError) else repr(e))
                restyle(self.status, "warn")

        run_task(lambda: core.discogs_identity(token), ok, failed, self.tasks)


def discogs_info_rows(store, infos, have):
    """List items for Discogs collection entries."""
    rows = []
    for n, i in enumerate(infos):
        fmt = i["format"].split(";")[0]
        rows.append({"i": n, "title": i["title"], "cover": ("url", i["cover"], 250) if i["cover"] else None,
                     "subtitle": i["artist"] + ("  ·  " + i["year"] if i["year"] else ""),
                     "meta": fmt if len(fmt) <= 22 else fmt[:21] + "...",
                     "meta2": "In your library" if i["id"] in have else i["label"]})
    return rows


class PrefsDialog(QDialog):
    """Appearance and album cover settings. Theme changes preview straight away."""

    def __init__(self, main):
        super().__init__(main)
        self.main, self.app = main, QApplication.instance()
        self.original, self.original_tint = main.theme_pref, main.tint
        outer = dialog_layout(self, "Preferences", 560)
        self.resize(640, 820)
        outer.addWidget(label("Preferences", "h2"))
        scroll = QScrollArea()  # scrolls on smaller screens; Cancel and Save stay in view
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("detailInner")
        inner.setStyleSheet("QWidget#detailInner { background: transparent; }")
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 12, 0)
        v.setSpacing(12)

        v.addSpacing(4)
        v.addWidget(label("APPEARANCE", "caption"))
        self.group = QButtonGroup(self)
        detected = "dark" if system_is_dark(self.app) else "light"
        hints = {"system": "Follows your desktop's light or dark setting, and changes when it does. "
                           "Your desktop is currently {}.".format(detected),
                 "light": "", "dark": ""}
        for key, text in THEMES.items():
            rb = QRadioButton(text)
            rb.setProperty("key", key)
            rb.setChecked(key == main.theme_pref)
            self.group.addButton(rb)
            v.addWidget(rb)
            if hints[key]:
                h = label(hints[key], "muted", wrap=True)
                h.setContentsMargins(27, 0, 0, 4)
                v.addWidget(h)
        self.group.buttonToggled.connect(self.preview)

        v.addSpacing(10)
        v.addWidget(label("COLOUR TINT", "caption"))
        tint_row = QHBoxLayout()
        self.tint_name = label("", "lead")
        tint_row.addWidget(self.tint_name)
        tint_row.addStretch(1)
        v.addLayout(tint_row)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.tints = QButtonGroup(self)
        keys = ["original"] + [k for k, _, _ in TINTS] + ["black", "white"]
        for n, key in enumerate(keys):
            b = QPushButton()
            b.setCheckable(True)
            b.setFixedSize(34, 34)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(TINT_NAMES[key])
            b.setProperty("key", key)
            b.setAccessibleName("Colour tint: " + TINT_NAMES[key])
            b.setChecked(key == main.tint)
            self.tints.addButton(b)
            grid.addWidget(b, n // 10, n % 10)
        grid.setColumnStretch(10, 1)
        v.addLayout(grid)
        v.addWidget(label("Pastels colour the buttons, highlights and a faint wash over the backgrounds. Pure "
                          "black gives true-black backgrounds in dark mode, and pure white gives pure-white "
                          "backgrounds in light mode.", "muted", wrap=True))
        self.tints.buttonClicked.connect(lambda b: self.preview_tint(b.property("key")))
        self._style_swatches()

        v.addSpacing(10)
        v.addWidget(label("ALBUM COVERS", "caption"))
        folder = core.library_cover_path(0).parent
        v.addWidget(label("Each album in your library has its cover saved here, so it shows offline and "
                          "stays put even if temporary files are cleared:", "muted", wrap=True))
        path = label(str(folder), wrap=True)
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(path)
        row = QHBoxLayout()
        row.addWidget(button("Open folder", lambda: (folder.mkdir(parents=True, exist_ok=True),
                                                     QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))))))
        row.addWidget(button("Save missing covers now", self.fetch_missing))
        row.addStretch(1)
        v.addLayout(row)
        self.cover_msg = label("", "muted", wrap=True)
        v.addWidget(self.cover_msg)

        v.addSpacing(10)
        v.addWidget(label("DISCOGS", "caption"))
        self.dg_status = label("", "muted", wrap=True)
        v.addWidget(self.dg_status)
        drow = QHBoxLayout()
        self.dg_link_btn = button("Link Discogs account", self.link_discogs)
        self.dg_unlink_btn = button("Unlink", self.unlink_discogs, "danger")
        drow.addWidget(self.dg_link_btn)
        drow.addWidget(self.dg_unlink_btn)
        drow.addStretch(1)
        v.addLayout(drow)
        self._show_discogs()

        v.addSpacing(10)
        v.addWidget(label("SEARCH PREVIEWS", "caption"))
        self.cache_label = label("", "muted", wrap=True)
        v.addWidget(self.cache_label)
        row2 = QHBoxLayout()
        row2.addWidget(button("Clear preview images", self.clear_cache))
        row2.addStretch(1)
        v.addLayout(row2)
        self._show_cache()

        v.addStretch(1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(button("Cancel", self.reject))
        save = button("Save", self.save, "primary")
        save.setDefault(True)
        bottom.addWidget(save)
        outer.addLayout(bottom)

    def _show_discogs(self):
        token, user = core.discogs_account(self.main.con)
        if token:
            text = "Linked as {}. Your collection has its own Discogs tab. {}".format(
                user, core.token_storage_note(self.main.con))
        elif core.DISCOGS_PROBLEM:
            text = core.DISCOGS_PROBLEM
        else:
            text = "Not linked. Link your account to pick records from your Discogs collection."
        self.dg_status.setText(text)
        self.dg_status.setWordWrap(True)
        self.dg_link_btn.setText("Use a different token" if token or core.DISCOGS_PROBLEM else "Link Discogs account")
        self.dg_unlink_btn.setVisible(bool(token or core.DISCOGS_PROBLEM))

    def link_discogs(self):
        if DiscogsLinkDialog(self, self.main).exec():
            self._show_discogs()

    def unlink_discogs(self):
        if QMessageBox.question(self, "Unlink Discogs", "Unlink your Discogs account? The access token is removed "
                                "from this computer. Records you've already added stay in your library."
                                ) == QMessageBox.StandardButton.Yes:
            core.discogs_unlink(self.main.con)
            self.main.update_discogs_tab()
            self._show_discogs()

    def _choice(self):
        b = self.group.checkedButton()
        return b.property("key") if b else "system"

    def preview(self, btn, checked):
        if checked:
            self.main.set_theme(self._choice(), save=False)
            self._style_swatches()

    def _tint_choice(self):
        b = self.tints.checkedButton()
        return b.property("key") if b else "original"

    def preview_tint(self, key):
        self.main.set_theme(self._choice(), save=False, tint=key)
        self._style_swatches()

    def _style_swatches(self):
        """Round colour swatches; the chosen one gets a ring in the text colour."""
        for b in self.tints.buttons():
            colour = tint_swatch(b.property("key"))
            edge = C["border"] if colour.lower() in ("#000000", "#ffffff") else "transparent"
            b.setStyleSheet("QPushButton {{ background: {0}; border: 1px solid {1}; border-radius: 17px; padding: 0; }}"
                            "QPushButton:hover {{ border: 2px solid {2}; }}"
                            "QPushButton:checked {{ border: 3px solid {3}; }}".format(colour, edge, C["faint"], C["text"]))
        self.tint_name.setText(TINT_NAMES.get(self._tint_choice(), ""))

    def fetch_missing(self):
        n = self.main.prefetch_covers()
        self.cover_msg.setText("Fetching {} cover{} in the background. They appear as they arrive.".format(
            n, "" if n == 1 else "s") if n else "Every album already has its cover saved, or none is available.")

    def _show_cache(self):
        mb = core.search_cache_size() / (1024 * 1024)
        self.cache_label.setText("Cover previews from searches are kept for a while so browsing is quicker. "
                                 "They use {:.1f} MB. Your albums' own covers are not affected by clearing them."
                                 .format(mb))

    def clear_cache(self):
        self.main.covers.cancel_queued()
        core.clear_search_cache()
        self.main.covers.forget_previews()
        self._show_cache()

    def save(self):
        self.main.set_theme(self._choice(), save=True, tint=self._tint_choice())
        self.accept()

    def reject(self):
        self.main.set_theme(self.original, save=False, tint=self.original_tint)
        super().reject()


class StyliDialog(QDialog):
    """Every cartridge / stylus, in use or retired, with its photo, hours, monthly use and specs."""

    def __init__(self, main, select=None):
        super().__init__(main)
        self.main, self.con, self.sid = main, main.con, None
        v = dialog_layout(self, "Cartridges / styli")
        self.resize(1080, 760)
        v.addWidget(label("Cartridges / styli", "h2"))
        body = QHBoxLayout()
        body.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(10)
        self.list = make_list(main.photos, 72)
        self.list.currentItemChanged.connect(lambda cur, prev: self.show_stylus(cur.data(ROLE)["id"] if cur else None))
        left.addWidget(self.list, 1)
        left.addWidget(button("+  Fit a new cartridge / stylus", self.add, "primary"))
        body.addLayout(left, 2)

        outer = card()
        ol = QVBoxLayout(outer)
        ol.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("detailInner")
        inner.setStyleSheet("QWidget#detailInner { background: transparent; }")
        scroll.setWidget(inner)
        ol.addWidget(scroll)
        d = QVBoxLayout(inner)
        d.setContentsMargins(22, 22, 22, 22)
        d.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(18)
        self.photo = CoverWidget(main.photos, 170, placeholder="cartridge", fit=True)
        top.addWidget(self.photo, 0, Qt.AlignmentFlag.AlignTop)
        tv = QVBoxLayout()
        tv.setSpacing(6)
        self.name = label("", "h2", wrap=True)
        self.state = label("", "muted", wrap=True)
        tv.addWidget(self.name)
        tv.addWidget(self.state)
        tv.addSpacing(6)
        self.btn_use = button("Put on turntable", self.put_on, "primary")
        self.btn_retire = button("Retire", self.retire, "danger")
        brow = QHBoxLayout()
        brow.addWidget(self.btn_use)
        brow.addWidget(self.btn_retire)
        brow.addStretch(1)
        tv.addLayout(brow)
        links = QHBoxLayout()
        links.setSpacing(2)
        self.btn_edit = button("Edit", self.edit, "ghost")
        self.btn_lookup = button("Find details online", self.lookup, "ghost")
        self.btn_photo = button("Choose photo", self.choose_photo, "ghost")
        self.btn_plays = button("Show plays", self.show_plays, "ghost")
        self.btn_delete = button("Delete", self.delete, "ghost")
        for b in (self.btn_edit, self.btn_lookup, self.btn_photo, self.btn_plays, self.btn_delete):
            links.addWidget(b)
        links.addStretch(1)
        tv.addLayout(links)
        tv.addStretch(1)
        top.addLayout(tv, 1)
        d.addLayout(top)

        d.addSpacing(6)
        self.stats_grid = QGridLayout()
        self.stats_grid.setHorizontalSpacing(24)
        self.stats_grid.setVerticalSpacing(12)
        self.stat = {}
        for i, (key, cap) in enumerate((("used", "HOURS USED"), ("rated", "RATED LIFE"), ("worn", "WORN"),
                                        ("sides", "SIDES PLAYED"), ("records", "RECORDS"), ("dates", "IN SERVICE"))):
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addWidget(label(cap, "caption"))
            self.stat[key] = label("-", "statValue")
            box.addWidget(self.stat[key])
            self.stats_grid.addLayout(box, i // 3, i % 3)
        d.addLayout(self.stats_grid)
        self.wear = QProgressBar()
        self.wear.setTextVisible(False)
        self.wear.setRange(0, 1000)
        self.wear.setFixedHeight(10)
        d.addWidget(self.wear)

        d.addSpacing(8)
        self.month_caption = label("HOURS PLAYED BY MONTH", "caption")
        d.addWidget(self.month_caption)
        self.months = MonthBars()
        d.addWidget(self.months)

        d.addSpacing(8)
        d.addWidget(label("SPECIFICATIONS", "caption"))
        self.specs_form = QFormLayout()
        self.specs_form.setHorizontalSpacing(18)
        self.specs_form.setVerticalSpacing(6)
        d.addLayout(self.specs_form)
        self.desc = label("", "muted", wrap=True)
        d.addWidget(self.desc)
        self.source = label("", "muted", wrap=True)
        self.source.setOpenExternalLinks(True)
        d.addWidget(self.source)
        d.addStretch(1)
        body.addWidget(outer, 3)
        v.addLayout(body, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(button("Close", self.accept))
        v.addLayout(bottom)
        self.refresh(select)

    # ---- list ----------------------------------------------------------------
    def refresh(self, select=None):
        select = select or self.sid
        cur = core.active_stylus(self.con)
        rows = self.con.execute("SELECT * FROM styli").fetchall()
        rows.sort(key=lambda s: (0 if s["active"] else 1, s["id"] if s["active"] else
                                 -(core.to_dt(s["retired_at"]).timestamp() if s["retired_at"] else 0)))
        self.list.blockSignals(True)
        self.list.clear()
        sel = None
        for s in rows:
            m = core.stylus_summary(self.con, s)
            if s["active"]:
                sub = "On the turntable" if cur and s["id"] == cur["id"] else "In use"
            else:
                sub = "Retired {}".format(core.to_dt(s["retired_at"]).strftime("%d %b %Y")) if s["retired_at"] \
                    else "Retired"
            item = QListWidgetItem()
            item.setData(ROLE, {"id": s["id"], "title": s["name"], "subtitle": sub, "placeholder": "cartridge",
                                "fit": True, "cover": self.main.photos.key(s["id"]),
                                "meta": "{:.1f} h".format(m["used"] / 3600),
                                "meta2": "{:.0f}% of {:g} h".format(m["pct"], s["rated_hours"])})
            self.list.addItem(item)
            if s["id"] == select:
                sel = item
        self.list.blockSignals(False)
        if sel is None and self.list.count():
            sel = self.list.item(0)
        if sel is not None:
            self.list.setCurrentItem(sel)
        self.show_stylus(sel.data(ROLE)["id"] if sel is not None else None)

    def _changed(self, select=None):
        self.main.photos.updated()
        self.main.refresh_all()
        self.refresh(select)

    # ---- detail ---------------------------------------------------------------
    def show_stylus(self, sid):
        self.sid = sid
        s = self.con.execute("SELECT * FROM styli WHERE id = ?", (sid,)).fetchone() if sid else None
        for w in (self.btn_use, self.btn_retire, self.btn_edit, self.btn_lookup, self.btn_photo, self.btn_plays,
                  self.btn_delete):
            w.setVisible(s is not None)
        while self.specs_form.rowCount():
            self.specs_form.removeRow(0)
        if not s:
            self.photo.show_cover(None, "")
            self.name.setText("No cartridges / styli yet")
            self.state.setText("Click Fit a new cartridge / stylus to add the one on your turntable.")
            for w in self.stat.values():
                w.setText("-")
            self.months.set_data([])
            self.month_caption.setVisible(False)
            self.desc.setText("")
            self.source.setText("")
            return
        m = core.stylus_stats(self.con, s)
        cur = core.active_stylus(self.con)
        on = cur is not None and cur["id"] == s["id"]
        self.photo.show_cover(self.main.photos.key(s["id"]), s["name"])
        self.name.setText(s["name"])
        if not s["active"]:
            state = "Retired {}. Its plays and hours are kept.".format(m["retired"].strftime("%d %b %Y")
                                                                       if m["retired"] else "")
        elif on:
            state = "On the turntable: new plays are logged against this cartridge / stylus."
        else:
            state = "In use, but not on the turntable right now."
        if m["level"] == "over":
            state += " Rated life reached."
        elif m["level"] == "warn":
            state += " Past the {:g}% warning level.".format(s["warn_pct"])
        self.state.setText(state)
        restyle(self.state, {"ok": "muted", "warn": "warn", "over": "over"}[m["level"]])
        self.btn_use.setText("Back in use" if not s["active"] else "Put on turntable")
        self.btn_use.setVisible(not on)
        self.btn_retire.setVisible(bool(s["active"]))
        self.btn_delete.setVisible(m["sides_played"] == 0)
        self.stat["used"].setText("{:.1f} h".format(m["used"] / 3600))
        self.stat["rated"].setText("{:g} h".format(s["rated_hours"]))
        self.stat["worn"].setText("{:.1f}%".format(m["pct"]))
        self.stat["sides"].setText(str(m["sides_played"]))
        self.stat["records"].setText(str(m["records"]))
        end = m["retired"] or core.now_local()
        self.stat["dates"].setText("{} to {}".format(m["installed"].strftime("%b %Y"),
                                                     "now" if s["active"] else end.strftime("%b %Y")))
        self.wear.setValue(int(min(100, m["pct"]) * 10))
        restyle(self.wear, level=m["level"])
        months = core.stylus_hours_by_month(self.con, s["id"])
        self.months.set_data(months)
        self.month_caption.setVisible(bool(months))
        specs = core.get_specs(s)
        shown = 0
        for key, lab, _ in core.SPEC_FIELDS:
            if specs.get(key):
                val = label(specs[key], wrap=True)
                val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                self.specs_form.addRow(label(lab, "muted"), val)
                shown += 1
        if not shown:
            self.specs_form.addRow(label("None saved yet. Use Find details online, or Edit to type them in.",
                                         "muted", wrap=True))
        self.desc.setText(specs.get("description", ""))
        self.desc.setVisible(bool(specs.get("description")))
        url = s["url"] or ""
        self.source.setText('Details from <a href="{0}" style="color:{1}">{2}</a>'.format(
            url, C["accent"], QUrl(url).host() or url) if url else "")

    def _row(self):
        return self.con.execute("SELECT * FROM styli WHERE id = ?", (self.sid,)).fetchone() if self.sid else None

    # ---- actions ----------------------------------------------------------------
    def put_on(self):
        if self.sid:
            core.stylus_use(self.con, self.sid)
            self._changed(self.sid)

    def retire(self):
        s = self._row()
        if s and QMessageBox.question(self, "Retire cartridge / stylus", "Retire {}? Its {} of plays stay in your history."
                                      .format(s["name"], core.fmt_hours(core.stylus_used_seconds(self.con, s)))
                                      ) == QMessageBox.StandardButton.Yes:
            core.stylus_retire(self.con, s["id"])
            self._changed(s["id"])

    def delete(self):
        s = self._row()
        if s and QMessageBox.question(self, "Delete cartridge / stylus", "Delete {}? Use this only for a cartridge / stylus added by "
                                      "mistake.".format(s["name"])) == QMessageBox.StandardButton.Yes:
            core.stylus_delete(self.con, s["id"])
            self.sid = None
            self._changed()

    def add(self):
        dlg = StylusEditor(self, None, core.styli_in_use(self.con))
        if not dlg.exec():
            return
        r = dlg.result_data
        sid = core.stylus_add(self.con, r["name"], r["hours"], r["used"], r["warn"], r["installed"], r["replaces"])
        core.set_stylus_details(self.con, sid, r["specs"], r["url"] or None)
        if r["photo"]:
            core.save_stylus_photo(sid, r["photo"])
        self._changed(sid)
        if not r["url"] and QMessageBox.question(self, "Find details online", "Look up a photo and specifications for {} online?"
                                .format(r["name"])) == QMessageBox.StandardButton.Yes:
            self.lookup()

    def edit(self):
        s = self._row()
        if not s:
            return
        dlg = StylusEditor(self, s)
        if dlg.exec():
            r = dlg.result_data
            core.stylus_edit(self.con, s["id"], name=r["name"], hours=r["hours"], warn=r["warn"], used_h=r["used"],
                             installed=r["installed"])
            core.set_stylus_details(self.con, s["id"], r["specs"], r["url"])
            if r["photo"]:
                core.save_stylus_photo(s["id"], r["photo"])
            self._changed(s["id"])

    def lookup(self):
        s = self._row()
        if not s:
            return
        dlg = LookupDialog(self, s)
        if not dlg.exec():
            return
        r = dlg.result_data
        if r["photo"]:
            core.save_stylus_photo(s["id"], r["photo"])
        core.set_stylus_details(self.con, s["id"], r["specs"], r["url"])
        if r["hours"]:
            core.stylus_edit(self.con, s["id"], hours=r["hours"])
        self._changed(s["id"])

    def choose_photo(self):
        s = self._row()
        if not s:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose a photo of the cartridge", str(Path.home()),
                                              "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif)")
        if not path:
            return
        if QPixmap(path).isNull():
            QMessageBox.warning(self, "Photo", "That file couldn't be read as an image.")
            return
        core.save_stylus_photo(s["id"], Path(path).read_bytes())
        self._changed(s["id"])

    def show_plays(self):
        if self.sid:
            self.main.show_stylus_plays(self.sid)
            self.accept()


class StylusEditor(QDialog):
    """Add or edit a cartridge / stylus. Result in .result_data."""

    def __init__(self, parent, s=None, in_use=()):
        super().__init__(parent)
        new = s is None
        v = dialog_layout(self, "Fit a new cartridge / stylus" if new else "Edit cartridge / stylus", 600)
        self.resize(620, 780)
        self.result_data = None
        v.addWidget(label("Fit a new cartridge / stylus" if new else "Edit {}".format(s["name"]), "h2"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("detailInner")
        inner.setStyleSheet("QWidget#detailInner { background: transparent; }")
        scroll.setWidget(inner)
        v.addWidget(scroll, 1)
        f = QVBoxLayout(inner)
        f.setContentsMargins(0, 0, 8, 0)
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.name = QLineEdit(s["name"] if s else "")
        self.name.setPlaceholderText("Make and model, e.g. Ortofon 2M Red")
        self.hours = QDoubleSpinBox()
        self.hours.setRange(0, 100000)
        self.hours.setDecimals(0)
        self.hours.setSuffix(" h")
        self.hours.setValue(s["rated_hours"] if s else 0)
        self.used = QDoubleSpinBox()
        self.used.setRange(0, 100000)
        self.used.setDecimals(1)
        self.used.setSuffix(" h")
        self.used.setValue(round(s["initial_seconds"] / 3600, 1) if s else 0)
        self.warn = QSpinBox()
        self.warn.setRange(1, 100)
        self.warn.setSuffix(" %")
        self.warn.setValue(int(s["warn_pct"]) if s else 80)
        d = core.to_dt(s["installed_at"]).date() if s else dt.date.today()
        self.fitted = QDateEdit(QDate(d.year, d.month, d.day))
        self.fitted.setCalendarPopup(True)
        self.fitted.setDisplayFormat("dd MMM yyyy")
        self.url = QLineEdit((s["url"] or "") if s else "")
        self.url.setPlaceholderText("Paste the manufacturer's or a shop's page to fill in the details")
        self.tasks, self.closed, self.photo, self.fetched_url, self.filled = set(), False, None, None, set()
        self.url_timer = QTimer(self)
        self.url_timer.setSingleShot(True)
        self.url_timer.setInterval(800)
        self.url_timer.timeout.connect(self.read_page)
        self.url.textChanged.connect(lambda t: self.url_timer.start())
        self.url.editingFinished.connect(self.read_page)
        for w in (self.hours, self.used, self.warn, self.fitted):
            w.setMaximumWidth(170)
        frow(form, "Name", self.name)
        frow(form, "Rated life", self.hours)
        frow(form, "Hours on it already", self.used)
        frow(form, "Warn me at", self.warn)
        frow(form, "Date fitted", self.fitted)
        frow(form, "Product page", self.url)
        page_row = QHBoxLayout()
        self.pic = PixmapWidget(96)
        self.pic.setVisible(False)
        page_row.addWidget(self.pic)
        self.page_status = label("", "muted", wrap=True)
        page_row.addWidget(self.page_status, 1)
        form.addRow("", page_row)
        self.replaces = None
        if new and in_use:
            self.replaces = QComboBox()
            self.replaces.addItem("Nothing: keep my other cartridges / styli in use", None)
            for st in in_use:
                self.replaces.addItem("Replaces {} (retire it)".format(st["name"]), st["id"])
            frow(form, "This cartridge / stylus", self.replaces)
        f.addLayout(form)
        f.addWidget(label("Use the rated life from your cartridge / stylus maker. It varies a lot between stylus types.",
                          "muted", wrap=True))
        f.addSpacing(10)
        f.addWidget(label("SPECIFICATIONS (OPTIONAL)", "caption"))
        specs = core.get_specs(s) if s else {}
        sform = QFormLayout()
        sform.setSpacing(8)
        sform.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.spec_edits = {}
        for key, lab, _ in core.SPEC_FIELDS:
            e = QLineEdit(specs.get(key, ""))
            frow(sform, lab, e)
            self.spec_edits[key] = e
        self.desc = QLineEdit(specs.get("description", ""))
        frow(sform, "Notes", self.desc)
        f.addLayout(sform)
        f.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(button("Cancel", self.reject))
        save = button("Save", self.save, "primary")
        save.setDefault(True)
        row.addWidget(save)
        v.addLayout(row)

    def done(self, result):
        self.closed = True
        super().done(result)

    def _page_status(self, text, style="muted"):
        self.page_status.setText(text)
        restyle(self.page_status, style)

    def read_page(self):
        """Read the product page in the background and fill in whatever is still empty."""
        self.url_timer.stop()
        url = self.url.text().strip()
        if not url or url == self.fetched_url:
            return
        if not url.lower().startswith(("http://", "https://")):
            if "." not in url or " " in url:
                return
            url = "https://" + url
        self.fetched_url = self.url.text().strip()

        def work():
            found = core.scrape_product_page(url)
            if found.get("image_url"):
                try:
                    found["image"] = core.http_get(found["image_url"])[0]
                except core.MBError:
                    found["image"] = None
            return found

        def failed(e):
            if not self.closed and self.url.text().strip() == self.fetched_url:
                self._page_status("Couldn't read that page: {}".format(e if isinstance(e, core.MBError) else repr(e)),
                                  "warn")

        self._page_status("Reading the page...")
        run_task(work, lambda found: None if self.closed else self.fill_from_page(found), failed, self.tasks)

    def fill_from_page(self, found):
        if self.url.text().strip() != self.fetched_url:
            return  # the link was changed while we were reading
        filled = []

        def put(edit, value, key):
            # fill empty fields, and update ones this page filled before; never overwrite your own typing
            if value and (not edit.text().strip() or key in self.filled):
                edit.setText(value)
                self.filled.add(key)
                filled.append(key)

        put(self.name, core.name_from_page_title(found.get("title")), "name")
        for key, edit in self.spec_edits.items():
            put(edit, found["specs"].get(key, ""), key)
        put(self.desc, found.get("description", ""), "description")
        hours = core.suggest_hours(found["specs"])
        if hours and (self.hours.value() <= 0 or "hours" in self.filled):
            self.hours.setValue(hours)
            self.filled.add("hours")
            filled.append("hours")
        pm = QPixmap()
        if found.get("image"):
            pm.loadFromData(found["image"])
        if not pm.isNull():
            self.photo = found["image"]
            self.pic.set_pixmap(pm)
            self.pic.setVisible(True)
        n_specs = sum(1 for k in filled if k in self.spec_edits)
        bits = []
        if "name" in filled:
            bits.append("the name")
        if n_specs:
            bits.append("{} specification{}".format(n_specs, "" if n_specs == 1 else "s"))
        if "hours" in filled:
            bits.append("a rated life of {:g} h".format(hours))
        if not pm.isNull():
            bits.append("a photo")
        if bits:
            text = "Filled in {} from {}. Check them before saving.".format(
                ", ".join(bits[:-1]) + (" and " if len(bits) > 1 else "") + bits[-1], found["source"])
            self._page_status(text, "ok")
        else:
            self._page_status("Read {}, but found nothing new to fill in. Its layout may not list specs in a "
                              "table.".format(found["source"]), "warn")

    def save(self):
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "Cartridge / stylus", "Give the cartridge / stylus a name.")
            return
        if self.hours.value() <= 0:
            QMessageBox.warning(self, "Cartridge / stylus", "Enter the rated life in hours from the cartridge / stylus maker.")
            return
        day = self.fitted.date().toPyDate()
        installed = core.now_local() if day == dt.date.today() else \
            dt.datetime.combine(day, dt.time(12, 0)).astimezone()
        specs = {k: e.text().strip() for k, e in self.spec_edits.items()}
        specs["description"] = self.desc.text().strip()
        self.result_data = {"name": name, "hours": self.hours.value(), "used": self.used.value(),
                            "warn": float(self.warn.value()), "installed": installed, "specs": specs,
                            "url": self.url.text().strip(),
                            "replaces": self.replaces.currentData() if self.replaces else None,
                            "photo": self.photo}
        self.accept()


class LookupDialog(QDialog):
    """Find a cartridge photo and specs on Wikipedia or from a product page link."""

    def __init__(self, parent, s):
        super().__init__(parent)
        self.s, self.tasks, self.closed, self.busy, self.found, self.result_data = s, set(), False, False, None, None
        v = dialog_layout(self, "Find details online", 760)
        self.resize(820, 680)
        v.addWidget(label("Find details for {}".format(s["name"]), "h2"))
        v.addWidget(label("Wikipedia has photos and descriptions of well-known cartridges. For anything else, "
                          "paste a link to the manufacturer's or a shop's page for it: the photo and the "
                          "specification table are read from that page.", "muted", wrap=True))
        r1 = QHBoxLayout()
        self.q = QLineEdit(s["name"])
        self.q.returnPressed.connect(self.search)
        r1.addWidget(self.q, 1)
        self.search_btn = button("Search Wikipedia", self.search)
        r1.addWidget(self.search_btn)
        v.addLayout(r1)
        r2 = QHBoxLayout()
        self.url = QLineEdit(s["url"] or "")
        self.url.setPlaceholderText("https://... product page for this cartridge")
        self.url.returnPressed.connect(self.read_page)
        r2.addWidget(self.url, 1)
        self.read_btn = button("Read page", self.read_page)
        r2.addWidget(self.read_btn)
        v.addLayout(r2)

        res = QHBoxLayout()
        res.setSpacing(18)
        self.pic = PixmapWidget(220)
        res.addWidget(self.pic, 0, Qt.AlignmentFlag.AlignTop)
        rv = QVBoxLayout()
        self.r_title = label("", "lead", wrap=True)
        self.r_source = label("", "muted", wrap=True)
        self.r_desc = label("", "muted", wrap=True)
        self.table = table(["Specification", "Value"], 1)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        for w in (self.r_title, self.r_source, self.r_desc):
            rv.addWidget(w)
        rv.addWidget(self.table, 1)
        res.addLayout(rv, 1)
        v.addLayout(res, 1)
        self.use_photo = QCheckBox("Use this photo")
        self.use_hours = QCheckBox("")
        v.addWidget(self.use_photo)
        v.addWidget(self.use_hours)
        bottom = QHBoxLayout()
        self.status = label("", "muted", wrap=True)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(button("Cancel", self.reject))
        self.ok_btn = button("Use these details", self.use, "primary")
        bottom.addWidget(self.ok_btn)
        v.addLayout(bottom)
        self.show_result(None)
        if s["url"]:
            self.read_page()
        else:
            self.search()

    def done(self, result):
        self.closed = True
        super().done(result)

    def _run(self, fn, text):
        def work():
            found = fn()
            if found and found.get("image_url"):
                try:
                    found["image"] = core.http_get(found["image_url"])[0]
                except core.MBError:
                    found["image"] = None
            return found

        def ok(found):
            if not self.closed:
                self._busy(False)
                self.show_result(found)

        def failed(e):
            if not self.closed:
                self._busy(False)
                self._status(str(e) if isinstance(e, core.MBError) else repr(e), "warn")

        self._busy(True)
        self._status(text)
        run_task(work, ok, failed, self.tasks)

    def _busy(self, busy):
        self.busy = busy
        self.search_btn.setEnabled(not busy)
        self.read_btn.setEnabled(not busy)
        if busy:
            self.setCursor(Qt.CursorShape.BusyCursor)
        else:
            self.unsetCursor()

    def _status(self, text, style="muted"):
        self.status.setText(text)
        restyle(self.status, style)

    def search(self):
        q = self.q.text().strip()
        if q:
            self._run(lambda: core.lookup_wikipedia(q), "Searching Wikipedia...")

    def read_page(self):
        url = self.url.text().strip()
        if not url:
            self._status("Paste a link to a product page first.", "warn")
            return
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        self._run(lambda: core.scrape_product_page(url), "Reading the page...")

    def show_result(self, found):
        self.found = found
        has = bool(found)
        if found is None and not self.busy and self.status.text().startswith("Searching"):
            self._status("Wikipedia has no article on this cartridge. Paste a product page link instead.", "warn")
        pm = QPixmap()
        if found and found.get("image"):
            pm.loadFromData(found["image"])
        self.pic.set_pixmap(None if pm.isNull() else pm, "" if has else "No result yet")
        self.use_photo.setVisible(not pm.isNull())
        self.use_photo.setChecked(not pm.isNull())
        self.r_title.setText(found.get("title", "") if found else "")
        self.r_source.setText("From {}".format(found["source"]) if found else "")
        self.r_desc.setText(found.get("description", "") if found else "")
        specs = (found or {}).get("specs", {})
        self.table.setRowCount(len(core.SPEC_FIELDS))
        for i, (key, lab, _) in enumerate(core.SPEC_FIELDS):
            it = cell(lab, key)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            it.setForeground(QColor(C["muted"]))
            self.table.setItem(i, 0, it)
            self.table.setItem(i, 1, cell(specs.get(key, "")))
        hours = core.suggest_hours(specs)
        self.use_hours.setVisible(bool(hours))
        self.use_hours.setChecked(False)
        if hours:
            self.use_hours.setText("Set the rated life to {:g} h, as given on the page".format(hours))
        self.ok_btn.setEnabled(has)
        if found:
            n = sum(1 for k in specs if specs[k])
            if n:
                self._status("Found {} specification{}{}. Check them, edit any value, then click Use these details."
                             .format(n, "" if n == 1 else "s", " and a photo" if not pm.isNull() else ""))
            else:
                self._status("Found {}a description but no specifications. Paste a product page link to fill "
                             "them in, or type them into the table.".format("a photo and " if not pm.isNull() else ""))

    def use(self):
        if not self.found:
            return
        specs = {self.table.item(i, 0).data(ROLE): (self.table.item(i, 1).text() or "").strip()
                 for i in range(self.table.rowCount())}
        specs = {k: val for k, val in specs.items() if val}
        if self.found.get("description"):
            specs["description"] = self.found["description"]
        self.result_data = {"photo": self.found.get("image") if self.use_photo.isChecked() else None,
                            "specs": specs, "url": self.found.get("url"),
                            "hours": core.suggest_hours(specs) if self.use_hours.isChecked() else None}
        self.accept()


# ---------------------------------------------------------------------------
# Start-up
# ---------------------------------------------------------------------------

def app_icon():
    """Styli's icon: styli.png next to this file, else the copy installed in the icon theme."""
    path = Path(__file__).resolve().parent / "styli.png"
    if path.exists():
        icon = QIcon()
        image = QImage(str(path))
        for size in (16, 24, 32, 48, 64, 128, 256, 512):
            icon.addPixmap(QPixmap.fromImage(image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                                          Qt.TransformationMode.SmoothTransformation)))
        return icon
    return QIcon.fromTheme("styli", QIcon.fromTheme("media-optical"))


def run(con, core_module):
    global core
    core = core_module
    global SYSTEM_PALETTE
    core.private_files()  # covers, photos and caches saved from here on are yours alone
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Styli")
    app.setDesktopFileName("styli")
    app.setWindowIcon(app_icon())
    SYSTEM_PALETTE = QPalette(app.palette())  # before our theme replaces it
    apply_theme(app, core.get_setting(con, "theme", "system"), core.get_setting(con, "tint", "original"))
    win = MainWindow(con)
    hints = app.styleHints()
    if hasattr(hints, "colorSchemeChanged"):  # Qt 6.5+: follow the desktop live
        hints.colorSchemeChanged.connect(win.follow_desktop)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import styli
    styli.migrate_old_library()
    sys.exit(run(styli.connect(styli.default_db_path()), styli))
