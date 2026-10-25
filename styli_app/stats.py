"""The numbers behind the Stats and Top 10 tabs (and `styli stats` and `styli top`): running totals
over a cartridge / stylus's life, totals per day, week or month (empty ones included, so quiet
spells show), the longest break, when in the week you play, your most played albums, and the
stats on the back of each Top 10 cover."""

import datetime as dt

from styli_app import library, plays
from styli_app.common import now_local, to_dt


TOP_PERIODS = (("all", "All time", None), ("year", "Past 12 months", 365), ("quarter", "Past 3 months", 91),
               ("month", "Past month", 30))


def period_since(key):
    """The start of a Top 10 period ('all', 'year', 'quarter' or 'month'), or None for all time."""
    days = {k: d for k, _, d in TOP_PERIODS}.get(key)
    return now_local() - dt.timedelta(days=days) if days else None


def top_albums(con, limit=10, since=None):
    """Most played records, most first: by full album plays, then sides played, then time,
    then whichever was played most recently."""
    counts = plays.play_counts(con, since=since)
    secs = {}
    q = "SELECT record_id, SUM(seconds) FROM plays WHERE record_id IS NOT NULL"
    for r in con.execute(q + (" AND played_at >= ?" if since else "") + " GROUP BY record_id",
                         (since.isoformat(),) if since else ()):
        secs[r[0]] = r[1]
    order = sorted(counts.items(), key=lambda kv: kv[1]["last"] or "", reverse=True)  # newest first...
    order.sort(key=lambda kv: (-kv[1]["album"], -kv[1]["sides"], -secs.get(kv[0], 0)))  # ...among equals
    out = []
    for rid, c in order[:limit]:
        rec = con.execute("SELECT * FROM records WHERE id = ?", (rid,)).fetchone()
        if rec:
            out.append({"record": rec, "album": c["album"], "sides": c["sides"], "seconds": secs.get(rid, 0),
                        "last": c["last"]})
    return out


def album_stats(con, record_id, since=None):
    """What the Top 10 shows on the back of an album's cover."""
    q = "SELECT side, seconds, played_at, log_id FROM plays WHERE record_id = ?"
    rows = con.execute(q + (" AND played_at >= ?" if since else ""),
                       (record_id, since.isoformat()) if since else (record_id,)).fetchall()
    c = plays.play_counts(con, record_id, since).get(record_id, {"album": 0, "sides": 0, "last": None})
    times = sorted(to_dt(r["played_at"]) for r in rows)
    by_side = {}
    grid = [[[0, 0] for _ in range(24)] for _ in range(7)]
    for r in rows:
        if "partial" not in r["side"]:
            by_side[r["side"]] = by_side.get(r["side"], 0) + 1
        d = to_dt(r["played_at"])
        grid[d.weekday()][d.hour][0] += 1
    today = now_local().date()
    months = []
    y, m = today.year, today.month
    for _ in range(12):
        months.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    months.reverse()
    logs = {}
    for r in rows:  # each time it was put on, counted in the month it was played
        d = to_dt(r["played_at"])
        logs.setdefault((d.year, d.month), set()).add(r["log_id"] if r["log_id"] is not None else r["played_at"])
    per_month = [len(logs.get(ym, ())) for ym in months]
    sides_listed = [s["label"] for s in library.record_sides(con, record_id)]
    fav_side = max(by_side.items(), key=lambda kv: kv[1]) if by_side and len(sides_listed) > 1 else None
    return {"album": c["album"], "sides": c["sides"], "seconds": sum(r["seconds"] for r in rows),
            "listens": len({r["log_id"] for r in rows}), "first": times[0] if times else None,
            "last": times[-1] if times else None, "favourite_side": fav_side,
            "favourite_time": favourite_time(grid), "months": list(zip(months, per_month))}


CHART_UNITS = ("day", "week", "month")

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def stylus_play_times(con, sid):
    """(when played, seconds) for every side played on a cartridge / stylus, oldest first."""
    rows = con.execute("SELECT played_at, seconds FROM plays WHERE stylus_id = ?", (sid,)).fetchall()
    return sorted((to_dt(r[0]), r[1]) for r in rows)


def period_start(day, unit):
    """The first day of the day, week (Monday) or month that a date falls in."""
    if unit == "week":
        return day - dt.timedelta(days=day.weekday())
    if unit == "month":
        return day.replace(day=1)
    return day


def next_period(day, unit):
    """The first day of the following day, week or month."""
    if unit == "week":
        return day + dt.timedelta(days=7)
    if unit == "month":
        return (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return day + dt.timedelta(days=1)


def auto_unit(first, last):
    """Bars by day for a couple of months, by week up to two years, and by month after that."""
    span = (last - first).days
    return "day" if span <= 62 else ("week" if span <= 730 else "month")


def stylus_timeline(con, s, unit=None):
    """Everything the lifetime charts need for one cartridge / stylus.

    points: (when, sides so far, seconds so far), starting at the fitting date with any
    hours it had already done. buckets: one per day, week or month from fitting to
    today (or retirement), including the empty ones, so gaps in your listening show.
    """
    plays = stylus_play_times(con, s["id"])
    installed = to_dt(s["installed_at"])
    end = to_dt(s["retired_at"]) if s["retired_at"] else now_local()
    start = min([installed] + [p[0] for p in plays[:1]])
    end = max([end] + [p[0] for p in plays[-1:]])
    unit = unit if unit in CHART_UNITS else auto_unit(start.date(), end.date())
    points = [(start, 0, s["initial_seconds"])]
    sides, secs = 0, s["initial_seconds"]
    for when, sec in plays:
        sides, secs = sides + 1, secs + sec
        points.append((when, sides, secs))
    buckets, index = [], {}
    day = period_start(start.date(), unit)
    while day <= end.date():
        index[day] = len(buckets)
        buckets.append({"start": day, "end": next_period(day, unit), "sides": 0, "seconds": 0})
        day = next_period(day, unit)
    for when, sec in plays:
        b = buckets[index[period_start(when.date(), unit)]]
        b["sides"] += 1
        b["seconds"] += sec
    return {"unit": unit, "start": start, "end": end, "installed": installed, "points": points,
            "buckets": buckets, "initial": s["initial_seconds"], "rated": s["rated_hours"] * 3600,
            "warn_pct": s["warn_pct"], "sides": sides, "seconds": secs,
            "gap": longest_break([p[0] for p in plays], end, ongoing=not s["retired_at"])}


def longest_break(times, end, ongoing=True):
    """The longest run of days with nothing played, between plays (and up to today if still in use).
    Returns (days, first quiet day, last quiet day) or None."""
    days = sorted({t.date() for t in times})
    if not days:
        return None
    best = None
    edges = list(zip(days, days[1:]))
    if ongoing:
        edges.append((days[-1], end.date() + dt.timedelta(days=1)))
    for a, b in edges:
        quiet = (b - a).days - 1
        if quiet > 0 and (best is None or quiet > best[0]):
            best = (quiet, a + dt.timedelta(days=1), b - dt.timedelta(days=1))
    return best


DAY_PARTS = (("night", range(0, 5)), ("morning", range(5, 12)), ("afternoon", range(12, 17)),
             ("evening", range(17, 22)), ("night", range(22, 24)))


def favourite_time(grid):
    """The part of the week you play most, e.g. ("Saturday", "evening", sides), or None."""
    best = None
    for d in range(7):
        totals = {}
        for part, hours in DAY_PARTS:
            totals[part] = totals.get(part, 0) + sum(grid[d][h][0] for h in hours)
        for part, n in totals.items():
            if n and (best is None or n > best[2]):
                best = (WEEKDAYS[d], part, n)
    return best


def play_heatmap(con, sid=None):
    """Sides played and seconds by weekday (0 = Monday) and hour, at the time on the clock
    when they were played. grid[day][hour] = [sides, seconds]."""
    grid = [[[0, 0] for _ in range(24)] for _ in range(7)]
    rows = con.execute("SELECT played_at, seconds FROM plays" + (" WHERE stylus_id = ?" if sid else ""),
                       (sid,) if sid else ()).fetchall()
    for r in rows:
        d = to_dt(r[0])
        cell = grid[d.weekday()][d.hour]
        cell[0] += 1
        cell[1] += r[1]
    return grid
