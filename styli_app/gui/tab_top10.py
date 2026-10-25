"""The Top 10 tab: the most played albums as a grid of covers that flip over to show each album's
stats.

TopGrid lays them out up to five across, wrapping onto more rows as the window narrows; only one
is turned over at a time. The grid fits its height just after each resize, never during one,
which would start another resize."""

import math

from PyQt6.QtCore import QEasingCurve, QRectF, QSize, QTimer, QVariantAnimation, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from styli_app import common, db, stats
from styli_app.gui.cover_views import draw_cover
from styli_app.gui.theme import C
from styli_app.gui.widgets import label


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
        """Animation step: how far the cover has turned (0 = cover, 1 = stats)."""
        self.p = float(v)
        self.update()

    def set_flipped(self, on, animate=True):
        """Turn the cover over to show the stats (or back), animated unless asked not to be."""
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
        """A click turns the cover over (TopGrid makes sure only one is turned)."""
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
        """Draw the tile: squashed sideways as it turns, the front or back depending on how far it has turned, then
        the title and artist underneath."""
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
        """The front: the album cover with its rank in a badge."""
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
        """The back: the album's stats, as many rows as fit, and its plays over the last 12 months."""
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
                ("Time listened", common.fmt_hours(s["seconds"])), ("Last played", date(s["last"]))]
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
        """Replace the albums shown (all of them show their covers to start)."""
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
        """How many albums fit across: as many 200 pixel tiles as fit, up to five."""
        width = self.width() if width is None else width
        return max(1, min(self.MAX_COLS, int((width + self.GAP) // (self.MIN_TILE + self.GAP))))

    def _side(self, width, n):
        """The width of each tile for n columns."""
        return max(40, int((width - self.GAP * (n - 1)) / n))

    def heightForWidth(self, width):
        """The height the grid needs at a given width (every row, with captions and gaps)."""
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
        """Place the tiles for the new width."""
        super().resizeEvent(event)
        self.relayout()


class TopTab:
    """The Top 10 tab: the most played albums as a grid of covers."""

    # ---- top 10: the most played albums as a grid of covers ----------------------
    def _build_top(self):
        """The Top 10 tab: period picker and the grid of album covers."""
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
        for key, name, _ in stats.TOP_PERIODS:
            self.top_period.addItem(name, key)
        self.top_period.setCurrentIndex(max(0, self.top_period.findData(db.get_setting(self.con, "top_period", "all"))))
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
        """A Top 10 period was chosen: remember it and redraw."""
        db.set_setting(self.con, "top_period", self.top_period.itemData(i))
        self.refresh_top(force=True)

    def refresh_top(self, force=False):
        """Rebuild the Top 10 (straight away if it's open, otherwise when it's next opened)."""
        if self.tabs.currentWidget() is not self.top_page and not force:
            self.top_dirty = True
            return
        self.top_dirty = False
        key = self.top_period.currentData() or "all"
        name = self.top_period.currentText()
        since = stats.period_since(key)
        top = stats.top_albums(self.con, 10, since)
        tiles = [AlbumTile(self.covers, i, t, stats.album_stats(self.con, t["record"]["id"], since),
                           self.record_cover_key(t["record"]), name)
                 for i, t in enumerate(top, 1)]
        self.top_grid.set_tiles(tiles)
        self.top_empty.setText("" if top else ("Nothing played in that time yet." if since else
                               "Log some plays and your most played albums will appear here."))
        self.top_empty.setVisible(not top)
