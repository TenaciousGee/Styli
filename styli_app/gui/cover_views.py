"""Showing covers and photos: covers drawn with rounded corners (or a record, or a cartridge, when
there isn't one, coloured from the title so each looks different), the album panel's cover, the
rows of the record lists, cartridge / stylus photos and the hours-by-month bars."""

import datetime as dt
import hashlib

from PyQt6.QtCore import QObject, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QAbstractItemView, QListWidget, QStyle, QStyledItemDelegate, QWidget

from styli_app import cartridges
from styli_app.gui.theme import C
from styli_app.gui.widgets import ROLE


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


class CoverWidget(QWidget):
    """A square that shows one album cover, or a drawn record while there isn't one."""

    def __init__(self, store, size, placeholder="vinyl", fit=False):
        super().__init__()
        self.store, self.key, self.seed, self.placeholder, self.fit = store, None, "", placeholder, fit
        self.setFixedSize(size, size)
        store.changed.connect(self.update)

    def show_cover(self, key, seed=""):
        """Show the cover for key (a CoverStore key); seed picks the placeholder's colours."""
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
        """Draw one row: rounded highlight, cover, title and subtitle, and two lines on the right."""
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
    """A list of records drawn by RowDelegate, rows height pixels tall."""
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
        """A key for a cartridge / stylus photo that changes when the photo file changes."""
        path = cartridges.stylus_photo_path(sid)
        return ("stylus", sid, path.stat().st_mtime if path.exists() else 0)

    def get(self, key, priority=0):
        """The photo for key, loaded from disk the first time it's asked for."""
        if not key or not key[2]:
            return None
        pm = self.cache.get(key)
        if pm is None:
            pm = self.cache[key] = QPixmap(str(cartridges.stylus_photo_path(key[1])))
        return None if pm.isNull() else pm

    def loading(self, key):
        """Photos load straight from disk, so never 'loading' (matches CoverStore's interface)."""
        return False

    def updated(self):
        """A photo was added or changed: repaint everything showing one."""
        self.changed.emit()


class PixmapWidget(QWidget):
    """Shows a QPixmap you hand it (used for lookup previews)."""

    def __init__(self, size, placeholder="cartridge"):
        super().__init__()
        self.pm, self.placeholder, self.note = None, placeholder, ""
        self.setFixedSize(size, size)

    def set_pixmap(self, pm, note=""):
        """Show an image, or a note when there isn't one."""
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
        """Show the last 12 of (month, seconds) as bars; hidden when there's nothing played."""
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
