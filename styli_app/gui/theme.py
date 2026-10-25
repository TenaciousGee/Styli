"""Look and feel: colours and the stylesheet.

The light and dark palettes (LIGHT, DARK) can be given a tint: 16 pastel hues, pure black or
pure white, with shades checked for readable contrast. apply_theme fills C, the current colours
that every widget and chart reads as it paints, and the QSS stylesheet styles widgets by their
object name (for example QPushButton#primary)."""

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPalette, QPen

from styli_app import covers


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
    """A colour from hue (0-360), saturation and lightness (0-100) as #rrggbb, or #aarrggbb with alpha."""
    c = QColor.fromHslF((h % 360) / 360.0, max(0.0, min(1.0, s / 100.0)), max(0.0, min(1.0, l / 100.0)))
    return c.name() if alpha is None else "#{:02x}{}".format(alpha, c.name()[1:])


def _luminance(hexc):
    """How bright a colour looks to the eye (WCAG relative luminance), for contrast checks."""
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
    path = covers.cache_dir().parent / "arrow-{}{}.png".format(colour.strip("#"), "-up" if up else "")
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
    """Switch the whole app to light or dark (or the desktop's choice) with a colour tint. Returns True when the
    result is dark."""
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
