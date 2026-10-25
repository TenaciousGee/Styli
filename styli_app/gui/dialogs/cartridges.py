"""Managing cartridges / styli: the list (in use and retired) with each one's photo, wear and
specifications; fitting or editing one (with details read from a product page); and finding a
photo and specifications online."""

import datetime as dt
from pathlib import Path

from PyQt6.QtCore import QDate, QTimer, QUrl, Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDoubleSpinBox,
                             QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLineEdit, QListWidgetItem,
                             QMessageBox, QProgressBar, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from styli_app import cartridge_lookup, cartridges, common, web
from styli_app.gui.cover_views import CoverWidget, MonthBars, PixmapWidget, make_list
from styli_app.gui.tasks import run_task
from styli_app.gui.theme import C
from styli_app.gui.widgets import ROLE, button, card, cell, dialog_layout, frow, label, restyle, table


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
        """Reload the list: in use first, then retired ones, most recently retired first."""
        select = select or self.sid
        cur = cartridges.active_stylus(self.con)
        rows = self.con.execute("SELECT * FROM styli").fetchall()
        rows.sort(key=lambda s: (0 if s["active"] else 1, s["id"] if s["active"] else
                                 -(common.to_dt(s["retired_at"]).timestamp() if s["retired_at"] else 0)))
        self.list.blockSignals(True)
        self.list.clear()
        sel = None
        for s in rows:
            m = cartridges.stylus_summary(self.con, s)
            if s["active"]:
                sub = "On the turntable" if cur and s["id"] == cur["id"] else "In use"
            else:
                sub = "Retired {}".format(common.to_dt(s["retired_at"]).strftime("%d %b %Y")) if s["retired_at"] \
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
        """Something changed: refresh the main window and this list, keeping a selection."""
        self.main.photos.updated()
        self.main.refresh_all()
        self.refresh(select)

    # ---- detail ---------------------------------------------------------------
    def show_stylus(self, sid):
        """Show one cartridge / stylus's photo, wear, dates, specifications and hours by month."""
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
        m = cartridges.stylus_stats(self.con, s)
        cur = cartridges.active_stylus(self.con)
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
        end = m["retired"] or common.now_local()
        self.stat["dates"].setText("{} to {}".format(m["installed"].strftime("%b %Y"),
                                                     "now" if s["active"] else end.strftime("%b %Y")))
        self.wear.setValue(int(min(100, m["pct"]) * 10))
        restyle(self.wear, level=m["level"])
        months = cartridges.stylus_hours_by_month(self.con, s["id"])
        self.months.set_data(months)
        self.month_caption.setVisible(bool(months))
        specs = cartridges.get_specs(s)
        shown = 0
        for key, lab, _ in cartridges.SPEC_FIELDS:
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
        """The selected cartridge / stylus, or None."""
        return self.con.execute("SELECT * FROM styli WHERE id = ?", (self.sid,)).fetchone() if self.sid else None

    # ---- actions ----------------------------------------------------------------
    def put_on(self):
        """Put the selected cartridge / stylus on the turntable."""
        if self.sid:
            cartridges.stylus_use(self.con, self.sid)
            self._changed(self.sid)

    def retire(self):
        """Retire the selected cartridge / stylus (after asking). Its plays are kept."""
        s = self._row()
        if s and QMessageBox.question(self, "Retire cartridge / stylus", "Retire {}? Its {} of plays stay in your history."
                                      .format(s["name"], common.fmt_hours(cartridges.stylus_used_seconds(self.con, s)))
                                      ) == QMessageBox.StandardButton.Yes:
            cartridges.stylus_retire(self.con, s["id"])
            self._changed(s["id"])

    def delete(self):
        """Delete the selected cartridge / stylus, only possible if nothing was played with it."""
        s = self._row()
        if s and QMessageBox.question(self, "Delete cartridge / stylus", "Delete {}? Use this only for a cartridge / stylus added by "
                                      "mistake.".format(s["name"])) == QMessageBox.StandardButton.Yes:
            cartridges.stylus_delete(self.con, s["id"])
            self.sid = None
            self._changed()

    def add(self):
        """Fit a new cartridge / stylus (optionally retiring the one it replaces)."""
        dlg = StylusEditor(self, None, cartridges.styli_in_use(self.con))
        if not dlg.exec():
            return
        r = dlg.result_data
        sid = cartridges.stylus_add(self.con, r["name"], r["hours"], r["used"], r["warn"], r["installed"], r["replaces"])
        cartridges.set_stylus_details(self.con, sid, r["specs"], r["url"] or None)
        if r["photo"]:
            cartridges.save_stylus_photo(sid, r["photo"])
        self._changed(sid)
        if not r["url"] and QMessageBox.question(self, "Find details online", "Look up a photo and specifications for {} online?"
                                .format(r["name"])) == QMessageBox.StandardButton.Yes:
            self.lookup()

    def edit(self):
        """Edit the selected cartridge / stylus's details."""
        s = self._row()
        if not s:
            return
        dlg = StylusEditor(self, s)
        if dlg.exec():
            r = dlg.result_data
            cartridges.stylus_edit(self.con, s["id"], name=r["name"], hours=r["hours"], warn=r["warn"], used_h=r["used"],
                             installed=r["installed"])
            cartridges.set_stylus_details(self.con, s["id"], r["specs"], r["url"])
            if r["photo"]:
                cartridges.save_stylus_photo(s["id"], r["photo"])
            self._changed(s["id"])

    def lookup(self):
        """Find a photo and specifications for the selected cartridge / stylus online."""
        s = self._row()
        if not s:
            return
        dlg = LookupDialog(self, s)
        if not dlg.exec():
            return
        r = dlg.result_data
        if r["photo"]:
            cartridges.save_stylus_photo(s["id"], r["photo"])
        cartridges.set_stylus_details(self.con, s["id"], r["specs"], r["url"])
        if r["hours"]:
            cartridges.stylus_edit(self.con, s["id"], hours=r["hours"])
        self._changed(s["id"])

    def choose_photo(self):
        """Use your own photo of the selected cartridge / stylus."""
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
        cartridges.save_stylus_photo(s["id"], Path(path).read_bytes())
        self._changed(s["id"])

    def show_plays(self):
        """Show the selected cartridge / stylus's plays in the History tab."""
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
        d = common.to_dt(s["installed_at"]).date() if s else dt.date.today()
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
        specs = cartridges.get_specs(s) if s else {}
        sform = QFormLayout()
        sform.setSpacing(8)
        sform.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.spec_edits = {}
        for key, lab, _ in cartridges.SPEC_FIELDS:
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
        """Mark the dialog closed so a page read that finishes later is ignored."""
        self.closed = True
        super().done(result)

    def _page_status(self, text, style="muted"):
        """Set the message under the product page link."""
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
            found = cartridge_lookup.scrape_product_page(url)
            if found.get("image_url"):
                try:
                    found["image"] = web.http_get(found["image_url"])[0]
                except web.MBError:
                    found["image"] = None
            return found

        def failed(e):
            if not self.closed and self.url.text().strip() == self.fetched_url:
                self._page_status("Couldn't read that page: {}".format(e if isinstance(e, web.MBError) else repr(e)),
                                  "warn")

        self._page_status("Reading the page...")
        run_task(work, lambda found: None if self.closed else self.fill_from_page(found), failed, self.tasks)

    def fill_from_page(self, found):
        """Fill in fields from a product page: empty ones, and ones it filled before. Never overwrites your own
        typing."""
        if self.url.text().strip() != self.fetched_url:
            return  # the link was changed while we were reading
        filled = []

        def put(edit, value, key):
            # fill empty fields, and update ones this page filled before; never overwrite your own typing
            if value and (not edit.text().strip() or key in self.filled):
                edit.setText(value)
                self.filled.add(key)
                filled.append(key)

        put(self.name, cartridge_lookup.name_from_page_title(found.get("title")), "name")
        for key, edit in self.spec_edits.items():
            put(edit, found["specs"].get(key, ""), key)
        put(self.desc, found.get("description", ""), "description")
        hours = cartridges.suggest_hours(found["specs"])
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
        """Check the details and close with them."""
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "Cartridge / stylus", "Give the cartridge / stylus a name.")
            return
        if self.hours.value() <= 0:
            QMessageBox.warning(self, "Cartridge / stylus", "Enter the rated life in hours from the cartridge / stylus maker.")
            return
        day = self.fitted.date().toPyDate()
        installed = common.now_local() if day == dt.date.today() else \
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
        """Mark the dialog closed so a lookup that finishes later is ignored."""
        self.closed = True
        super().done(result)

    def _run(self, fn, text):
        """Run a lookup in the background (downloading its photo too), then show the result."""
        def work():
            found = fn()
            if found and found.get("image_url"):
                try:
                    found["image"] = web.http_get(found["image_url"])[0]
                except web.MBError:
                    found["image"] = None
            return found

        def ok(found):
            if not self.closed:
                self._busy(False)
                self.show_result(found)

        def failed(e):
            if not self.closed:
                self._busy(False)
                self._status(str(e) if isinstance(e, web.MBError) else repr(e), "warn")

        self._busy(True)
        self._status(text)
        run_task(work, ok, failed, self.tasks)

    def _busy(self, busy):
        """Show or clear the busy state."""
        self.busy = busy
        self.search_btn.setEnabled(not busy)
        self.read_btn.setEnabled(not busy)
        if busy:
            self.setCursor(Qt.CursorShape.BusyCursor)
        else:
            self.unsetCursor()

    def _status(self, text, style="muted"):
        """Set the message at the bottom of the dialog."""
        self.status.setText(text)
        restyle(self.status, style)

    def search(self):
        """Look the cartridge up on Wikipedia."""
        q = self.q.text().strip()
        if q:
            self._run(lambda: cartridge_lookup.lookup_wikipedia(q), "Searching Wikipedia...")

    def read_page(self):
        """Read a manufacturer's or shop's product page."""
        url = self.url.text().strip()
        if not url:
            self._status("Paste a link to a product page first.", "warn")
            return
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        self._run(lambda: cartridge_lookup.scrape_product_page(url), "Reading the page...")

    def show_result(self, found):
        """Show what was found: photo, description and specifications you can edit."""
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
        self.table.setRowCount(len(cartridges.SPEC_FIELDS))
        for i, (key, lab, _) in enumerate(cartridges.SPEC_FIELDS):
            it = cell(lab, key)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            it.setForeground(QColor(C["muted"]))
            self.table.setItem(i, 0, it)
            self.table.setItem(i, 1, cell(specs.get(key, "")))
        hours = cartridges.suggest_hours(specs)
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
        """Save the photo and specifications shown to the cartridge / stylus."""
        if not self.found:
            return
        specs = {self.table.item(i, 0).data(ROLE): (self.table.item(i, 1).text() or "").strip()
                 for i in range(self.table.rowCount())}
        specs = {k: val for k, val in specs.items() if val}
        if self.found.get("description"):
            specs["description"] = self.found["description"]
        self.result_data = {"photo": self.found.get("image") if self.use_photo.isChecked() else None,
                            "specs": specs, "url": self.found.get("url"),
                            "hours": cartridges.suggest_hours(specs) if self.use_hours.isChecked() else None}
        self.accept()
