"""The History tab: every side played, filtered by cartridge / stylus, with delete and CSV export."""

from pathlib import Path
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QMessageBox, QVBoxLayout,
                             QWidget)

from styli_app import common, plays
from styli_app.gui.widgets import ROLE, button, cell, label, sort_name, table


class HistoryTab:
    """The History tab: every side played, filtered by cartridge / stylus, with delete and export."""

    def _build_history(self):
        """The History tab: every side played, filtered by cartridge / stylus, with delete and export."""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 14, 0, 0)
        v.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label("Show plays with", "muted"))
        self.hist_filter = QComboBox()
        self.hist_filter.setMinimumWidth(280)
        self.hist_filter.currentIndexChanged.connect(lambda i: self.refresh_history())
        top.addWidget(self.hist_filter)
        top.addStretch(1)
        v.addLayout(top)
        self.hist = table(["When", "Record", "Side", "Length", "Cartridge / stylus"], 1)
        self._sortable(self.hist, "sort_history")
        self.hist.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        v.addWidget(self.hist, 1)
        bar = QHBoxLayout()
        bar.addWidget(button("Delete selected", self.delete_plays, "danger"))
        self.hist_count = label("", "muted")
        self.hist_count.setToolTip("Click a column heading to sort; click it again to reverse")
        bar.addWidget(self.hist_count)
        bar.addStretch(1)
        bar.addWidget(button("Export CSV", self.export_csv))
        v.addLayout(bar)
        return page

    def refresh_history_filter(self):
        """Refill the History tab's cartridge / stylus drop-down."""
        f = self.hist_filter
        keep = f.currentData()
        f.blockSignals(True)
        f.clear()
        f.addItem("All cartridges / styli", None)
        rows = self.con.execute("SELECT * FROM styli ORDER BY active DESC, id DESC").fetchall()
        for s in rows:
            f.addItem("{}{}".format(s["name"], "" if s["active"] else "  (retired)"), s["id"])
        idx = f.findData(keep) if keep is not None else 0
        f.setCurrentIndex(max(0, idx))
        f.blockSignals(False)

    def show_stylus_plays(self, sid):
        """Open the History tab showing only one cartridge / stylus's plays."""
        self.refresh_history_filter()
        self.hist_filter.setCurrentIndex(max(0, self.hist_filter.findData(sid)))
        self.tabs.setCurrentWidget(self.hist_page)
        self.refresh_history()

    def refresh_history(self):
        """Reload the History table (up to 2,000 sides)."""
        sid = self.hist_filter.currentData() if hasattr(self, "hist_filter") else None
        where, params = ("WHERE p.stylus_id = ?", [sid]) if sid is not None else ("", [])
        rows = self.con.execute(
            "SELECT p.*, s.name stylus FROM plays p LEFT JOIN styli s ON s.id = p.stylus_id {} "
            "ORDER BY p.played_at DESC, p.id DESC LIMIT 2000".format(where), params).fetchall()
        t = self.hist
        t.setRowCount(len(rows))
        for i, p in enumerate(rows):
            t.setItem(i, 0, cell(common.to_dt(p["played_at"]).strftime("%d %b %Y  %H:%M"), p["id"], (p["played_at"], p["id"])))
            t.setItem(i, 1, cell(p["record_label"], None, sort_name(p["record_label"])))
            t.setItem(i, 2, cell(p["side"], None, p["side"].lower()))
            t.setItem(i, 3, cell(common.fmt_dur(p["seconds"]), None, p["seconds"]))
            t.setItem(i, 4, cell(p["stylus"] or "", None, (p["stylus"] or "").lower()))
        self._resort(t)
        total = self.con.execute("SELECT COUNT(*), COALESCE(SUM(seconds), 0) FROM plays p {}".format(where),
                                 params).fetchone()
        self.hist_count.setText("{} entries, {} in total{}".format(
            total[0], common.fmt_hours(total[1]), " (latest 2000 shown)" if total[0] > 2000 else ""))

    # ---- history actions ----------------------------------------------------
    def delete_plays(self):
        """Delete the sides selected in the History tab (after asking)."""
        rows = sorted({i.row() for i in self.hist.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "History", "Select the entries to delete first.")
            return
        if QMessageBox.question(self, "Delete plays", "Delete {} selected entr{}? This takes the time off "
                                "the cartridge / stylus total.".format(len(rows), "y" if len(rows) == 1 else "ies")
                                ) != QMessageBox.StandardButton.Yes:
            return
        ids = [self.hist.item(r, 0).data(ROLE) for r in rows]
        self.con.executemany("DELETE FROM plays WHERE id = ?", [(i,) for i in ids])
        self.con.commit()
        self.refresh_all()

    def export_csv(self):
        """Save every play as a CSV file."""
        path, _ = QFileDialog.getSaveFileName(self, "Export plays", str(Path.home() / "vinyl-plays.csv"),
                                              "CSV files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fh:
            n = plays.write_csv(self.con, fh)
        QMessageBox.information(self, "Export", "Exported {} plays to\n{}".format(n, path))
