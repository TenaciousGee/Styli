"""The Library tab: your records, their search box and sort order, and Add record."""

from PyQt6.QtWidgets import QHBoxLayout, QLineEdit, QListWidgetItem, QVBoxLayout, QWidget

from styli_app import common, db, plays
from styli_app.gui.cover_views import make_list
from styli_app.gui.dialogs.add_record import AddDialog
from styli_app.gui.widgets import LIB_SORTS, ROLE, button, label, make_splitter, sort_combo, sort_records


class LibraryTab:
    """The Library tab: your records, their search box and sort order, and Add record."""

    def _build_library(self):
        """The Library tab: search box, sort order and Add record, the record list, and the panel."""
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
        self.lib_sort = db.get_setting(self.con, "sort_library", "artist")
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

    def refresh_library(self):
        """Reload the record list in the chosen order, keeping the selection."""
        keep = self.lib_sel
        lst = self.lib_list
        lst.blockSignals(True)
        lst.clear()
        rows = self.con.execute(
            "SELECT r.*, "
            "(SELECT MAX(played_at) FROM plays p WHERE p.record_id = r.id) last "
            "FROM records r ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE").fetchall()
        selected = None
        counts = plays.play_counts(self.con)
        items = [dict(r=r, artist=r["artist"], title=r["title"], year=r["year"], added=r["added_at"], last=r["last"],
                      album=counts.get(r["id"], {}).get("album", 0), sides=counts.get(r["id"], {}).get("sides", 0))
                 for r in rows]
        for r in (i["r"] for i in sort_records(items, self.lib_sort)):
            sub = r["artist"] + ("  ·  {}".format(r["year"]) if r["year"] else "")
            item = QListWidgetItem()
            item.setData(ROLE, {
                "id": r["id"], "title": r["title"], "subtitle": sub, "cover": self.record_cover_key(r),
                "meta": common.fmt_plays(counts.get(r["id"], {}).get("album", 0), counts.get(r["id"], {}).get("sides", 0)),
                "meta2": "last " + common.to_dt(r["last"]).strftime("%d %b %Y") if r["last"] else "not played yet",
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
        """A library sort order was chosen: remember it and reload the list."""
        self.lib_sort = how
        db.set_setting(self.con, "sort_library", how)
        self.refresh_library()

    def apply_filter(self):
        """Hide records that don't match every word typed in the search box."""
        words = self.filter.text().lower().split()
        for i in range(self.lib_list.count()):
            item = self.lib_list.item(i)
            item.setHidden(bool(words) and not all(w in item.data(ROLE)["search"] for w in words))

    # ---- library actions ---------------------------------------------------
    def on_select(self, item):
        """A record was selected in the library list: show it in the panel."""
        rid = item.data(ROLE)["id"] if item is not None else None
        self.lib_sel = rid
        if self.view == "library" and rid != self.current:
            self.show_detail(rid)

    def open_add(self):
        """Open Add record, then select whatever was added."""
        dlg = AddDialog(self)
        dlg.exec()
        self.update_discogs_tab()
        if dlg.saved_id:
            self.lib_sel = dlg.saved_id
            self.filter.clear()
            self.tabs.setCurrentWidget(self.lib_page)
            self.refresh_all()
