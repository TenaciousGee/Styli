#!/usr/bin/env python3
"""
vinyltrack_gui: the PyQt6 desktop window for vinyltrack.

Keep this file in the same folder as vinyltrack.py and open it with `vt gui`
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
import shutil
import sys
from pathlib import Path

from PyQt6.QtCore import (QDate, QDateTime, QObject, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool,
                          QTimer, QUrl, pyqtSignal)
from PyQt6.QtGui import (QAction, QColor, QDesktopServices, QFont, QFontMetrics, QImage, QKeySequence, QPainter,
                         QPainterPath, QPalette, QPen, QPixmap)
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QDateEdit, QDateTimeEdit, QDialog,
                             QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QComboBox, QListWidget, QListWidgetItem, QMainWindow,
                             QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSpinBox, QStackedWidget,
                             QStyle, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTabWidget,
                             QVBoxLayout, QWidget)

core = None  # the vinyltrack module, handed over by run()
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
}
LIGHT = {
    "bg": "#f3f4f7", "surface": "#ffffff", "surface2": "#f1f3f7", "surface3": "#e5e8ee",
    "border": "#d9dde5", "text": "#16191f", "muted": "#5c6472", "faint": "#9aa1ad",
    "accent": "#3f63e0", "accent_hover": "#5576ea", "accent_dim": "#dfe6fd", "on_accent": "#ffffff",
    "ok": "#1b9a5f", "warn": "#b86e00", "over": "#d23a31",
    "hover": "#d9dde5", "alt_row": "#f8f9fb", "scroll_hover": "#c3c8d2",
    "danger_bg": "#fde4e2", "danger_text": "#9b1c14", "cover_edge": "#1a000000",
}
THEMES = {"system": "Match my desktop", "light": "Light", "dark": "Dark"}
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
QPushButton#primary {{ background: {accent}; color: {on_accent}; border: none; font-weight: 700; }}
QPushButton#primary:hover {{ background: {accent_hover}; }}
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
QTableCornerButton::section {{ background: {surface}; border: none; }}
QScrollArea {{ background: transparent; border: none; }}
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
QComboBox {{ background: {surface2}; border: 1px solid {border}; border-radius: 9px; padding: 7px 10px; }}
QComboBox:focus {{ border: 1px solid {accent}; }}
QComboBox::drop-down, QDateEdit::drop-down, QDateTimeEdit::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow, QDateEdit::down-arrow, QDateTimeEdit::down-arrow {{ image: url("{arrow}");
    width: 12px; height: 8px; }}
QComboBox QAbstractItemView {{ background: {surface}; border: 1px solid {border}; outline: 0;
    selection-background-color: {accent_dim}; selection-color: {text}; padding: 4px; }}
QMessageBox {{ background: {surface}; }}
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


def arrow_image(colour):
    """A small drop-down arrow drawn for the current theme (stylesheets need an image file)."""
    path = core.cache_dir().parent / "arrow-{}.png".format(colour.strip("#"))
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
        p.drawPolyline([QPointF(4, 4), QPointF(12, 12), QPointF(20, 4)])
        p.end()
        img.save(str(path))
    return path.as_posix()


def apply_theme(app, pref="system"):
    dark = pref == "dark" or (pref == "system" and system_is_dark(app))
    C.clear()
    C.update(DARK if dark else LIGHT)
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
    app.setStyleSheet(QSS.format(arrow=arrow_image(C["muted"]), **C))
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


def cell(text, data=None):
    it = QTableWidgetItem(str(text))
    if data is not None:
        it.setData(ROLE, data)
    return it


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
        _, rid, mbid, rgid, size, cover_url = key
        return core.ensure_record_cover(rid, mbid, rgid, size, cover_url)  # saved with the library for good
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
        right_w = 150 if d.get("meta") else 0
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
        self.theme_pref = core.get_setting(con, "theme", "system")
        self.covers = CoverStore(self)
        self.covers.rgid_found.connect(self._store_rgid)
        self.photos = PhotoStore(self)
        self.setWindowTitle("Vinyl Cartridge / Stylus Tracker")
        self.resize(1260, 900)
        self.setMinimumSize(960, 620)
        page = QWidget()
        page.setObjectName("page")
        self.setCentralWidget(page)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(16)
        outer.addWidget(self._build_styli_area())
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_library(), "Library")
        self.tabs.addTab(self._build_history(), "History")
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
        head.addWidget(label("CARTRIDGES / STYLI IN USE", "caption"))
        head.addStretch(1)
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
        return wrap

    def _build_library(self):
        page = QWidget()
        h = QHBoxLayout(page)
        h.setContentsMargins(0, 14, 0, 0)
        h.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(12)
        bar = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Search your library")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.apply_filter)
        bar.addWidget(self.filter, 1)
        bar.addWidget(button("+  Add record", self.open_add, "primary"))
        left.addLayout(bar)
        self.lib_empty = label("Your library is empty. Click Add record to find an album on MusicBrainz.",
                               "muted", wrap=True)
        left.addWidget(self.lib_empty)
        self.lib_list = make_list(self.covers, 76)
        self.lib_list.currentItemChanged.connect(lambda cur, prev: self.on_select(cur))
        self.lib_list.itemDoubleClicked.connect(lambda item: self.log_btn.setFocus())
        left.addWidget(self.lib_list, 1)
        h.addLayout(left, 1)
        h.addWidget(self._build_detail())
        return page

    def _build_detail(self):
        outer = card()
        outer.setFixedWidth(380)
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
        v = QVBoxLayout(inner)
        v.setContentsMargins(22, 22, 22, 22)
        v.setSpacing(8)
        self.cover = CoverWidget(self.covers, 210)
        v.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignHCenter)
        v.addSpacing(4)
        self.d_title = label("", "h2", wrap=True)
        self.d_artist = label("", "lead", wrap=True)
        self.d_meta = label("", "muted", wrap=True)
        for w in (self.d_title, self.d_artist, self.d_meta):
            v.addWidget(w)
        acts = QHBoxLayout()
        acts.setSpacing(4)
        self.btn_edit = button("Edit", self.edit_record, "ghost")
        self.btn_cover = button("Change cover", self.set_cover, "ghost")
        self.btn_remove = button("Remove", self.remove_record, "ghost")
        for b in (self.btn_edit, self.btn_cover, self.btn_remove):
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
        self.now_cb = QCheckBox("Played just now")
        self.now_cb.setChecked(True)
        v.addWidget(self.now_cb)
        self.when = QDateTimeEdit(QDateTime.currentDateTime())
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("dd MMM yyyy  HH:mm")
        self.when.setVisible(False)
        self.now_cb.toggled.connect(lambda on: (self.when.setVisible(not on),
                                                self.when.setDateTime(QDateTime.currentDateTime())))
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
        v.addWidget(self.d_msg)
        v.addStretch(1)
        self.detail_controls = [self.btn_edit, self.btn_cover, self.btn_remove, self.now_cb, self.partial,
                                self.times, self.log_btn, self.sides_caption, self.with_caption,
                                self.stylus_combo]
        return outer

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
        self.hist.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        v.addWidget(self.hist, 1)
        bar = QHBoxLayout()
        bar.addWidget(button("Delete selected", self.delete_plays, "danger"))
        self.hist_count = label("", "muted")
        bar.addWidget(self.hist_count)
        bar.addStretch(1)
        bar.addWidget(button("Export CSV", self.export_csv))
        v.addLayout(bar)
        return page

    # ---- refreshing --------------------------------------------------------
    def refresh_all(self):
        self.refresh_status()
        self.refresh_library()
        self.refresh_history()

    def refresh_status(self):
        """Rebuild the row of cartridge / stylus cards and the 'playing with' picker."""
        bar = self.strip_scroll.horizontalScrollBar()
        scroll_pos = bar.value()
        while self.strip_lay.count():
            w = self.strip_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
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
        return ("record", r["id"], r["mbid"], r["rgid"], 500, r["cover_url"])

    def refresh_library(self):
        keep = self.current
        lst = self.lib_list
        lst.blockSignals(True)
        lst.clear()
        rows = self.con.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM plays p WHERE p.record_id = r.id) n, "
            "(SELECT MAX(played_at) FROM plays p WHERE p.record_id = r.id) last "
            "FROM records r ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE").fetchall()
        selected = None
        for r in rows:
            sub = r["artist"] + ("  ·  {}".format(r["year"]) if r["year"] else "")
            item = QListWidgetItem()
            item.setData(ROLE, {
                "id": r["id"], "title": r["title"], "subtitle": sub, "cover": self.record_cover_key(r),
                "meta": "{} play{}".format(r["n"], "" if r["n"] == 1 else "s"),
                "meta2": "last " + core.to_dt(r["last"]).strftime("%d %b %Y") if r["last"] else "not played yet",
                "search": "{} {}".format(r["artist"], r["title"]).lower()})
            lst.addItem(item)
            if r["id"] == keep:
                selected = item
        lst.blockSignals(False)
        self.apply_filter()
        self.lib_empty.setVisible(not rows)
        if selected is not None and not selected.isHidden():
            lst.setCurrentItem(selected)
            lst.scrollToItem(selected)
            self.show_detail(keep, keep_msg=True)
        else:
            lst.setCurrentItem(None)
            self.show_detail(None)

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
        self.tabs.setCurrentIndex(1)
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
            t.setItem(i, 0, cell(core.to_dt(p["played_at"]).strftime("%d %b %Y  %H:%M"), p["id"]))
            t.setItem(i, 1, cell(p["record_label"]))
            t.setItem(i, 2, cell(p["side"]))
            t.setItem(i, 3, cell(core.fmt_dur(p["seconds"])))
            t.setItem(i, 4, cell(p["stylus"] or ""))
        total = self.con.execute("SELECT COUNT(*), COALESCE(SUM(seconds), 0) FROM plays p {}".format(where),
                                 params).fetchone()
        self.hist_count.setText("{} entries, {} in total{}".format(
            total[0], core.fmt_hours(total[1]), " (latest 2000 shown)" if total[0] > 2000 else ""))

    # ---- library actions ---------------------------------------------------
    def on_select(self, item):
        rid = item.data(ROLE)["id"] if item is not None else None
        if rid != self.current:
            self.current = rid
            self.show_detail(rid)

    def show_detail(self, rid, keep_msg=False):
        self.current = rid
        while self.chips_grid.count():
            w = self.chips_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.chips = {}
        if not keep_msg:
            self.d_msg.setText("")
        rec = self.con.execute("SELECT * FROM records WHERE id = ?", (rid,)).fetchone() if rid else None
        for w in self.detail_controls:
            w.setVisible(rec is not None)
        self.when.setVisible(rec is not None and not self.now_cb.isChecked())
        if not rec:
            self.cover.show_cover(None, "")
            self.d_title.setText("Select a record")
            self.d_artist.setText("")
            self.d_meta.setText("Pick an album from your library to log a play.")
            return
        self.cover.show_cover(self.record_cover_key(rec), rec["title"])
        self.d_title.setText(rec["title"])
        self.d_artist.setText(rec["artist"])
        self.d_meta.setText("   ·   ".join(x for x in (rec["year"], rec["format"]) if x))
        for i, s in enumerate(core.record_sides(self.con, rid)):
            chip = QPushButton("Side {}    {}".format(s["label"], core.fmt_dur(s["seconds"])))
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.setChecked(True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            if s["missing"]:
                chip.setToolTip("Some track lengths were missing, so this may be short. Use Edit to correct it.")
            self.chips_grid.addWidget(chip, i // 2, i % 2)
            self.chips[s["label"]] = chip
        self.log_btn.setEnabled(bool(self.chips))

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

    def do_log(self):
        if self.current is None:
            return
        chosen = [k for k, c in self.chips.items() if c.isChecked()]
        if not chosen:
            self.d_msg.setText("Choose at least one side.")
            restyle(self.d_msg, "warn")
            return
        try:
            if self.now_cb.isChecked():
                when = core.now_local()
            else:
                when = self.when.dateTime().toPyDateTime().astimezone().replace(microsecond=0)
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
        self.now_cb.setChecked(True)
        msg = "Logged side{} {}{} ({}){}.".format("s" if len(chosen) > 1 else "", " + ".join(chosen),
                                                    " x{}".format(times) if times > 1 else "", core.fmt_dur(total),
                                                    " with {}".format(stylus["name"]) if stylus else "")
        if not stylus:
            msg += " No cartridge / stylus is set up, so it isn't counted against one."
        self.refresh_all()
        self.d_msg.setText(msg)
        restyle(self.d_msg, "ok")
        after = self._level(sid)
        if stylus and after != before and after in ("warn", "over"):
            QMessageBox.warning(self, "Cartridge / stylus wear", core.stylus_alert(
                self.con, self.con.execute("SELECT * FROM styli WHERE id = ?", (stylus["id"],)).fetchone()))

    def _record(self):
        return self.con.execute("SELECT * FROM records WHERE id = ?", (self.current,)).fetchone() \
            if self.current else None

    def edit_record(self):
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
        self.current = None
        self.refresh_all()

    def _store_rgid(self, rid, rgid):
        self.con.execute("UPDATE records SET rgid = ? WHERE id = ? AND rgid IS NULL", (rgid, rid))
        self.con.commit()

    def open_add(self):
        dlg = AddDialog(self)
        dlg.exec()
        if dlg.saved_id:
            self.current = dlg.saved_id
            self.filter.clear()
            self.refresh_all()

    def open_prefs(self):
        PrefsDialog(self).exec()

    def set_theme(self, pref, save=True):
        self.theme_pref = pref
        apply_theme(QApplication.instance(), pref)
        self.refresh_status()
        if save:
            core.set_setting(self.con, "theme", pref)

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
        pc.setFixedWidth(320)
        pv = QVBoxLayout(pc)
        pv.setContentsMargins(20, 20, 20, 20)
        pv.setSpacing(8)
        self.pcover = CoverWidget(self.covers, 278)
        pv.addWidget(self.pcover, 0, Qt.AlignmentFlag.AlignHCenter)
        pv.addSpacing(6)
        self.ptitle = label("", "h2", wrap=True)
        self.psub = label("", "muted", wrap=True)
        pv.addWidget(self.ptitle)
        pv.addWidget(self.psub)
        pv.addStretch(1)
        body.addWidget(pc)
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

    def _preview(self, key, title, sub, seed=""):
        self.pcover.show_cover(key, seed or title)
        self.ptitle.setText(title)
        self.psub.setText(sub)

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
                              x for x in (year, " + ".join(types)) if x)))
        self._buttons()

    def open_album(self, g):
        # Thumbnails still waiting to download would only slow this down, so drop them.
        self.covers.cancel_queued()
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
        self._preview(("pressing", r["id"], rgid, 500), r.get("title", ""), "\n".join(lines))
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
                                             core.media_summary(rel.get("media")), new_sides, rgid)
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
            self._preview(("url", i["cover"], 500) if i["cover"] else None, i["title"], "\n".join(lines))
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
                                             info["rgid"], info["discogs_id"], info["cover_url"])
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
            core.set_setting(self.main.con, "discogs_token", token)
            core.set_setting(self.main.con, "discogs_user", user)
            self.user = user
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
        self.original = main.theme_pref
        v = dialog_layout(self, "Preferences", 560)
        self.resize(600, 780)
        v.addWidget(label("Preferences", "h2"))

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
        v.addLayout(bottom)

    def _show_discogs(self):
        token, user = core.discogs_account(self.main.con)
        self.dg_status.setText("Linked as {}. Add record can show your Discogs collection.".format(user) if token else
                               "Not linked. Link your account to pick records from your Discogs collection.")
        self.dg_link_btn.setText("Use a different token" if token else "Link Discogs account")
        self.dg_unlink_btn.setVisible(bool(token))

    def link_discogs(self):
        if DiscogsLinkDialog(self, self.main).exec():
            self._show_discogs()

    def unlink_discogs(self):
        if QMessageBox.question(self, "Unlink Discogs", "Unlink your Discogs account? The access token is removed "
                                "from this computer. Records you've already added stay in your library."
                                ) == QMessageBox.StandardButton.Yes:
            core.discogs_unlink(self.main.con)
            self._show_discogs()

    def _choice(self):
        b = self.group.checkedButton()
        return b.property("key") if b else "system"

    def preview(self, btn, checked):
        if checked:
            self.main.set_theme(self._choice(), save=False)

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
        self.main.set_theme(self._choice(), save=True)
        self.accept()

    def reject(self):
        self.main.set_theme(self.original, save=False)
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

def run(con, core_module):
    global core
    core = core_module
    global SYSTEM_PALETTE
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Vinyl Cartridge / Stylus Tracker")
    app.setDesktopFileName("vinyltrack")
    SYSTEM_PALETTE = QPalette(app.palette())  # before our theme replaces it
    apply_theme(app, core.get_setting(con, "theme", "system"))
    win = MainWindow(con)
    hints = app.styleHints()
    if hasattr(hints, "colorSchemeChanged"):  # Qt 6.5+: follow the desktop live
        hints.colorSchemeChanged.connect(win.follow_desktop)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import vinyltrack
    sys.exit(run(vinyltrack.connect(vinyltrack.default_db_path()), vinyltrack))
