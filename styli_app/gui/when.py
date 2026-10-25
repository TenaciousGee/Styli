"""When a play happened: the When row in the album panel.

It shows "Now" until you pick an earlier day and time on the calendar, for logging plays after
the event. Days you've already played records on are underlined, and times that haven't
happened yet can't be picked."""

import datetime as dt

from PyQt6.QtCore import QDate, QEvent, QPoint, QPointF, QRectF, QTime, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPen, QPixmap, QTextCharFormat
from PyQt6.QtWidgets import (QApplication, QCalendarWidget, QFrame, QHBoxLayout, QPushButton, QTimeEdit,
                             QToolButton, QVBoxLayout, QWidget)

from styli_app import common, plays
from styli_app.gui.theme import C
from styli_app.gui.widgets import button, card, label


def calendar_icon(colour, size=16):
    """A small calendar drawn for the current theme."""
    img = QImage(size * 2, size * 2, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(colour), 2.6)
    p.setPen(pen)
    p.drawRoundedRect(QRectF(4, 7, 24, 21), 4, 4)
    p.drawLine(QPointF(4, 13.5), QPointF(28, 13.5))
    p.drawLine(QPointF(11, 3.5), QPointF(11, 9))
    p.drawLine(QPointF(21, 3.5), QPointF(21, 9))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(colour))
    for x, y in ((10, 19), (16, 19), (22, 19), (10, 24), (16, 24)):
        p.drawEllipse(QPointF(x, y), 1.6, 1.6)
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def friendly_when(when):
    """A play time as 'Today, 20:30', 'Yesterday, 20:30' or 'Sat 11 Oct 2026, 20:30'."""
    today = common.now_local().date()
    day = when.date()
    if day == today:
        return "Today, " + when.strftime("%H:%M")
    if day == today - dt.timedelta(days=1):
        return "Yesterday, " + when.strftime("%H:%M")
    return when.strftime("%a %d %b %Y, %H:%M").replace(" 0", " ", 1)


class WhenPopup(QFrame):
    """A calendar and a time, opened from the When button. Days you've played records on are marked."""

    def __init__(self, picker, marked):
        super().__init__(picker, Qt.WindowType.Popup)
        self.picker = picker
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box = card()
        box.setObjectName("popupCard")
        outer.addWidget(box)
        v = QVBoxLayout(box)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(10)
        quick = QHBoxLayout()
        quick.setSpacing(6)
        quick.addWidget(label("WHEN DID YOU PLAY IT?", "caption"))
        quick.addStretch(1)
        for text, slot in (("Now", self.pick_now), ("Yesterday", self.pick_yesterday)):
            b = button(text, slot, "chip")
            quick.addWidget(b)
        v.addLayout(quick)
        now = common.now_local()
        start = picker.value or now
        self.cal = QCalendarWidget()
        self.cal.setGridVisible(False)
        self.cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.cal.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.cal.setMaximumDate(QDate(now.year, now.month, now.day))  # no plays in the future
        self.cal.setSelectedDate(QDate(start.year, start.month, start.day))
        self.cal.setMinimumSize(300, 230)
        plain = QTextCharFormat()
        plain.setForeground(QColor(C["text"]))
        for d in range(1, 8):  # every day alike: weekends aren't red here
            self.cal.setWeekdayTextFormat(Qt.DayOfWeek(d), plain)
        head = QTextCharFormat()
        head.setForeground(QColor(C["muted"]))
        head.setFontWeight(QFont.Weight.Bold)
        self.cal.setHeaderTextFormat(head)
        mark = QTextCharFormat()
        mark.setForeground(QColor(C["accent"]))
        mark.setFontWeight(QFont.Weight.Bold)
        mark.setFontUnderline(True)
        for d in marked:
            self.cal.setDateTextFormat(QDate(d.year, d.month, d.day), mark)
        later = QTextCharFormat()
        later.setForeground(QColor(C["faint"]))
        today = QDate(now.year, now.month, now.day)
        for n in range(1, 43):  # days still to come can't be picked, so grey them out
            self.cal.setDateTextFormat(today.addDays(n), later)
        for name, text in (("qt_calendar_prevmonth", "‹"), ("qt_calendar_nextmonth", "›")):
            b = self.cal.findChild(QToolButton, name)
            if b:
                b.setIcon(QIcon())
                b.setText(text)
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.cal.activated.connect(lambda d: self.done_clicked())  # double-click a day
        v.addWidget(self.cal)
        if marked:
            v.addWidget(label("Underlined days already have plays logged.", "muted"))
        row = QHBoxLayout()
        row.addWidget(label("Time", "muted"))
        self.time = QTimeEdit(QTime(start.hour, start.minute))
        self.time.setDisplayFormat("HH:mm")
        row.addWidget(self.time)
        row.addStretch(1)
        row.addWidget(button("Done", self.done_clicked, "primary"))
        v.addLayout(row)
        self.note = label("", "warn", wrap=True)
        self.note.setVisible(False)
        v.addWidget(self.note)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.done_clicked()
            return
        super().keyPressEvent(event)

    def pick_now(self):
        """Now: go back to logging plays at the current time."""
        self.picker.set_now()
        self.close()

    def pick_yesterday(self):
        """Yesterday: select yesterday on the calendar."""
        self.cal.setSelectedDate(QDate.currentDate().addDays(-1))

    def chosen(self):
        """The day and time picked, as a local datetime."""
        d, t = self.cal.selectedDate(), self.time.time()
        return dt.datetime(d.year(), d.month(), d.day(), t.hour(), t.minute()).astimezone()

    def done_clicked(self):
        """Use the picked time, unless it's in the future."""
        when = self.chosen()
        now = common.now_local()
        if when > now:
            self.note.setText("That time hasn't happened yet. Pick a time up to {}.".format(now.strftime("%H:%M")))
            self.note.setVisible(True)
            self.adjustSize()
            return
        self.picker.set_value(when)
        self.close()


class WhenPicker(QWidget):
    """Shows "Now" until you pick an earlier day and time for the play."""

    changed = pyqtSignal()

    def __init__(self, con):
        super().__init__()
        self.con = con
        self.value = None  # None means now
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.btn = QPushButton()
        self.btn.setObjectName("whenBtn")
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.setToolTip("Logging a play from earlier? Pick the day and time here.")
        self.btn.clicked.connect(self.open_popup)
        h.addWidget(self.btn, 1)
        self.now_btn = button("Use now", self.set_now, "ghost")
        h.addWidget(self.now_btn)
        self.popup = None
        self._render()

    def when(self):
        """When the play happened: the time picked, or now."""
        return self.value or common.now_local()

    def is_now(self):
        """True when no earlier time has been picked."""
        return self.value is None

    def set_now(self):
        """Go back to 'Now'."""
        self.value = None
        self._render()
        self.changed.emit()

    def set_value(self, when):
        """Use an earlier time (to the minute)."""
        self.value = when.replace(second=0, microsecond=0)
        self._render()
        self.changed.emit()

    def _render(self):
        """Update the button: 'Now', or the time picked (outlined so it stands out)."""
        self.btn.setIcon(calendar_icon(C["accent"] if self.value else C["muted"]))
        self.btn.setText("  Now" if self.value is None else "  " + friendly_when(self.value))
        self.btn.setProperty("past", self.value is not None)
        self.btn.style().unpolish(self.btn)
        self.btn.style().polish(self.btn)
        self.now_btn.setVisible(self.value is not None)

    def changeEvent(self, event):
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self.btn.setIcon(calendar_icon(C["accent"] if self.value else C["muted"]))
        super().changeEvent(event)

    def open_popup(self):
        """Open the calendar just below the button (or above it near the bottom of the screen)."""
        self.popup = WhenPopup(self, plays.play_dates(self.con))
        self.popup.adjustSize()
        pos = self.btn.mapToGlobal(QPoint(0, self.btn.height() + 4))
        screen = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        size = self.popup.sizeHint()
        x = max(screen.left(), min(pos.x(), screen.right() - size.width()))
        y = pos.y() if pos.y() + size.height() <= screen.bottom() else \
            self.btn.mapToGlobal(QPoint(0, 0)).y() - size.height() - 4
        self.popup.move(x, max(screen.top(), y))
        self.popup.show()
        self.popup.cal.setFocus()
