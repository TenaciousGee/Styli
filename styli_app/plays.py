"""Plays: logging them, the play log, removing them, play counts and CSV export.

Each play is stored as one row per side. Sides logged together share a log id, so the play log
shows them as one entry and Undo removes them together. A full album play is counted each time
every side has been played once."""

import csv

from styli_app import cartridges
from styli_app.common import to_dt


def play_counts(con, record_id=None, since=None):
    """Record id -> {"album": full album plays, "sides": side plays, "last": last played}.

    A full album play is counted each time every side of the record has been played
    in full: playing side A twice and side B once is one album play (three sides).
    Partial plays (stopped early) count as side plays but never towards an album play.
    since (a datetime) counts only plays from then on.
    """
    where, params = ("WHERE record_id = ?", (record_id,)) if record_id is not None else ("WHERE record_id IS NOT NULL", ())
    if since is not None:
        where += " AND played_at >= ?"
        params = params + (since.isoformat(),)
    out = {}
    for r in con.execute("SELECT record_id, COUNT(*) n, MAX(played_at) last FROM plays {} GROUP BY record_id"
                         .format(where), params):
        out[r["record_id"]] = {"album": 0, "sides": r["n"], "last": r["last"]}
    full = {}
    for r in con.execute("SELECT record_id, side, COUNT(*) n FROM plays {} GROUP BY record_id, side".format(where),
                         params):
        full.setdefault(r["record_id"], {})[r["side"]] = r["n"]
    swhere = "WHERE record_id = ?" if record_id is not None else ""
    labels = {}
    for r in con.execute("SELECT record_id, label FROM sides {}".format(swhere), params[:1] if record_id is not None else ()):
        labels.setdefault(r["record_id"], []).append(r["label"])
    for rid, info in out.items():
        sides = labels.get(rid)
        if sides:
            info["album"] = min(full.get(rid, {}).get(lab, 0) for lab in sides)
    return out


def play_entries(sides, chosen, custom=None):
    """(side, seconds) pairs for a play. A stopped-early play becomes one entry with the time given."""
    if custom is not None:
        return [("+".join(chosen) + " (partial)", custom)]
    return [(c, sides[c]["seconds"]) for c in chosen]


def log_play(con, rec, entries, when, times=1, stylus=None):
    """Record a play: one row per side (repeated for 'played it x times'), sharing one log id so they can be shown
    and undone together. Returns (cartridge used, total seconds)."""
    stylus = stylus or cartridges.active_stylus(con)
    label = "{} - {}".format(rec["artist"], rec["title"])
    times = max(1, int(times))
    log_id = con.execute("SELECT COALESCE(MAX(log_id), 0) + 1 FROM plays").fetchone()[0]
    for _ in range(times):
        for side, secs in entries:
            con.execute(
                "INSERT INTO plays (record_id, record_label, side, seconds, played_at, stylus_id, log_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rec["id"], label, side, secs, when.isoformat(), stylus["id"] if stylus else None, log_id))
    con.commit()
    return stylus, sum(s for _, s in entries) * times


def play_log(con, limit=500, stylus_id=None):
    """Logged plays, newest first: one entry each time a play was logged, with all its sides.

    Plays logged before log ids existed are grouped by record, time and cartridge,
    which is how they were logged (see _split_old_log for the rare exceptions).
    """
    where, params = ("WHERE p.stylus_id = ?", [stylus_id]) if stylus_id is not None else ("", [])
    rows = con.execute(
        "SELECT p.*, s.name stylus FROM plays p LEFT JOIN styli s ON s.id = p.stylus_id {} "
        "ORDER BY p.played_at DESC, p.id DESC".format(where), params).fetchall()
    groups, order = {}, []
    for r in rows:
        key = ("log", r["log_id"]) if r["log_id"] is not None else \
            ("old", r["record_id"], r["record_label"], r["played_at"], r["stylus_id"])
        g = groups.get(key)
        if g is None:
            if len(order) >= limit:
                continue
            g = groups[key] = {"entries": [], "played_at": r["played_at"], "record_id": r["record_id"],
                               "record_label": r["record_label"], "stylus": r["stylus"],
                               "stylus_id": r["stylus_id"], "log_id": r["log_id"]}
            order.append(key)
        g["entries"].append((r["id"], r["side"], r["seconds"]))
    out = []
    for key in order:
        g = groups.pop(key)
        entries = sorted(g.pop("entries"))
        chunks = [entries] if key[0] == "log" else _split_old_log(entries)
        for chunk in reversed(chunks):  # newest first
            e = dict(g, ids=[c[0] for c in chunk], sides=[c[1] for c in chunk],
                     seconds=sum(c[2] for c in chunk))
            e["what"] = describe_logged_sides(e["sides"])
            out.append(e)
    return out[:limit]


def _split_old_log(entries):
    """Old plays (before log ids) logged in the same second: keep 'A, B, A, B' as one play repeated,
    but split 'A, B, A' into two plays, as nobody logs that in one go."""
    sides = [e[1] for e in entries]
    n = len(sides)
    for k in range(1, n + 1):
        if n % k == 0 and sides == sides[:k] * (n // k) and len(set(sides[:k])) == k:
            return [entries]
    chunks, seen = [[]], set()
    for e in entries:
        if e[1] in seen:
            chunks.append([])
            seen = set()
        chunks[-1].append(e)
        seen.add(e[1])
    return chunks


def describe_logged_sides(sides):
    """['A', 'B', 'A', 'B'] -> 'Sides A + B, played twice'; ['A (partial)'] -> 'Side A, stopped early'."""
    partial = [s for s in sides if "partial" in s]
    if partial:
        labels = partial[0].replace("(partial)", "").strip().replace("+", " + ")
        text = "{} {}, stopped early".format("Sides" if "+" in labels else "Side", labels)
        times = len(partial)
    else:
        uniq = list(dict.fromkeys(sides))
        times = len(sides) // len(uniq) if uniq and len(sides) % len(uniq) == 0 else 1
        if times == 1:
            uniq = sides
        text = "{} {}".format("Sides" if len(uniq) > 1 else "Side", " + ".join(uniq))
    if times > 1:
        text += ", {}".format({2: "twice", 3: "three times"}.get(times, "{} times".format(times)))
    return text


def remove_plays(con, ids):
    """Delete plays by id. Their time comes off the cartridge / stylus they were logged with."""
    con.executemany("DELETE FROM plays WHERE id = ?", [(i,) for i in ids])
    con.commit()


def write_csv(con, fh):
    """Write every play to a CSV file (when, record, side, minutes, cartridge). Returns how many."""
    rows = con.execute(
        "SELECT p.played_at, p.record_label, p.side, p.seconds, s.name stylus "
        "FROM plays p LEFT JOIN styli s ON s.id = p.stylus_id ORDER BY p.played_at").fetchall()
    w = csv.writer(fh)
    w.writerow(["played_at", "record", "side", "minutes", "stylus"])
    for r in rows:
        w.writerow([r["played_at"], r["record_label"], r["side"], round(r["seconds"] / 60, 2), r["stylus"] or ""])
    return len(rows)


def play_dates(con):
    """Every day that has a play logged on it (for marking them on the calendar)."""
    return {to_dt(r[0]).date() for r in con.execute("SELECT DISTINCT played_at FROM plays")}
