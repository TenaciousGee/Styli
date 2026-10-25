"""The Discogs collection tab: your collection (downloaded in pages, then kept locally), and a
record picked from it shown ready to log or add to your library."""

import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QListWidgetItem, QMessageBox, QVBoxLayout, QWidget

from styli_app import common, db, discogs, library, plays, web
from styli_app.gui.cover_views import make_list
from styli_app.gui.dialogs.sides_editor import SidesEditor
from styli_app.gui.tasks import run_task
from styli_app.gui.widgets import (LIB_SORTS, ROLE, breakable, button, credit_label, label, make_splitter,
                                   restyle, show_credit, sort_combo, sort_records)


class DiscogsTab:
    """The Discogs collection tab: your collection, and records from it shown ready to log or add."""

    # ---- Discogs tab -----------------------------------------------------------
    def _build_discogs(self):
        """The Discogs collection tab: filter, sort and refresh, the collection list, and the panel."""
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
        self.dg_sort = db.get_setting(self.con, "sort_discogs", "artist")
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
        self.dg_credit = credit_label()
        left.addWidget(self.dg_credit)
        leftw.setMinimumWidth(380)
        self.dg_split.addWidget(leftw)
        self.dg_fetch_timer = QTimer(self)
        self.dg_fetch_timer.setSingleShot(True)
        self.dg_fetch_timer.setInterval(350)  # wait until you stop moving through the list
        self.dg_fetch_timer.timeout.connect(self.fetch_dg_sides)
        return page

    def update_discogs_tab(self):
        """Show the Discogs tab while an account is linked, between Library and History."""
        token, user = discogs.discogs_account(self.con)
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

    def load_dg_collection(self, refresh=False):
        """Show the saved copy of the collection straight away, then refresh it from Discogs if it's old."""
        token, user = discogs.discogs_account(self.con)
        if not token:
            return
        self.dg_user = user
        if not refresh:
            items, age = discogs.load_discogs_cache(user)
            if items is not None:
                self.dg_items = items
                self.fill_dg_list()
                if age < discogs.DISCOGS_STALE_HOURS:
                    self._dg_count_status()
                    return
        self.dg_gen += 1
        gen, self.dg_new = self.dg_gen, []
        self.dg_refresh_btn.setEnabled(False)
        self._dg_status("Reading your Discogs collection...")
        run_task(lambda: discogs.discogs_collection_page(token, user, 1), lambda v: self._dg_page(v, gen, 1),
                 self._dg_failed, self.tasks)

    def _dg_page(self, val, gen, page):
        """One page of the Discogs collection arrived: keep it and ask for the next, or finish."""
        if gen != self.dg_gen:
            return
        batch, pages, total = val
        self.dg_new.extend(batch)
        token, user = discogs.discogs_account(self.con)
        if not token:
            return
        if page >= pages or not batch:
            self.dg_items = self.dg_new
            discogs.save_discogs_cache(user, self.dg_items)
            self.dg_refresh_btn.setEnabled(True)
            self.fill_dg_list()
            self._dg_count_status()
            return
        if page == 1 and not self.dg_items:
            self.dg_items = list(self.dg_new)
            self.fill_dg_list()
        self._dg_status("Reading your Discogs collection: {} of {}...".format(len(self.dg_new), total))
        run_task(lambda: discogs.discogs_collection_page(token, user, page + 1), lambda v: self._dg_page(v, gen, page + 1),
                 self._dg_failed, self.tasks)

    def _dg_failed(self, e):
        """Downloading the Discogs collection failed: say why."""
        self.dg_refresh_btn.setEnabled(True)
        self._dg_status(str(e) if isinstance(e, web.MBError) else repr(e), "warn")

    def _dg_status(self, text, style="muted"):
        """Set the message under the Discogs list."""
        self.dg_status.setText(text)
        restyle(self.dg_status, style)

    def _dg_count_status(self):
        """Show how many records are in the collection and how many the filter shows."""
        _, user = discogs.discogs_account(self.con)
        n = len(self.dg_items)
        shown = len(self.dg_shown)
        text = "{} record{} in {}'s Discogs collection".format(n, "" if n == 1 else "s", user)
        if shown != n:
            text += ", {} shown".format(shown)
        show_credit(self.dg_credit, discogs.discogs_collection_url(user) if user and n else None)
        self._dg_status(text + ". Use Add to library, or log a play, to add a record to your library.")

    def _dg_play_info(self):
        """Discogs release number -> (record id, play counts) for records already in the library."""
        counts = plays.play_counts(self.con)
        rows = self.con.execute("SELECT id, discogs_id FROM records WHERE discogs_id IS NOT NULL").fetchall()
        return {r["discogs_id"]: (r["id"], counts.get(r["id"], {"album": 0, "sides": 0, "last": None})) for r in rows}

    def _dg_row(self, n, i, lib):
        """The list row for a collection item (marked when it's already in your library)."""
        fmt = i["format"].split(";")[0]
        if i["id"] in lib:
            c = lib[i["id"]][1]
            meta2 = common.fmt_plays(c["album"], c["sides"]) if c["sides"] else "In your library"
        else:
            meta2 = i["label"]
        return {"i": n, "title": i["title"], "cover": ("url", i["cover"], 250) if i["cover"] else None,
                "subtitle": i["artist"] + ("  ·  " + i["year"] if i["year"] else ""),
                "meta": fmt if len(fmt) <= 24 else fmt[:23] + "...", "meta2": meta2}

    def _set_dg_sort(self, how):
        """A Discogs sort order was chosen: remember it and refill the list."""
        self.dg_sort = how
        db.set_setting(self.con, "sort_discogs", how)
        self.fill_dg_list()

    def fill_dg_list(self):
        """Fill the Discogs list, filtered and sorted, keeping the selection."""
        infos = [discogs.discogs_item_info(it) for it in self.dg_items]
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
        """The collection item selected in the Discogs list, or None."""
        item = self.dgl.currentItem()
        if item is None or not self.dg_shown:
            return None
        return self.dg_shown[item.data(ROLE)["i"]]

    def on_dg_select(self, keep_msg=False):
        """A Discogs record was selected: show it from the library if it's there, otherwise fetch its sides and
        show it ready to add or log."""
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
        token, _ = discogs.discogs_account(self.con)
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
                self.d_msg.setText("Couldn't get the track list: {}".format(str(e) if isinstance(e, web.MBError)
                                                                             else repr(e)))
                restyle(self.d_msg, "warn")

        run_task(lambda: discogs.prepare_discogs_release(token, did), done, failed, self.tasks)

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
        show_credit(self.d_credit, discogs.discogs_release_url(i["id"], (info or {}).get("about")))
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
        """Check or correct a Discogs record's sides before it's added."""
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
        """Add the shown Discogs record to the library without logging a play."""
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
        rid = library.save_record(self.con, info["mbid"], info["artist"], info["title"], info["year"], info["format"],
                               info["sides"], info["rgid"], info["discogs_id"], info["cover_url"],
                               info.get("tracks"), about or None)
        self.pending, self.current = None, rid
        return rid
