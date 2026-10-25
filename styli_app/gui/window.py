"""The main window: the cartridge / stylus cards along the top, the tabs, and the album panel.

Each tab's code is in its own module (tab_library, album_panel, tab_discogs, tab_playlog,
tab_history, tab_stats, tab_top10) as a mixin class, combined here into MainWindow. What's left
here is shared by them all: the cards, the resizable list and panel, sortable tables, switching
tabs, refreshing after any change (refresh_all), and the theme."""

from PyQt6.QtCore import QEvent, QTimer, Qt
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QMainWindow, QProgressBar,
                             QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from styli_app import cartridges, common, covers, db
from styli_app.gui.album_panel import AlbumPanel
from styli_app.gui.cover_views import CoverWidget, PhotoStore
from styli_app.gui.dialogs.cartridges import StyliDialog
from styli_app.gui.dialogs.preferences import PrefsDialog
from styli_app.gui.tab_discogs import DiscogsTab
from styli_app.gui.tab_history import HistoryTab
from styli_app.gui.tab_library import LibraryTab
from styli_app.gui.tab_playlog import PlayLogTab
from styli_app.gui.tab_stats import StatsTab
from styli_app.gui.tab_top10 import TopTab
from styli_app.gui.tasks import CoverStore
from styli_app.gui.theme import apply_theme
from styli_app.gui.widgets import button, card, clear_layout, label, restyle


class StylusCard(QFrame):
    """One cartridge / stylus in use: photo, wear bar and hours. Click to put it on the turntable."""

    def __init__(self, main, s, current):
        super().__init__()
        self.main, self.sid = main, s["id"]
        self.setObjectName("card")
        self.setProperty("current", "true" if current else "false")
        self.setFixedSize(370, 150)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        m = cartridges.stylus_summary(main.con, s)
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
        """Clicking a card puts that cartridge / stylus on the turntable."""
        self.main.make_current(self.sid)


class MainWindow(LibraryTab, AlbumPanel, DiscogsTab, PlayLogTab, HistoryTab, StatsTab, TopTab, QMainWindow):
    """Styli's window: the cartridge / stylus cards along the top, then the tabs (Library, Discogs collection, Play
    log, History, Stats, Top 10) and the album panel beside the lists."""

    def __init__(self, con):
        super().__init__()
        self.con, self.current, self.chips = con, None, {}
        self.view, self.lib_sel, self.pending, self.pending_item, self.tasks = "library", None, None, None, set()
        self.detail_loading, self.detail_failed, self.pending_wiki, self.wiki_loading = set(), set(), {}, set()
        self.dg_items, self.dg_shown, self.dg_cache, self.dg_loading = [], [], {}, set()
        self.dg_sel, self.dg_user, self.dg_gen, self.dg_new = None, None, 0, []
        self.theme_pref = db.get_setting(con, "theme", "system")
        self.tint = db.get_setting(con, "tint", "original")
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
        """The row of cartridge / stylus cards above the tabs, with Hide cards and Manage."""
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
        self._apply_strip(db.get_setting(self.con, "strip_hidden", "0") == "1")
        return wrap

    def toggle_strip(self):
        """Hide or show the cartridge / stylus cards, and remember the choice."""
        hidden = self.strip_scroll.isVisibleTo(self.strip_scroll.parentWidget())
        db.set_setting(self.con, "strip_hidden", "1" if hidden else "0")
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
        """When the window widens, share the extra width 3:2 between the list and the panel."""
        split.setStretchFactor(0, 3)  # extra width is shared: 3 parts to the list, 2 to the panel
        if split.count() > 1:
            split.setStretchFactor(1, 2)

    def _panel_fraction(self):
        """The album panel's saved share of the width (kept between 20% and 60%)."""
        try:
            return min(0.6, max(0.2, float(db.get_setting(self.con, "panel_fraction", "0.34"))))
        except ValueError:
            return 0.34

    def _apply_panel_width(self, split):
        """Set the divider so the panel has its saved share of the width."""
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
        """The divider was dragged: save the panel's new share shortly after."""
        sizes = split.sizes()
        if len(sizes) > 1 and sum(sizes) > 0:
            self._pending_fraction = sizes[1] / float(sum(sizes))
            self.panel_save_timer.start()

    def _save_panel_width(self):
        """Save the album panel's share of the width."""
        if getattr(self, "_pending_fraction", None):
            db.set_setting(self.con, "panel_fraction", "{:.3f}".format(self._pending_fraction))

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_sized", False):
            self._sized = True
            QTimer.singleShot(0, lambda: self._apply_panel_width(self.lib_split))

    def eventFilter(self, obj, event):
        """Resize the album cover with the panel, and fit the Stats charts after a resize."""
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

    # ---- sortable tables -----------------------------------------------------------
    def _sortable(self, t, setting, unsortable=None):
        """Click a column heading to sort by it; click again to reverse. The choice is remembered."""
        col, order = 0, "desc"
        saved = db.get_setting(self.con, setting, "")
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
        """A column header was clicked: sort by it and remember the order (the Remove column doesn't sort)."""
        if col == t.property("unsortable"):  # the Remove column: keep the current sort
            t.horizontalHeader().blockSignals(True)
            t.horizontalHeader().setSortIndicator(t.property("sort_col"), Qt.SortOrder.DescendingOrder
                                                  if t.property("sort_order") == "desc" else Qt.SortOrder.AscendingOrder)
            t.horizontalHeader().blockSignals(False)
            return
        name = "desc" if order == Qt.SortOrder.DescendingOrder else "asc"
        t.setProperty("sort_col", col)
        t.setProperty("sort_order", name)
        db.set_setting(self.con, t.property("sort_setting"), "{}:{}".format(col, name))
        self._resort(t)

    def _resort(self, t):
        """Sort a table again by its saved column and direction after it's reloaded."""
        order = Qt.SortOrder.DescendingOrder if t.property("sort_order") == "desc" else Qt.SortOrder.AscendingOrder
        t.sortItems(t.property("sort_col"), order)

    # ---- refreshing --------------------------------------------------------
    def refresh_all(self):
        """Reload everything after a change: cards, lists, tables, charts and Top 10."""
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
        in_use = cartridges.styli_in_use(self.con)
        cur = cartridges.active_stylus(self.con)
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
            m = cartridges.stylus_summary(self.con, cur)
            self.strip_summary.setText("{}  ·  {} of {:g} h  ·  {:.0f}% worn".format(
                cur["name"], common.fmt_hours(m["used"]).replace(" h", ""), cur["rated_hours"], m["pct"]))
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
        """The CoverStore key for a library record's cover (artist and title let hand-entered records be matched to
        an album)."""
        return ("record", r["id"], r["mbid"], r["rgid"], 500, r["cover_url"], r["artist"], r["title"])

    def _level(self, sid=None):
        """The wear level (ok, warn or over) of a cartridge / stylus, to spot a play crossing a level."""
        s = self.con.execute("SELECT * FROM styli WHERE id = ?", (sid,)).fetchone() if sid else \
            cartridges.active_stylus(self.con)
        return cartridges.stylus_summary(self.con, s)["level"] if s else None

    def make_current(self, sid):
        """Put a cartridge / stylus on the turntable: new plays are logged against it.

        The cards stay where they are; only the highlight moves.
        """
        if not sid:
            return
        cartridges.set_current_stylus(self.con, sid)
        for i in range(self.strip_lay.count()):
            w = self.strip_lay.itemAt(i).widget()
            if isinstance(w, StylusCard):
                w.set_current(w.sid == sid)
        idx = self.stylus_combo.findData(sid)
        if idx >= 0:
            self.stylus_combo.setCurrentIndex(idx)
        self.stats_sid = None  # the Stats tab follows the turntable
        self.refresh_stats()

    def _store_rgid(self, rid, rgid):
        """Save an album id found while fetching a cover, so it isn't looked up again."""
        self.con.execute("UPDATE records SET rgid = ? WHERE id = ? AND rgid IS NULL", (rgid, rid))
        self.con.commit()

    def on_tab(self, idx):
        """A tab was opened: move the album panel there if it uses one, and load anything it needs."""
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

    def open_prefs(self):
        """Open Preferences."""
        PrefsDialog(self).exec()

    def set_theme(self, pref, save=True, tint=None):
        """Apply a theme and tint, saving them unless it's only a preview."""
        self.theme_pref = pref
        if tint is not None:
            self.tint = tint
        apply_theme(QApplication.instance(), pref, self.tint)
        self.refresh_status()
        if save:
            db.set_setting(self.con, "theme", pref)
            db.set_setting(self.con, "tint", self.tint)

    def follow_desktop(self, *args):
        """The desktop switched between light and dark: follow it if set to match the desktop."""
        if self.theme_pref == "system":
            self.set_theme("system", save=False)

    def prefetch_covers(self):
        """Queue a saved cover for every record that hasn't got one yet. Returns how many."""
        n = 0
        for r in self.con.execute("SELECT * FROM records").fetchall():
            if not covers.library_cover_path(r["id"]).exists():
                key = self.record_cover_key(r)
                self.covers.missing.discard(key)
                self.covers.get(key, priority=5)
                n += 1
        return n

    def open_styli(self):
        """Open Manage cartridges / styli, then refresh everything."""
        StyliDialog(self).exec()
        self.refresh_all()
