"""Checking or typing a record's sides and their running times before it's saved."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QAbstractItemView, QDialog, QFormLayout, QHBoxLayout, QLineEdit, QMessageBox

from styli_app import common
from styli_app.gui.theme import C
from styli_app.gui.widgets import ROLE, button, cell, dialog_layout, frow, label, table


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
            self.add_row(s["label"], common.fmt_dur(s["seconds"]) if s["seconds"] else "",
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
        """Add a side to the table. Returns its row number."""
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
        """Add the next unused side letter and start typing its length."""
        used = {(self.table.item(r, 0).text() or "").strip().upper() for r in range(self.table.rowCount())}
        nxt = next((common.side_letter(i) for i in range(40) if common.side_letter(i) not in used), "")
        r = self.add_row(nxt)
        self.table.setCurrentCell(r, 1)
        self.table.editItem(self.table.item(r, 1))

    def remove_selected(self):
        """Remove the selected sides."""
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def save(self):
        """Check every side has a label and a valid length, then close with the result."""
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
                    secs = common.parse_duration(text)
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
