"""The Stats tab: four summary boxes and three charts for one cartridge / stylus.

Each chart sits in a ChartCard; ReorderBox holds the cards in a column. Drag a card by its
dotted handle, or use its arrows, and the new order is saved."""

from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout,
                             QWidget)

from styli_app import cartridges, common, db, stats
from styli_app.gui.charts import (CumulativeChart, HeatmapChart, PeriodChart, fmt_measure, measure_value,
                                  period_label)
from styli_app.gui.theme import C, arrow_image
from styli_app.gui.widgets import ElidedLabel, button, card, label, restyle


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


class ChartCard(QFrame):
    """One chart on the Stats tab in its card: drag handle, title, up and down arrows, a one-line explanation and
    the chart itself."""

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
        """Add a chart card at the bottom."""
        self.cards.append(card)
        self.lay.addWidget(card, card.chart.preferred)
        self._refresh_buttons()

    def order(self):
        """The cards' keys from top to bottom (saved as the stats_order setting)."""
        return [c.key for c in self.cards]

    def set_order(self, keys):
        """Put the cards in a saved order (unknown keys go last)."""
        rank = {k: i for i, k in enumerate(keys)}
        self.cards.sort(key=lambda c: rank.get(c.key, len(rank)))
        self._relayout()

    def _relayout(self):
        """Re-add the cards in their current order, each stretching in proportion to its chart."""
        for c in self.cards:
            self.lay.removeWidget(c)
        for c in self.cards:
            self.lay.addWidget(c, c.chart.preferred)  # extra height is shared in proportion
        self._refresh_buttons()

    def _refresh_buttons(self):
        """Arrow icons in the theme's colours; the top card can't go up or the bottom one down."""
        up, down = QIcon(arrow_image(C["muted"], up=True)), QIcon(arrow_image(C["muted"]))
        for i, c in enumerate(self.cards):
            c.up.setIcon(up)
            c.down.setIcon(down)
            c.up.setEnabled(i > 0)
            c.down.setEnabled(i < len(self.cards) - 1)

    def move_card(self, card, index):
        """Move a card to a new position, and tell the window so the order is saved."""
        old = self.cards.index(card)
        index = max(0, min(len(self.cards) - 1, index))
        if index == old:
            return
        self.cards.insert(index, self.cards.pop(old))
        self._relayout()
        self.moved.emit(self.order())

    def step(self, card, delta):
        """Move a card one place up (-1) or down (+1)."""
        self.move_card(card, self.cards.index(card) + delta)

    def start_drag(self, card):
        """Start dragging a card: a faded picture of it follows the pointer until it's dropped."""
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
        """The accent line showing where a dragged card will land."""
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
        """A card was dropped: move it to where the marker showed."""
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


class StatsTab:
    """The Stats tab: summary boxes and the three reorderable charts for one cartridge / stylus."""

    # ---- stats: lifetime charts and the weekly heatmap ----------------------------
    def _build_stats(self):
        """The Stats tab: cartridge / stylus picker, sides or hours, grouping, four summary boxes and the three
        reorderable charts, all fitted to the window height."""
        self.stats_sid = None  # None: follow the cartridge / stylus on the turntable
        self.stats_dirty = True
        self.stats_measure = db.get_setting(self.con, "stats_measure", "sides")
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
        self.st_unit.setCurrentIndex(max(0, self.st_unit.findData(db.get_setting(self.con, "stats_unit", ""))))
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
        self.st_charts.set_order(db.get_setting(self.con, "stats_order", "lifetime,periods,heatmap").split(","))
        self.st_charts.moved.connect(lambda order: db.set_setting(self.con, "stats_order", ",".join(order)))
        return page

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
        """A cartridge / stylus was picked on the Stats tab (picking the one on the turntable means 'follow the
        turntable' again)."""
        sid = self.st_combo.itemData(i)
        cur = cartridges.active_stylus(self.con)
        self.stats_sid = None if cur and sid == cur["id"] else sid
        self.refresh_stats(force=True)

    def _stats_measure(self, b):
        """Sides played or Hours was chosen: remember it and redraw the charts."""
        self.stats_measure = b.property("key")
        db.set_setting(self.con, "stats_measure", self.stats_measure)
        self.refresh_stats(force=True)

    def _stats_unit(self, i):
        """Grouping by day, week, month or automatically was chosen: remember it and redraw."""
        db.set_setting(self.con, "stats_unit", self.st_unit.itemData(i) or "")
        self.refresh_stats(force=True)

    def refresh_stats(self, force=False):
        """Redraw the Stats tab (straight away if it's open, otherwise when it's next opened)."""
        if self.tabs.currentWidget() is not self.stats_page and not force:
            self.stats_dirty = True
            return
        self.stats_dirty = False
        self.st_charts._refresh_buttons()  # arrow colours follow the theme
        rows = self.con.execute("SELECT * FROM styli ORDER BY active DESC, id DESC").fetchall()
        cur = cartridges.active_stylus(self.con)
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
        t = stats.stylus_timeline(self.con, s, self.st_unit.currentData() or None)
        grid = stats.play_heatmap(self.con, sid)
        m = self.stats_measure
        self.st_empty.setVisible(False)
        self.st_body.setVisible(True)
        unit = t["unit"]
        if not t["sides"]:
            self.st_empty.setText("Nothing has been played with {} yet. Its charts fill in as you log plays.".format(s["name"]))
            self.st_empty.setVisible(True)
        summary = cartridges.stylus_summary(self.con, s)
        tiles = [("SIDES PLAYED", str(t["sides"]), "since {}".format(t["installed"].strftime("%d %b %Y").lstrip("0"))),
                 ("HOURS", common.fmt_hours(t["seconds"]),
                  "{:.0f}% of its {:g} h rated life".format(summary["pct"], s["rated_hours"]) +
                  (" (includes {} from before)".format(common.fmt_hours(t["initial"])) if t["initial"] else ""))]
        busiest = max(t["buckets"], key=lambda b: measure_value(m, b["sides"], b["seconds"])) if t["buckets"] else None
        if busiest and busiest["sides"]:
            tiles.append(("BUSIEST " + unit.upper(), fmt_measure(m, measure_value(m, busiest["sides"], busiest["seconds"])),
                          period_label(busiest["start"], unit, long=True)))
        else:
            tiles.append(("BUSIEST " + unit.upper(), "None yet", ""))
        gap = t["gap"]
        if gap:
            ongoing = gap[2] >= common.now_local().date() and not s["retired_at"]
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
        fav = stats.favourite_time(grid)
        self.st_heat_cap.setText("WHEN YOU PLAY")
        subs["heatmap"] = (("You play most on {} {}s. ".format(fav[0], fav[1]) if fav else "") +
                           "Each square is one hour of the week, going by the time each play was logged for.")
        self.st_subs = subs
        self._fit_stats(force=True)
        self.st_cum.set_data(t, m)
        self.st_per.set_data(t, m)
        self.st_heat.set_data(grid if t["sides"] else None, m)
        self.st_heat.empty = "No plays to show yet."
