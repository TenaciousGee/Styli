"""The album panel beside the Library and Discogs lists: cover, sides to log, when, which
cartridge / stylus, Log play (with Undo), and the album's summary and track listing, fetched in
the background when it isn't saved yet."""

import html
import shutil
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QMessageBox,
                             QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

from styli_app import cartridges, common, covers, details, discogs, library, plays, web
from styli_app.gui.cover_views import CoverWidget
from styli_app.gui.dialogs.sides_editor import SidesEditor
from styli_app.gui.tasks import run_task
from styli_app.gui.theme import C
from styli_app.gui.when import WhenPicker, friendly_when
from styli_app.gui.widgets import (breakable, button, card, clear_layout, credit_label, discogs_credit,
                                   fill_track_grid, label, nice_date, restyle, show_credit)


class AlbumPanel:
    """The album panel beside the Library and Discogs lists: cover, sides, when, which cartridge / stylus,
    Log play, and the album's summary and track listing."""

    def _build_detail(self):
        """The album panel shared by the Library and Discogs tabs: cover, sides to log, when, which cartridge /
        stylus, Log play, and the album's summary and tracks."""
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
        self.d_credit = credit_label()  # for records whose cover and details came from Discogs
        for w in (self.d_title, self.d_artist, self.d_meta, self.d_credit, self.d_note):
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

    def _msg_link(self, link):
        """A link in a message was clicked: 'undo:<log id>' removes the play just logged."""
        if link.startswith("undo:"):
            log_id = int(link.split(":", 1)[1])
            ids = [r[0] for r in self.con.execute("SELECT id FROM plays WHERE log_id = ?", (log_id,))]
            if ids:
                plays.remove_plays(self.con, ids)
                self.refresh_all()
            self.d_msg.setText("Removed that play.")
            restyle(self.d_msg, "muted")

    def _clear_chips(self):
        """Remove the side buttons from the panel."""
        clear_layout(self.chips_grid)
        self.chips = {}

    def _set_chips(self, sides):
        """One toggle button per side (all on to start), two to a row."""
        self._clear_chips()
        for i, s in enumerate(sides):
            text = "Side {}   {}".format(s["label"], common.fmt_dur(s["seconds"]) if s["seconds"] else "?:??")
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
        """Show a library record (or 'select a record') in the album panel."""
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
        show_credit(self.d_credit, discogs.discogs_release_url(rec["discogs_id"], details.get_about(rec))
                    if rec is not None and rec["discogs_id"] else None)
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
        c = plays.play_counts(self.con, rid).get(rid)
        played = "Not played yet" if not c else "{}, last {}".format(common.fmt_plays(c["album"], c["sides"]),
                                                                      common.to_dt(c["last"]).strftime("%d %b %Y"))
        self.d_meta.setText(breakable("   ·   ".join(x for x in (rec["year"], rec["format"]) if x)) + "\n" + played)
        self._set_chips(library.record_sides(self.con, rid))
        self.show_about_record(rec)

    def do_log(self):
        """Log play: record the chosen sides at the chosen time against the chosen cartridge / stylus, show an Undo
        link, and warn if it has just passed its warning level or rated life."""
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
            custom = common.parse_duration(text) if text else None
        except ValueError as e:
            QMessageBox.warning(self, "Log a play", str(e))
            return
        times = self.times.value()
        rec = self.con.execute("SELECT * FROM records WHERE id = ?", (self.current,)).fetchone()
        sides = {s["label"]: s for s in library.record_sides(self.con, rec["id"])}
        sid = self.stylus_combo.currentData()
        chosen_stylus = self.con.execute("SELECT * FROM styli WHERE id = ?", (sid,)).fetchone() if sid else None
        before = self._level(sid)
        stylus, total = plays.log_play(self.con, rec, plays.play_entries(sides, chosen, custom), when, times,
                                      chosen_stylus)
        self.partial.clear()
        self.times.setValue(1)
        self.when.set_now()  # the next play is "now" again unless you pick another time
        msg = "Logged side{} {}{} ({}){}{}.".format("s" if len(chosen) > 1 else "", " + ".join(chosen),
                                                      " x{}".format(times) if times > 1 else "", common.fmt_dur(total),
                                                      " for " + friendly_when(when) if back_dated else "",
                                                      " with {}".format(stylus["name"]) if stylus else "")
        if stylus and when < common.to_dt(stylus["installed_at"]):
            msg += " That's before {} was fitted ({}), so check the date.".format(
                stylus["name"], common.to_dt(stylus["installed_at"]).strftime("%d %b %Y"))
        if not stylus:
            msg += " No cartridge / stylus is set up, so it isn't counted against one."
        log_id = self.con.execute("SELECT MAX(log_id) FROM plays").fetchone()[0]
        self.refresh_all()
        self.d_msg.setText('{}&nbsp;&nbsp;<a href="undo:{}" style="color:{}">Undo</a>'.format(
            html.escape(msg), log_id, C["accent"]))
        restyle(self.d_msg, "ok")
        after = self._level(sid)
        if stylus and after != before and after in ("warn", "over"):
            QMessageBox.warning(self, "Cartridge / stylus wear", cartridges.stylus_alert(
                self.con, self.con.execute("SELECT * FROM styli WHERE id = ?", (stylus["id"],)).fetchone()))

    def _record(self):
        """The record shown in the panel, or None."""
        return self.con.execute("SELECT * FROM records WHERE id = ?", (self.current,)).fetchone() \
            if self.current else None

    def edit_record(self):
        """Edit the shown record's title, artist and sides."""
        if self.pending is not None:
            self.edit_pending()
            return
        rec = self._record()
        if not rec:
            return
        dlg = SidesEditor(self, "Edit record", rec["artist"], rec["title"], rec["year"] or "",
                          [dict(s) for s in library.record_sides(self.con, rec["id"])],
                          note="Plays you have already logged keep the lengths they were logged with.")
        if dlg.exec():
            sides, artist, title, year = dlg.result_data
            self.con.execute("UPDATE records SET artist = ?, title = ?, year = ? WHERE id = ?",
                             (artist, title, year, rec["id"]))
            library.replace_sides(self.con, rec["id"], sides)
            self.refresh_all()

    def set_cover(self):
        """Choose your own image as the shown record's cover."""
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
        dest = covers.library_cover_path(rec["id"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, str(dest))
        self.covers.forget_record(rec["id"])
        self.refresh_library()

    def remove_record(self):
        """Remove the shown record from the library (after asking). Its plays are kept."""
        rec = self._record()
        if not rec:
            return
        if QMessageBox.question(self, "Remove record", "Remove {} by {} from your library?\n\nPlays you have "
                                "logged are kept, so cartridge / stylus hours don't change.".format(rec["title"], rec["artist"])
                                ) != QMessageBox.StandardButton.Yes:
            return
        self.con.execute("DELETE FROM records WHERE id = ?", (rec["id"],))
        self.con.commit()
        covers.remove_record_cover(rec["id"])
        self.covers.forget_record(rec["id"])
        self.current = self.lib_sel = None
        self.refresh_all()

    # ---- album details: summary and track listing -------------------------------
    def _build_about(self):
        """The 'About this album' part of the panel: facts, summary, links, pressing notes, tracks."""
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
        """Scroll the panel down to the album's summary."""
        bar = self.detail_scroll.verticalScrollBar()
        bar.setValue(min(bar.maximum(), self.about_box.y() - 12))

    def render_about(self, about, tracks, lengths, status="", can_refresh=False):
        """Show an album's summary and track listing (grouped by side) in the panel."""
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
            links.append(discogs_credit(about["discogs_url"]))
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
        """Show what's saved about a library record, and fetch anything missing in the background."""
        token, _ = discogs.discogs_account(self.con)
        tracks = details.get_tracks(self.con, rec["id"])
        about = details.get_about(rec)
        lengths = {s["label"]: s["seconds"] for s in library.record_sides(self.con, rec["id"])}
        need_t, need_a = details.details_needed(self.con, rec, token)
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
        token, _ = discogs.discogs_account(self.con)
        need_t, need_a = details.details_needed(self.con, rec, token)
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
            details.store_details(self.con, rid, res)
            if res["about"] is None:
                self.detail_failed.add(rid)  # offline: don't keep retrying this session
            if self.current == rid and self.pending is None:
                self.show_about_record(self.con.execute("SELECT * FROM records WHERE id = ?", (rid,)).fetchone(),
                                       res["error"] if res["about"] is None else None)

        def failed(e):
            self.detail_loading.discard(rid)
            self.detail_failed.add(rid)
            if self.current == rid and self.pending is None:
                self.show_about_record(rec, str(e) if isinstance(e, web.MBError) else repr(e))

        run_task(lambda: details.fetch_details(data, token, need_t, need_a), done, failed, self.tasks)

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

            run_task(lambda: details.lookup_album_wikipedia(artist, title), done, failed, self.tasks)
