"""The Play log tab: each time a play was logged, newest first, with Remove."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QKeySequence
from PyQt6.QtWidgets import QAbstractItemView, QHBoxLayout, QMessageBox, QVBoxLayout, QWidget

from styli_app import common, plays
from styli_app.gui.theme import C
from styli_app.gui.widgets import ROLE, button, cell, label, sort_name, table


class PlayLogTab:
    """The Play log tab: each time a play was logged, with Remove."""

    # ---- play log ---------------------------------------------------------------
    def _build_playlog(self):
        """The Play log tab: one row each time a play was logged, with Remove."""
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 14, 0, 0)
        v.setSpacing(12)
        v.addWidget(label("Each time you log a play appears here once. Removing a play takes it out of your history "
                          "and play counts, and off the cartridge / stylus it was logged with. Click a column "
                          "heading to sort; click it again to reverse.", "muted", wrap=True))
        self.plog = table(["Played", "Record", "What was logged", "Length", "Cartridge / stylus", ""], 1)
        self._sortable(self.plog, "sort_playlog", unsortable=5)
        self.plog.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.plog.verticalHeader().setDefaultSectionSize(40)
        self.plog.cellClicked.connect(lambda r, c: self.remove_logs([self._plog_group(r)]) if c == 5 else None)
        self.plog.setMouseTracking(True)
        self.plog.cellEntered.connect(lambda r, c: self.plog.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if c == 5 else Qt.CursorShape.ArrowCursor))
        delete = QAction(self.plog)
        delete.setShortcuts([QKeySequence(QKeySequence.StandardKey.Delete), QKeySequence("Backspace")])
        delete.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        delete.triggered.connect(self.remove_selected_logs)
        self.plog.addAction(delete)
        v.addWidget(self.plog, 1)
        bar = QHBoxLayout()
        bar.addWidget(button("Remove selected", self.remove_selected_logs, "danger"))
        self.plog_count = label("", "muted")
        bar.addWidget(self.plog_count)
        bar.addStretch(1)
        v.addLayout(bar)
        return page

    def refresh_playlog(self):
        """Reload the Play log table."""
        self.plog_groups = plays.play_log(self.con, limit=500)
        t = self.plog
        t.setRowCount(len(self.plog_groups))
        for i, g in enumerate(self.plog_groups):
            t.setItem(i, 0, cell(common.to_dt(g["played_at"]).strftime("%d %b %Y  %H:%M"), i, (g["played_at"], max(g["ids"]))))
            t.setItem(i, 1, cell(g["record_label"], None, sort_name(g["record_label"])))
            t.setItem(i, 2, cell(g["what"], None, g["what"].lower()))
            t.setItem(i, 3, cell(common.fmt_dur(g["seconds"]), None, g["seconds"]))
            t.setItem(i, 4, cell(g["stylus"] or "", None, (g["stylus"] or "").lower()))
            rm = cell("Remove")
            rm.setForeground(QColor(C["accent"]))
            rm.setToolTip("Remove this play")
            f = rm.font()
            f.setBold(True)
            rm.setFont(f)
            t.setItem(i, 5, rm)
        self._resort(t)
        total = self.con.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
        self.plog_count.setText("{} logged play{}{}".format(
            len(self.plog_groups), "" if len(self.plog_groups) == 1 else "s",
            " (the latest 500)" if len(self.plog_groups) == 500 else "") if total else "No plays logged yet.")

    def remove_selected_logs(self):
        """Remove the plays selected in the Play log (after asking)."""
        rows = sorted({i.row() for i in self.plog.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "Play log", "Select the plays to remove first.")
            return
        self.remove_logs([self._plog_group(r) for r in rows])

    def _plog_group(self, row):
        """The logged play shown on a row (rows move when the table is sorted)."""
        return self.plog_groups[self.plog.item(row, 0).data(ROLE)]

    def remove_logs(self, groups, ask=True):
        """Remove logged plays (asking first unless ask is False), then refresh everything."""
        if ask:
            if len(groups) == 1:
                g = groups[0]
                text = "Remove this play?\n\n{}\n{} ({})\nPlayed {}{}\n\nIt comes out of your history and play " \
                       "counts{}.".format(g["record_label"], g["what"], common.fmt_dur(g["seconds"]),
                                         common.to_dt(g["played_at"]).strftime("%d %b %Y at %H:%M"),
                                         " with " + g["stylus"] if g["stylus"] else "",
                                         ", and {} comes off {}'s hours".format(common.fmt_dur(g["seconds"]), g["stylus"])
                                         if g["stylus"] else "")
            else:
                total = sum(g["seconds"] for g in groups)
                text = "Remove these {} plays?\n\nTogether they add up to {}, which comes out of your history, " \
                       "play counts and the hours of the cartridges / styli they were logged with.".format(
                           len(groups), common.fmt_dur(total))
            if QMessageBox.question(self, "Remove play" if len(groups) == 1 else "Remove plays", text) \
                    != QMessageBox.StandardButton.Yes:
                return False
        plays.remove_plays(self.con, [i for g in groups for i in g["ids"]])
        self.refresh_all()
        return True
