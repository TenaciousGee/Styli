"""Charts for the Stats tab, drawn with QPainter and coloured from the theme: the running total
over a cartridge / stylus's life, totals per day, week or month, and the weekly heatmap. They
share the window's height, show fewer gridlines when short, and give exact figures in a tooltip
as you move over them."""

import datetime as dt
import math

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QToolTip, QWidget

from styli_app import common, stats
from styli_app.gui.theme import C


def nice_ticks(top, count=4):
    """Round numbers from 0 up to at least top, about count steps."""
    if top <= 0:
        return [0, 1]
    raw = top / float(count)
    mag = 10 ** math.floor(math.log10(raw))
    step = mag * 10
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            step = m * mag
            break
    return [i * step for i in range(int(math.ceil(top / step - 1e-9)) + 1)]


def mix(a, b, t):
    """A colour t of the way from a to b."""
    a, b = QColor(a), QColor(b)
    t = max(0.0, min(1.0, t))
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def faded(colour, alpha):
    """A theme colour made partly see-through (alpha 0-255)."""
    c = QColor(colour)
    c.setAlpha(alpha)
    return c


def measure_value(measure, sides, seconds):
    """Sides played, or hours, depending on what the Stats tab is showing."""
    return sides if measure == "sides" else seconds / 3600.0


def fmt_measure(measure, value, short=False):
    """A value as '12 sides' or '3.4 hours' ('12' or '3.4 h' when short, for axis labels)."""
    if measure == "sides":
        n = int(round(value))
        return str(n) if short else "{} side{}".format(n, "" if n == 1 else "s")
    if short:
        return "{:g} h".format(round(value, 1)) if value < 100 else "{:,.0f} h".format(value)
    return "{:.1f} hours".format(value) if value < 100 else "{:,.0f} hours".format(value)


PERIOD_NAMES = {"day": "day", "week": "week", "month": "month"}


def period_label(start, unit, long=False):
    """A day, week or month for chart labels ('6 Oct') and tooltips ('Week of 6 Oct 2026')."""
    if unit == "month":
        return start.strftime("%B %Y" if long else "%b %Y")
    if unit == "week":
        return ("Week of " if long else "") + start.strftime("%d %b %Y" if long else "%d %b").lstrip("0")
    return start.strftime("%A %d %b %Y" if long else "%d %b").replace(" 0", " ").lstrip("0")


def date_ticks(start, end, width):
    """Calendar-aligned dates to label along a time axis, spaced at least ~90 pixels apart."""
    days = max(1, (end.date() - start.date()).days)
    room = max(2, int(width / 92))
    for step in (1, 2, 7, 14):
        if days / step <= room:
            first = start.date()
            if step == 7:
                first += dt.timedelta(days=(7 - first.weekday()) % 7)  # Mondays
            out, d = [], first
            while d <= end.date():
                out.append((d, d.strftime("%d %b").lstrip("0")))
                d += dt.timedelta(days=step)
            return out
    months = days / 30.4
    for step in (1, 2, 3, 6, 12, 24, 60):
        if months / step <= room:
            break
    out = []
    d = (start.date().replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    while d <= end.date():
        if (d.year * 12 + d.month - 1) % step == 0:
            out.append((d, d.strftime("%Y") if step >= 12 else d.strftime("%b %Y")))
        d = (d + dt.timedelta(days=32)).replace(day=1)
    return out


class ChartBase(QWidget):
    """What the Stats charts share: margins, the y axis and gridlines, hover tooltips and empty messages."""
    LEFT, RIGHT, TOP, BOTTOM = 52, 14, 18, 24

    def __init__(self, min_height=110, preferred=240):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumHeight(min_height)  # charts grow and shrink with the window, down to this
        self.preferred = preferred
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.hover = None
        self.measure = "sides"
        self.empty = "No plays logged with this cartridge / stylus yet."

    def sizeHint(self):
        # Ask only for the minimum: the page then fits the window whenever it can, and the spare
        # height is shared between the charts (in proportion to self.preferred) by the layout.
        return QSize(600, self.minimumHeight())

    def small_font(self, bold=False):
        """The slightly smaller font used for axis labels."""
        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() * 0.86))
        f.setBold(bold)
        return f

    def plot_rect(self):
        """The area inside the axes where data is drawn."""
        return QRectF(self.LEFT, self.TOP, max(10, self.width() - self.LEFT - self.RIGHT),
                      max(10, self.height() - self.TOP - self.BOTTOM))

    def tick_count(self):
        """Fewer gridlines on a short chart, so the axis labels never run into each other."""
        return max(1, min(4, int(self.plot_rect().height() // 24)))

    def leaveEvent(self, event):
        self.hover = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def draw_y_axis(self, p, r, ticks, top):
        """Horizontal gridlines with their values down the left."""
        p.setFont(self.small_font())
        for v in ticks:
            y = r.bottom() - r.height() * v / top
            p.setPen(QPen(faded(C["border"], 255 if v == 0 else 150), 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(0, y - 9, self.LEFT - 10, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       fmt_measure(self.measure, v, short=True))

    def draw_empty(self, p):
        """A centred message when there's nothing to draw."""
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, self.empty)

    def show_tip(self, event, text):
        """Show a tooltip by the mouse pointer."""
        QToolTip.showText(event.globalPosition().toPoint(), text, self)


class CumulativeChart(ChartBase):
    """Sides (or hours) played so far, over the whole life of the cartridge / stylus."""

    def __init__(self):
        super().__init__(95, 260)
        self.t = None

    def set_data(self, t, measure):
        """Show a timeline from core.stylus_timeline as sides or hours."""
        self.t, self.measure, self.hover = t, measure, None
        self.update()

    def _series(self):
        """The running totals as (when, value) in the current measure."""
        t = self.t
        return [(w, measure_value(self.measure, s, sec)) for w, s, sec in t["points"]]

    def _limits(self):
        """(y top, threshold lines to draw) for the current data."""
        t = self.t
        top = max(v for _, v in self._series())
        lines = []
        if self.measure == "hours" and t["rated"]:
            rated = t["rated"] / 3600.0
            warn = rated * t["warn_pct"] / 100.0
            if warn <= top * 1.6:  # only when it's close enough not to flatten the line
                lines.append((warn, "{:g}% of rated life".format(t["warn_pct"]), C["warn"]))
            if rated <= top * 1.6:
                lines.append((rated, "Rated life, {:g} h".format(rated), C["over"]))
        top = max([top] + [v for v, _, _ in lines])
        ticks = nice_ticks(top * 1.05 if top else 1, self.tick_count())
        return ticks, lines

    def _x(self, r, when):
        """The x position of a moment in time."""
        t = self.t
        span = max(1.0, (t["end"] - t["start"]).total_seconds())
        return r.left() + r.width() * (when - t["start"]).total_seconds() / span

    def paintEvent(self, event):
        """Draw the gridlines, date labels, the stepped running total with its shading, the wear lines (in Hours),
        and the hover marker."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.t:
            self.draw_empty(p)
            return
        r = self.plot_rect()
        ticks, lines = self._limits()
        top = ticks[-1]
        self.draw_y_axis(p, r, ticks, top)
        p.setFont(self.small_font())
        p.setPen(QColor(C["muted"]))
        for d, text in date_ticks(self.t["start"], self.t["end"], r.width()):
            x = self._x(r, dt.datetime.combine(d, dt.time()).astimezone())
            if r.left() - 1 <= x <= r.right() + 1:
                p.drawText(QRectF(x - 46, r.bottom() + 4, 92, 18), Qt.AlignmentFlag.AlignHCenter, text)
        series = self._series()
        y = lambda v: r.bottom() - r.height() * v / top
        path = QPainterPath(QPointF(r.left(), y(series[0][1])))
        last = series[0][1]
        for when, v in series[1:]:
            x = self._x(r, when)
            path.lineTo(QPointF(x, y(last)))
            path.lineTo(QPointF(x, y(v)))
            last = v
        path.lineTo(QPointF(r.right(), y(last)))
        area = QPainterPath(path)
        area.lineTo(QPointF(r.right(), r.bottom()))
        area.lineTo(QPointF(r.left(), r.bottom()))
        area.closeSubpath()
        p.fillPath(area, faded(C["accent"], 38))
        pen = QPen(QColor(C["accent"]), 2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        for v, text, colour in lines:
            pen = QPen(QColor(colour), 1.5, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(r.left(), y(v)), QPointF(r.right(), y(v)))
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(r.left() + 6, y(v) - 18, r.width() - 12, 16),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, text)
        if self.hover is not None:
            x, when, v = self.hover
            p.setPen(QPen(faded(C["muted"], 160), 1))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(QPen(QColor(C["surface"]), 2))
            p.setBrush(QColor(C["accent"]))
            p.drawEllipse(QPointF(x, y(v)), 5, 5)
        p.end()

    def mouseMoveEvent(self, event):
        """Show the running total at the date under the pointer."""
        if not self.t:
            return
        r = self.plot_rect()
        x = min(max(event.position().x(), r.left()), r.right())
        span = (self.t["end"] - self.t["start"]).total_seconds()
        when = self.t["start"] + dt.timedelta(seconds=span * (x - r.left()) / max(1.0, r.width()))
        value = 0
        for w, v in self._series():
            if w <= when:
                value = v
            else:
                break
        self.hover = (x, when, value)
        self.update()
        self.show_tip(event, "{}\n{} so far".format(when.strftime("%a %d %b %Y").replace(" 0", " "),
                                                     fmt_measure(self.measure, value)))


class PeriodChart(ChartBase):
    """Sides (or hours) played each day, week or month: peaks stand out and quiet spells show as gaps."""

    def __init__(self):
        super().__init__(90, 240)
        self.t = None

    def set_data(self, t, measure):
        """Show a timeline from core.stylus_timeline as sides or hours."""
        self.t, self.measure, self.hover = t, measure, None
        self.update()

    def _values(self):
        """Each period's total in the current measure."""
        return [measure_value(self.measure, b["sides"], b["seconds"]) for b in self.t["buckets"]]

    def _slot(self, r):
        """The width each bar (and the gap beside it) gets."""
        return r.width() / max(1, len(self.t["buckets"]))

    def paintEvent(self, event):
        """Draw the gridlines, a bar per period (rounded at the top), the labels along the bottom and the Peak
        label on the busiest bar."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.t or not self.t["buckets"]:
            self.draw_empty(p)
            return
        r = self.plot_rect()
        values = self._values()
        peak = max(values)
        ticks = nice_ticks(peak * 1.12 if peak else 1, self.tick_count())
        top = ticks[-1]
        self.draw_y_axis(p, r, ticks, top)
        slot = self._slot(r)
        gap = 2 if slot >= 6 else (1 if slot >= 3 else 0)
        radius = 4 if slot - gap >= 8 else (2 if slot - gap >= 4 else 0)
        peak_i = values.index(peak) if peak else None
        if self.hover is not None:
            p.fillRect(QRectF(r.left() + self.hover * slot, r.top(), slot, r.height()), faded(C["accent"], 22))
        p.setPen(Qt.PenStyle.NoPen)
        for i, v in enumerate(values):
            if v <= 0:
                continue
            h = max(2.0, r.height() * v / top)
            bar = QRectF(r.left() + i * slot + gap / 2.0, r.bottom() - h, max(1.0, slot - gap), h)
            p.setBrush(QColor(C["accent_hover"] if i == self.hover else C["accent"]))
            if radius and bar.height() > radius:  # rounded at the top, square on the baseline
                p.drawRoundedRect(bar, radius, radius)
                p.drawRect(bar.adjusted(0, radius, 0, 0))
            else:
                p.drawRect(bar)
        p.setFont(self.small_font())
        p.setPen(QColor(C["muted"]))
        unit = self.t["unit"]
        every = max(1, int(math.ceil(78 / max(1.0, slot))))
        if unit == "month" and every > 1:
            every = next(n for n in (1, 2, 3, 6, 12, 24, 60) if n >= every)
        for i, b in enumerate(self.t["buckets"]):
            if unit == "month":
                show = (b["start"].month - 1) % every == 0 if every in (2, 3, 6, 12) else i % every == 0
                text = b["start"].strftime("%Y") if every >= 12 else b["start"].strftime("%b %Y")
            else:
                show = i % every == 0
                text = period_label(b["start"], unit)
            if show:
                x = r.left() + (i + 0.5) * slot
                p.drawText(QRectF(x - 46, r.bottom() + 4, 92, 18), Qt.AlignmentFlag.AlignHCenter, text)
        if peak_i is not None:  # label the busiest period only
            x = r.left() + (peak_i + 0.5) * slot
            ytop = r.bottom() - r.height() * peak / top
            p.setFont(self.small_font(bold=True))
            p.setPen(QColor(C["text"]))
            text = "Peak: " + fmt_measure(self.measure, peak, short=True)
            w = QFontMetrics(p.font()).horizontalAdvance(text) + 8
            lx = min(max(x - w / 2, r.left()), r.right() - w)
            p.drawText(QRectF(lx, ytop - 18, w, 16), Qt.AlignmentFlag.AlignCenter, text)
        p.end()

    def mouseMoveEvent(self, event):
        """Highlight the bar under the pointer and show its period and total."""
        if not self.t or not self.t["buckets"]:
            return
        r = self.plot_rect()
        i = int((event.position().x() - r.left()) / self._slot(r))
        if not 0 <= i < len(self.t["buckets"]) or not r.top() - 4 <= event.position().y() <= r.bottom() + 4:
            if self.hover is not None:
                self.hover = None
                self.update()
                QToolTip.hideText()
            return
        if i != self.hover:
            self.hover = i
            self.update()
        b = self.t["buckets"][i]
        if b["sides"]:
            text = "{}\n{} ({})".format(period_label(b["start"], self.t["unit"], long=True),
                                        fmt_measure("sides", b["sides"]), common.fmt_dur(b["seconds"]))
        else:
            text = "{}\nNothing played".format(period_label(b["start"], self.t["unit"], long=True))
        self.show_tip(event, text)


class HeatmapChart(ChartBase):
    """When in the week you play: a row for each day, a column for each hour."""

    LEFT, RIGHT, TOP, BOTTOM = 44, 44, 20, 26
    MIN_ROW, MAX_ROW = 13, 34

    def __init__(self):
        super().__init__(self.TOP + 7 * self.MIN_ROW + self.BOTTOM, self.TOP + 7 * 26 + self.BOTTOM)
        self.setMaximumHeight(self.TOP + 7 * self.MAX_ROW + self.BOTTOM)
        self.grid = None

    @property
    def ROW(self):
        """Row height: the heatmap grows and shrinks with the space it's given."""
        return max(self.MIN_ROW, min(self.MAX_ROW, (self.height() - self.TOP - self.BOTTOM) / 7.0))

    def set_data(self, grid, measure):
        """Show a grid from core.play_heatmap as sides or hours."""
        self.grid, self.measure, self.hover = grid, measure, None
        self.update()

    def _value(self, d, h):
        """One hour's total in the current measure."""
        c = self.grid[d][h]
        return measure_value(self.measure, c[0], c[1])

    def _cell(self):
        """The width of each hour's square."""
        return (self.width() - self.LEFT - self.RIGHT) / 24.0

    def colour(self, v, top):
        """A square's colour: empty for none, then from faint to the full accent colour at the busiest hour."""
        if v <= 0:
            return QColor(C["surface2"])
        return mix(C["surface3"], C["accent"], 0.22 + 0.78 * v / top)

    def paintEvent(self, event):
        """Draw the hour labels, a row of squares for each day with its total, and the key."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.grid:
            self.draw_empty(p)
            return
        top = max(self._value(d, h) for d in range(7) for h in range(24))
        cw = self._cell()
        p.setFont(self.small_font())
        p.setPen(QColor(C["muted"]))
        for h in range(0, 24, 3):
            p.drawText(QRectF(self.LEFT + h * cw - 4, 0, 40, self.TOP - 4),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, "{:02d}:00".format(h))
        for d in range(7):
            y = self.TOP + d * self.ROW
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(0, y, self.LEFT - 8, self.ROW - 2), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       stats.WEEKDAYS[d][:3])
            total = sum(self._value(d, h) for h in range(24))
            p.drawText(QRectF(self.width() - self.RIGHT + 6, y, self.RIGHT - 6, self.ROW - 2),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fmt_measure(self.measure, total, short=True) if total else "")
            p.setPen(Qt.PenStyle.NoPen)
            for h in range(24):
                v = self._value(d, h)
                cell = QRectF(self.LEFT + h * cw + 1, y + 1, cw - 2, self.ROW - (4 if self.ROW > 18 else 2))
                p.setBrush(self.colour(v, top or 1))
                p.drawRoundedRect(cell, 4, 4)
                if self.hover == (d, h):
                    p.setPen(QPen(QColor(C["text"]), 1.5))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRoundedRect(cell.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
                    p.setPen(Qt.PenStyle.NoPen)
        # key: fewer to more
        y = self.TOP + 7 * self.ROW + 5
        p.setPen(QColor(C["muted"]))
        fm = QFontMetrics(p.font())
        x = self.width() - self.RIGHT
        steps = [0, 0.25, 0.5, 0.75, 1.0]
        more_w = fm.horizontalAdvance("More")
        x -= more_w
        p.drawText(QRectF(x, y, more_w, 16), Qt.AlignmentFlag.AlignVCenter, "More")
        x -= 6 + 5 * 16
        sx = x
        p.setPen(Qt.PenStyle.NoPen)
        for s in steps:
            p.setBrush(self.colour(s * (top or 1), top or 1) if s else QColor(C["surface2"]))
            p.drawRoundedRect(QRectF(sx, y + 2, 13, 13), 3, 3)
            sx += 16
        fewer_w = fm.horizontalAdvance("None")
        p.setPen(QColor(C["muted"]))
        p.drawText(QRectF(x - fewer_w - 6, y, fewer_w, 16), Qt.AlignmentFlag.AlignVCenter, "None")
        p.end()

    def mouseMoveEvent(self, event):
        """Outline the square under the pointer and show that hour's plays."""
        if not self.grid:
            return
        cw = self._cell()
        h = int((event.position().x() - self.LEFT) / cw) if event.position().x() >= self.LEFT else -1
        d = int((event.position().y() - self.TOP) / self.ROW) if event.position().y() >= self.TOP else -1
        if not (0 <= h < 24 and 0 <= d < 7):
            if self.hover is not None:
                self.hover = None
                self.update()
                QToolTip.hideText()
            return
        if (d, h) != self.hover:
            self.hover = (d, h)
            self.update()
        sides, secs = self.grid[d][h]
        text = "{}s, {:02d}:00 to {:02d}:00\n{}".format(
            stats.WEEKDAYS[d], h, (h + 1) % 24,
            "{} ({})".format(fmt_measure("sides", sides), common.fmt_dur(secs)) if sides else "Nothing played")
        self.show_tip(event, text)
