"""Building blocks: consistently styled labels, buttons and tables, table cells that sort by a
hidden key, long titles that can wrap, the track listing grid, the sort orders offered by the
library and Discogs lists, and the "Data provided by Discogs." credit."""

import datetime as dt
import html
import re

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QGridLayout, QHeaderView, QLabel, QPushButton,
                             QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from styli_app import common, discogs
from styli_app.gui.theme import C


ROLE = Qt.ItemDataRole.UserRole


def label(text="", name=None, wrap=False):
    """A text label, optionally styled by name (e.g. 'muted', 'caption') and wrapping."""
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def button(text, slot=None, name=None):
    """A push button wired to slot, optionally styled by name (e.g. 'primary', 'ghost')."""
    b = QPushButton(text)
    if name:
        b.setObjectName(name)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def card():
    """A rounded panel, the background for most groups of controls."""
    f = QFrame()
    f.setObjectName("card")
    return f


def restyle(widget, name=None, **props):
    """Change a widget's style name or property and make the stylesheet notice."""
    if name is not None:
        widget.setObjectName(name)
    for k, v in props.items():
        widget.setProperty(k, v)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def table(headers, stretch_col):
    """A read-only table that selects whole rows, with column stretch_col taking spare width."""
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.setWordWrap(False)
    t.verticalHeader().setDefaultSectionSize(36)
    hh = t.horizontalHeader()
    hh.setHighlightSections(False)
    hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    for i in range(len(headers)):
        hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch if i == stretch_col
                                else QHeaderView.ResizeMode.ResizeToContents)
    return t


def frow(form, text, widget):
    """A form row whose label lines up with the middle of its field."""
    lab = QLabel(text)
    lab.setMinimumHeight(widget.sizeHint().height())
    lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.addRow(lab, widget)


def nice_date(text):
    """'1977-02-04' -> '4 Feb 1977', '1977-02' -> 'Feb 1977'; anything else as it is."""
    text = (text or "").strip()
    try:
        if len(text) == 10:
            d = dt.date.fromisoformat(text)
            return "{} {}".format(d.day, d.strftime("%b %Y"))
        if len(text) == 7:
            return dt.date.fromisoformat(text + "-01").strftime("%b %Y")
    except ValueError:
        pass
    return text


ZWSP = "\u200b"


def breakable(text, chunk=14):
    """Let very long words (web addresses, catalogue numbers) wrap instead of pushing the panel wider."""
    def split(m):
        w = m.group(0)
        return ZWSP.join(w[i:i + chunk] for i in range(0, len(w), chunk))
    return re.sub(r"\S{%d,}" % (chunk * 2), split, text or "")


def make_splitter(main):
    """The draggable divider between a list and the album panel (its position is remembered)."""
    split = QSplitter(Qt.Orientation.Horizontal)
    split.setChildrenCollapsible(False)
    split.setHandleWidth(18)
    split.splitterMoved.connect(lambda pos, idx: main._panel_moved(split))
    return split


def fill_track_grid(grid, tracks, lengths):
    """Track listing grouped by side: a heading with the side's length, then position, title and length."""
    clear_layout(grid)
    row, side = 0, None
    for t in tracks or []:
        if t["side"] != side:
            side = t["side"]
            grid.addWidget(label("Side {}".format(side), "trackSide"), row, 0, 1, 2)
            if lengths.get(side):
                tl = label(common.fmt_dur(lengths[side]), "muted")
                tl.setAlignment(Qt.AlignmentFlag.AlignRight)
                grid.addWidget(tl, row, 2)
            row += 1
        pos = label(t["position"] or "", "muted")
        pos.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        title = label(breakable(t["title"] or ""), wrap=True)
        dur = label(common.fmt_dur(t["seconds"]) if t["seconds"] else "", "muted")
        dur.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        grid.addWidget(pos, row, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(title, row, 1)
        grid.addWidget(dur, row, 2, Qt.AlignmentFlag.AlignTop)
        row += 1


def make_track_grid():
    """An empty grid for a track listing (filled by fill_track_grid). Returns (widget, grid)."""
    box = QWidget()
    grid = QGridLayout(box)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(5)
    grid.setColumnStretch(1, 1)
    return box, grid


def clear_layout(lay):
    """Remove every widget from a layout straight away (not just at the next idle moment)."""
    while lay.count():
        w = lay.takeAt(0).widget()
        if w:
            w.hide()
            w.setParent(None)
            w.deleteLater()


SORT_ROLE = Qt.ItemDataRole.UserRole + 1


class SortItem(QTableWidgetItem):
    """A table cell that sorts by a hidden key (dates by date, lengths by seconds, names ignoring 'The')."""

    def __lt__(self, other):
        """Compare by the hidden sort key when there is one, otherwise by the text."""
        a, b = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if a is None or b is None:
            return super().__lt__(other)
        return a < b


def cell(text, data=None, key=None):
    """A table cell with optional hidden data and sort key."""
    it = SortItem(str(text))
    if data is not None:
        it.setData(ROLE, data)
    if key is not None:
        it.setData(SORT_ROLE, key)
    return it


def sort_name(text):
    """Names sort the way record shops file them: case doesn't matter and a leading 'The' is ignored."""
    t = " ".join((text or "").lower().split())
    return t[4:] if t.startswith("the ") else t


LIB_SORTS = [("artist", "Artist (A to Z)"), ("artist_desc", "Artist (Z to A)"), ("album", "Album (A to Z)"),
             ("album_desc", "Album (Z to A)"), ("year", "Year (oldest first)"), ("year_desc", "Year (newest first)"),
             ("added", "Recently added"), ("played", "Recently played"), ("most", "Most played")]


def sort_records(items, how):
    """items: dicts with artist, title, year, added, last, album, sides. Returns them sorted."""
    by_name = lambda i: (sort_name(i["artist"]), sort_name(i["title"]), i["year"] or "")  # noqa: E731
    by_album = lambda i: (sort_name(i["title"]), sort_name(i["artist"]))  # noqa: E731
    year = lambda i: i["year"] or ""  # noqa: E731
    if how == "artist_desc":
        return sorted(items, key=by_name, reverse=True)
    if how == "album":
        return sorted(items, key=by_album)
    if how == "album_desc":
        return sorted(items, key=by_album, reverse=True)
    if how in ("year", "year_desc"):
        dated = sorted([i for i in items if year(i)], key=lambda i: (year(i), by_name(i)), reverse=how == "year_desc")
        return dated + sorted([i for i in items if not year(i)], key=by_name)  # undated last either way
    if how == "added":
        return sorted(items, key=lambda i: i["added"] or "", reverse=True)
    if how == "played":
        played = sorted([i for i in items if i["last"]], key=lambda i: i["last"], reverse=True)
        return played + sorted([i for i in items if not i["last"]], key=by_name)
    if how == "most":
        return sorted(items, key=lambda i: (-i["album"], -i["sides"], by_name(i)))
    return sorted(items, key=by_name)


def sort_combo(options, current, changed):
    """A 'Sort by' drop-down; changed(key) is called with the chosen order."""
    box = QComboBox()
    for key, text in options:
        box.addItem(text, key)
    box.setCurrentIndex(max(0, box.findData(current)))
    box.setToolTip("Sort the list")
    box.currentIndexChanged.connect(lambda i: changed(box.itemData(i)))
    return box


class ElidedLabel(QLabel):
    """One line of text that shortens itself with "..." instead of wrapping (so it never changes
    the height of the card it's in), with the whole text in its tooltip."""

    def setText(self, text):
        super().setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self):
        return QSize(20, super().minimumSizeHint().height())

    def sizeHint(self):
        return QSize(20, super().sizeHint().height())

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        p.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                   self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width()))
        p.end()


def discogs_credit(url):
    """'Data provided by Discogs.' linked to the discogs.com page the data shown came from, as
    Discogs' API terms ask. For a label with setOpenExternalLinks(True)."""
    return '<a href="{}" style="color:{}">{}</a>'.format(html.escape(url, quote=True), C["accent"],
                                                          html.escape(discogs.DISCOGS_CREDIT))


def credit_label():
    """An empty label for a Discogs credit; links in it open in your web browser."""
    w = label("", "muted", wrap=True)
    w.setOpenExternalLinks(True)
    w.setVisible(False)
    return w


def show_credit(widget, url):
    """Show the Discogs credit in widget, linked to url, or hide it when url is None."""
    widget.setText(discogs_credit(url) if url else "")
    widget.setVisible(bool(url))


def dialog_layout(dlg, title, min_width=None):
    """Title a dialog and give it the standard margins. Returns its main layout."""
    dlg.setWindowTitle(title)
    if min_width:
        dlg.setMinimumWidth(min_width)
    v = QVBoxLayout(dlg)
    v.setContentsMargins(22, 20, 22, 18)
    v.setSpacing(12)
    return v
