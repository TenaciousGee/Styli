"""Add record: search MusicBrainz by artist and album or barcode, then pick your pressing; or pick a
record from your Discogs collection. A preview shows the cover and track listing as you go."""

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QHBoxLayout, QLineEdit,
                             QListWidgetItem, QMessageBox, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from styli_app import details, discogs, library, musicbrainz, web
from styli_app.gui.cover_views import CoverWidget, make_list
from styli_app.gui.dialogs.discogs_link import DiscogsLinkDialog
from styli_app.gui.dialogs.sides_editor import SidesEditor
from styli_app.gui.tasks import run_task
from styli_app.gui.widgets import (ROLE, breakable, button, card, cell, credit_label, dialog_layout,
                                   fill_track_grid, label, make_track_grid, restyle, show_credit, table)


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
        self.dg_credit = credit_label()
        l2.addWidget(self.dg_credit)
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
        self.pcredit = credit_label()  # shown for Discogs records only
        pv.addWidget(self.ptitle)
        pv.addWidget(self.psub)
        pv.addWidget(self.pcredit)
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
        """Mark the dialog closed so background results that arrive later are ignored."""
        self.closed = True
        self.press_gen += 1
        self.covers.cancel_queued()
        super().done(result)

    def _task(self, fn, ok):
        """Run fn in the background and call ok with the result, unless the dialog has closed by then."""
        def alive(cb):
            return lambda val: None if self.closed else cb(val)
        run_task(fn, alive(ok), alive(self._failed), self.tasks)

    def _set_status(self, text, style="muted"):
        """Set the message at the bottom of the dialog."""
        self.status.setText(text)
        restyle(self.status, style)

    def _set_busy(self, busy, text=None):
        """Show or clear the busy state (pointer, message and buttons)."""
        self.busy = busy
        if busy:
            self.setCursor(Qt.CursorShape.BusyCursor)
        else:
            self.unsetCursor()
        if text is not None:
            self._set_status(text)
        self._buttons()

    def _buttons(self):
        """Enable and label the buttons to suit the current step."""
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
        """A lookup failed: say why."""
        self._set_busy(False, "")
        QMessageBox.warning(self, "Discogs" if isinstance(e, discogs.DiscogsError) else "MusicBrainz",
                            str(e) if isinstance(e, web.MBError) else repr(e))

    def _preview(self, key, title, sub, seed="", tracks=None, credit=None):
        """Show a cover, title and details (and tracks, when known) in the preview panel.
        credit is the discogs.com page for a Discogs record's credit line."""
        self.pcover.show_cover(key, seed or title)
        self.ptitle.setText(breakable(title))
        self.psub.setText(breakable(sub))
        show_credit(self.pcredit, credit)
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
        """Show a track listing in the preview panel, with where it came from."""
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
        """Fetch the track listing of the album or pressing being previewed, in the background."""
        key = self.track_key
        if key is None or key in self.track_cache or key in self.track_loading:
            return
        kind, ident = key
        token, _ = discogs.discogs_account(self.con)
        self.track_loading.add(key)

        def work():
            if kind == "album":
                rel = musicbrainz.album_sample_release(ident)
                if not rel:
                    return [], {}, "", None
                bits = [x for x in (rel.get("date", "")[:4], rel.get("country"), musicbrainz.media_summary(rel.get("media"))) if x]
                return musicbrainz.release_tracklist(rel) + ("From the {} release. Pick your pressing to see its own."
                                                      .format(" ".join(bits)) if bits else "", None)
            if kind == "release":
                rel = musicbrainz.fetch_release(ident)
                return musicbrainz.release_tracklist(rel) + ("", rel)
            rel = discogs.discogs_get("/releases/{}".format(int(ident)), token)
            return musicbrainz.release_tracklist(discogs.discogs_to_release(rel)) + ("", None)

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
                    str(e) if isinstance(e, web.MBError) else repr(e)))

        run_task(work, lambda v: None if self.closed else done(v),
                 lambda e: None if self.closed else failed(e), self.tasks)

    # ---- searching -----------------------------------------------------------
    def search(self):
        """Search MusicBrainz by barcode, or by artist and album."""
        artist, title, bc = self.artist.text().strip(), self.title.text().strip(), self.barcode.text().strip()
        if bc:
            self._set_busy(True, "Looking up the barcode...")
            self._task(lambda: musicbrainz.search_releases(barcode=bc, vinyl=False, limit=25)[0], self._got_barcode)
            return
        if not artist and not title:
            self._set_status("Type the artist and album, or just the artist to list their albums.", "warn")
            return
        self._set_busy(True, "Searching MusicBrainz...")
        self._task(lambda: musicbrainz.search_albums(artist or None, title or None),
                   lambda res: self._got_albums(res, bool(title)))

    def _got_albums(self, albums, had_title):
        """Albums found: list them with their covers."""
        self.albums = albums
        lst = self.album_list
        lst.blockSignals(True)
        lst.clear()
        for i, g in enumerate(albums):
            types = [g.get("primary-type") or "Other"] + list(g.get("secondary-types") or [])
            year = (g.get("first-release-date") or "")[:4]
            item = QListWidgetItem()
            item.setData(ROLE, {"i": i, "title": g.get("title", ""), "cover": ("release-group", g["id"], 250),
                                "subtitle": musicbrainz.album_artist(g) + ("  ·  " + year if year else ""),
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
        """Back to the album list."""
        self.mode = self.mb_mode = "albums"
        self.press_gen += 1  # stop loading pressings of an album we've left
        self.stack.setCurrentIndex(0)
        self.preview_album()
        self._buttons()

    def preview_album(self):
        """Preview the selected album: cover, details and, shortly after, its tracks."""
        item = self.album_list.currentItem()
        if item is None:
            self._preview(None, "", "Album art appears here when you pick an album.")
        else:
            g = self.albums[item.data(ROLE)["i"]]
            types = [g.get("primary-type") or "Other"] + list(g.get("secondary-types") or [])
            year = (g.get("first-release-date") or "")[:4]
            self._preview(("release-group", g["id"], 500), g.get("title", ""),
                          "{}\n{}".format(musicbrainz.album_artist(g), "   ·   ".join(
                              x for x in (year, " + ".join(types)) if x)), tracks=("album", g["id"]))
        self._buttons()

    def open_album(self, g):
        # Thumbnails still waiting to download would only slow this down, so drop them.
        """List the pressings of an album so you can pick the one you own."""
        self.covers.cancel_queued()
        self.track_timer.stop()
        self.album, self.pressings, self.press_total = g, [], 0
        self.press_gen += 1
        gen = self.press_gen
        self._set_busy(True, "Loading pressings of {}...".format(g.get("title")))
        self._task(lambda: musicbrainz.list_pressings_page(g["id"], 0), lambda val: self._got_page(val, g, gen, 0))

    def _got_page(self, val, g, gen, offset):
        """Show the first page of pressings straight away and keep loading the rest behind it."""
        if gen != self.press_gen:
            return
        batch, total = val
        self.pressings.extend(batch)
        self.pressings.sort(key=musicbrainz.pressing_sort_key)
        self.press_total = total
        nxt = len(self.pressings)
        self.loading_more = bool(batch) and nxt < total and nxt < musicbrainz.PRESSING_CAP
        if offset == 0:
            self._set_busy(False)
            self.show_pressings()
        elif self.mode == "pressings":
            self.refilter()
        if self.loading_more:
            self._task(lambda: musicbrainz.list_pressings_page(g["id"], nxt), lambda v: self._got_page(v, g, gen, nxt))

    def _got_barcode(self, items):
        """Releases with the barcode found: list them as pressings."""
        self._set_busy(False)
        if not items:
            self._set_status("No release on MusicBrainz has that barcode. Try the artist and album.", "warn")
            return
        self.album, self.press_total, self.loading_more = None, len(items), False
        self.pressings = sorted(items, key=musicbrainz.pressing_sort_key)
        self.show_pressings()

    def show_pressings(self):
        """Show the pressing list."""
        self.mode = self.mb_mode = "pressings"
        self.stack.setCurrentIndex(1)
        self.back_btn.setVisible(bool(self.albums) and self.album is not None)
        self.pfilter.blockSignals(True)
        self.pfilter.clear()
        self.pfilter.blockSignals(False)
        self.refilter()

    def refilter(self, *args):
        """Filter the pressings by the words typed and the vinyl-only switch."""
        if self.mode != "pressings":
            return
        row = self.press_table.currentRow()
        keep = self.shown[row]["id"] if 0 <= row < len(self.shown) else None
        items, note, style = self.pressings, "", "muted"
        if self.vinyl.isChecked():
            vinyl = [r for r in items if musicbrainz.is_vinyl(r)]
            if vinyl:
                items = vinyl
            elif items:
                note, style = " MusicBrainz lists no vinyl pressings of this album, so all formats are shown.", "warn"
        title = self.album.get("title") if self.album else None
        words = self.pfilter.text().lower().split()
        if words:
            items = [r for r in items if all(w in musicbrainz.describe_pressing(r, title).lower() for w in words)]
        self.shown = items
        t = self.press_table
        t.blockSignals(True)
        t.setRowCount(len(items))
        for i, r in enumerate(items):
            vals = (r.get("date") or "", r.get("country") or "", musicbrainz.media_summary(r.get("media")),
                    musicbrainz.release_label(r), r.get("barcode") or "", musicbrainz.pressing_notes(r, title))
            for c, val in enumerate(vals):
                t.setItem(i, c, cell(val))
        t.blockSignals(False)
        kind = "vinyl pressing" if self.vinyl.isChecked() and not note else "pressing"
        if self.album:
            self.press_head.setText("{} by {}:  {} {}{}".format(
                title, musicbrainz.album_artist(self.album), len(items), kind, "" if len(items) == 1 else "s"))
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
        """Preview the selected pressing: cover, label, catalogue number, barcode and tracks."""
        row = self.press_table.currentRow()
        if self.mode != "pressings" or not (0 <= row < len(self.shown)):
            self._buttons()
            return
        r = self.shown[row]
        rgid = self.album["id"] if self.album else None
        artist = musicbrainz.album_artist(self.album) if self.album else musicbrainz.artist_credit_str(r.get("artist-credit"))
        lines = [artist, "   ·   ".join(x for x in (r.get("date"), r.get("country"),
                                                          musicbrainz.media_summary(r.get("media"))) if x)]
        if musicbrainz.release_label(r):
            lines.append(musicbrainz.release_label(r))
        if r.get("barcode"):
            lines.append("Barcode " + r["barcode"])
        self._preview(("pressing", r["id"], rgid, 500), r.get("title", ""), "\n".join(lines),
                      tracks=("release", r["id"]))
        self._buttons()

    # ---- choosing --------------------------------------------------------------
    def next_step(self):
        """The main button: open the album, or fetch the chosen pressing to check its sides and save."""
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
        self._task(lambda: musicbrainz.fetch_release(mbid), lambda rel: self._got_release(rel, mbid))

    def _got_release(self, rel, mbid):
        """The chosen pressing arrived: open the sides editor to check and save it."""
        self._set_busy(False, "Check the side lengths, then Save.")
        sides = musicbrainz.build_sides(rel, allow_prompt=False)
        notes, style = [], "muted"
        if not sides:
            notes.append("MusicBrainz has no track list for this release, so enter the side lengths from the "
                         "sleeve or label.")
            style = "warn"
        elif not musicbrainz.release_has_side_letters(rel):
            notes.append("MusicBrainz doesn't mark the sides for this release, so the split between sides has "
                         "been estimated. Check against the sleeve or label.")
            style = "warn"
        if any(s["missing"] for s in sides):
            notes.append("Some tracks have no length on MusicBrainz, so those sides may be short.")
            style = "warn"
        if not notes:
            notes.append("Side lengths from MusicBrainz. Correct any that look wrong, then Save.")
        dlg = SidesEditor(self, "Confirm record", musicbrainz.artist_credit_str(rel.get("artist-credit")),
                          rel.get("title") or "", (rel.get("date") or "")[:4], sides, " ".join(notes), style)
        if dlg.exec():
            new_sides, artist, title, year = dlg.result_data
            rgid = (rel.get("release-group") or {}).get("id") or (self.album or {}).get("id")
            self.saved_id = library.save_record(self.con, rel.get("id", mbid), artist, title, year,
                                             musicbrainz.media_summary(rel.get("media")), new_sides, rgid,
                                             tracks=details.tracks_from_sides(sides))
            self.accept()

    # ---- Discogs ---------------------------------------------------------------
    def set_source(self, key):
        """Switch between searching MusicBrainz and picking from your Discogs collection."""
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
        """Link a Discogs account, then load its collection."""
        if DiscogsLinkDialog(self, self.main).exec():
            self.load_discogs(refresh=True)

    def load_discogs(self, refresh=False):
        """Load your Discogs collection (the saved copy straight away, downloading when it's old)."""
        token, user = discogs.discogs_account(self.con)
        self.dg_refresh.setEnabled(bool(token))
        if not token:
            self.dg_stack.setCurrentIndex(0)
            show_credit(self.dg_credit, None)
            self._preview(None, "", "Covers appear here when you pick a record.")
            self._set_status("Link your Discogs account to browse your collection here.")
            self._buttons()
            return
        self.dg_stack.setCurrentIndex(1)
        if not refresh:
            items, age = discogs.load_discogs_cache(user)
            if items is not None:
                self.dg_items = items
                self.fill_discogs()
                if age < discogs.DISCOGS_STALE_HOURS:
                    self._set_status("{} items in {}'s collection. Refresh to pick up anything added on Discogs "
                                     "since.".format(len(items), user))
                    return
        self.dg_gen += 1
        gen, self.dg_new = self.dg_gen, []
        self.dg_refresh.setEnabled(False)
        self._set_status("Reading your Discogs collection...")
        self._task(lambda: discogs.discogs_collection_page(token, user, 1), lambda val: self._got_dg_page(val, gen, 1))

    def _got_dg_page(self, val, gen, page):
        """Collection pages arrive a hundred at a time; show them as they come."""
        if gen != self.dg_gen:
            return
        batch, pages, total = val
        self.dg_new.extend(batch)
        token, user = discogs.discogs_account(self.con)
        if page >= pages or not batch:
            self.dg_items = self.dg_new
            discogs.save_discogs_cache(user, self.dg_items)
            self.dg_refresh.setEnabled(True)
            self.fill_discogs()
            self._set_status("{} items in {}'s collection.".format(len(self.dg_items), user))
            return
        if not self.dg_items or page == 1:
            self.dg_items = list(self.dg_new)
            self.fill_discogs()
        self._set_status("Reading your Discogs collection: {} of {}...".format(len(self.dg_new), total))
        self._task(lambda: discogs.discogs_collection_page(token, user, page + 1),
                   lambda v: self._got_dg_page(v, gen, page + 1))

    def fill_discogs(self):
        """Fill the Discogs list, filtered, keeping the selection."""
        user = discogs.discogs_account(self.con)[1]
        show_credit(self.dg_credit, discogs.discogs_collection_url(user) if user and self.dg_items else None)
        cur = self.dg_list.currentItem()
        keep = self.dg_shown[cur.data(ROLE)["i"]]["id"] if cur is not None and self.dg_shown else None
        infos = [discogs.discogs_item_info(it) for it in self.dg_items]
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
        """Preview the selected Discogs record."""
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
                          tracks=("discogs", i["id"]), credit=discogs.discogs_release_url(i["id"]))
        self._buttons()

    def use_discogs(self):
        """Fetch the chosen Discogs record's sides (unless it's already in the library)."""
        item = self.dg_list.currentItem()
        if item is None or not self.dg_shown:
            return
        i = self.dg_shown[item.data(ROLE)["i"]]
        dup = self.con.execute("SELECT title, artist FROM records WHERE discogs_id = ?", (i["id"],)).fetchone()
        if dup:
            QMessageBox.information(self, "Add a record", "That record is already in your library:\n{} by {}".format(
                dup["title"], dup["artist"]))
            return
        token, _ = discogs.discogs_account(self.con)
        self.covers.cancel_queued()
        self._set_busy(True, "Fetching the track list from Discogs...")
        self._task(lambda: discogs.prepare_discogs_release(token, i["id"]), self._got_discogs_release)

    def _got_discogs_release(self, info):
        """The Discogs record's sides arrived: open the sides editor to check and save it."""
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
            self.saved_id = library.save_record(self.con, info["mbid"], artist, title, year, info["format"], sides,
                                             info["rgid"], info["discogs_id"], info["cover_url"],
                                             info.get("tracks"), info.get("about"))
            self.accept()

    def manual(self):
        """Enter a record by hand: title, artist and side lengths."""
        dlg = SidesEditor(self, "Enter a record", self.artist.text().strip(), self.title.text().strip(), "", [],
                          "Type the side lengths from the sleeve or label.")
        if dlg.exec():
            sides, artist, title, year = dlg.result_data
            self.saved_id = library.save_record(self.con, None, artist, title, year, "Vinyl", sides)
            self.accept()


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
