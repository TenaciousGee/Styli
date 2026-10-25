"""Commands for plays: play, history, log, undo and export."""

import re
import sys

from styli_app import cartridges, library, plays
from styli_app.common import confirm, err, fmt_dur, fmt_hours, parse_duration, parse_when, to_dt


# ---- logging plays -----------------------------------------------------------------------------------

def cmd_play(con, args):
    """styli play: log sides of a record as played (now, or at the time given)."""
    rec = library.resolve_record(con, args.record)
    if not rec:
        return 1
    sides = {s["label"]: s for s in library.record_sides(con, rec["id"])}
    tokens = [t.upper() for s in args.sides for t in re.split(r"[,\s]+", s) if t]
    if not tokens or "ALL" in tokens:
        chosen = list(sides)
    else:
        chosen = []
        for t in tokens:
            if t in sides:
                chosen.append(t)
            elif all(ch in sides for ch in t):  # "AB" shorthand
                chosen.extend(t)
            else:
                err("{} has no side '{}'. Sides: {}".format(rec["title"], t, ", ".join(sides)))
                return 1
    try:
        when = parse_when(args.when)
        custom = parse_duration(args.duration) if args.duration else None
    except ValueError as e:
        err(str(e))
        return 1
    entries = plays.play_entries(sides, chosen, custom)
    chosen_stylus = None
    if args.stylus:
        chosen_stylus = cartridges.find_stylus(con, args.stylus)
        if not chosen_stylus:
            err("no cartridge / stylus matches '{}'. See `styli stylus list`.".format(args.stylus))
            return 1
    stylus, total = plays.log_play(con, rec, entries, when, args.times, chosen_stylus)
    what = ", ".join("side {} ({})".format(s, fmt_dur(sec)) for s, sec in entries)
    times = " x{}".format(args.times) if args.times > 1 else ""
    print("Logged {}{} of {} - {}: {}".format(what, times, rec["artist"], rec["title"], fmt_dur(total)))
    if stylus:
        print(cartridges.stylus_alert(con, stylus))
    else:
        print("No cartridge / stylus set up, so this play is not counted against one. Add one with `styli stylus add`.")
    return 0


def cmd_history(con, args):
    """styli history: recent plays, one line per side."""
    where, params = "", []
    if args.stylus:
        s = cartridges.find_stylus(con, args.stylus)
        if not s:
            err("no cartridge / stylus matches '{}'.".format(args.stylus))
            return 1
        where, params = "WHERE p.stylus_id = ?", [s["id"]]
        m = cartridges.stylus_stats(con, s)
        print("{}: {} over {} sides.\n".format(s["name"], fmt_hours(m["used"]), m["sides_played"]))
    rows = con.execute(
        "SELECT p.*, s.name stylus FROM plays p LEFT JOIN styli s ON s.id = p.stylus_id {} "
        "ORDER BY p.played_at DESC, p.id DESC LIMIT ?".format(where), params + [args.n]).fetchall()
    if not rows:
        print("No plays logged yet.")
    for p in rows:
        print("{}  {:>7}  side {:<6} {}{}".format(
            to_dt(p["played_at"]).strftime("%Y-%m-%d %H:%M"), fmt_dur(p["seconds"]), p["side"],
            p["record_label"], "  [{}]".format(p["stylus"]) if p["stylus"] else ""))
    return 0


def _latest_logged(con, n):
    """The n most recently logged plays (by when they were logged, not when they were played)."""
    log = plays.play_log(con, limit=100000)
    return sorted(log, key=lambda g: -max(g["ids"]))[:n]


def cmd_log(con, args):
    """styli log: the play log, one line each time you logged a play."""
    log = plays.play_log(con, limit=args.n)
    if not log:
        print("No plays logged yet.")
        return 0
    for n, g in enumerate(log, 1):
        print("{:>3}. {}  {:<34} {:<38} {:>8}  {}".format(
            n, to_dt(g["played_at"]).strftime("%Y-%m-%d %H:%M"), g["record_label"][:34], g["what"][:38],
            fmt_dur(g["seconds"]), g["stylus"] or ""))
    print("\nRemove one with: styli undo <number>   (styli undo on its own removes the last play you logged)")
    return 0


def cmd_undo(con, args):
    """styli undo: remove the last play logged, or a numbered one from styli log."""
    if args.number:
        log = plays.play_log(con, limit=args.number)
        if len(log) < args.number:
            err("there's no play {} in the log. See `styli log`.".format(args.number))
            return 1
        chosen = [log[args.number - 1]]
    else:
        chosen = _latest_logged(con, args.n)
    if not chosen:
        print("Nothing to undo.")
        return 0
    for g in chosen:
        print("  {}  {}: {} ({}){}".format(to_dt(g["played_at"]).strftime("%Y-%m-%d %H:%M"), g["record_label"],
                                         g["what"], fmt_dur(g["seconds"]),
                                         ", with " + g["stylus"] if g["stylus"] else ""))
    if not confirm("Remove {}? The time comes off the cartridge / stylus it was logged with.".format(
            "this play" if len(chosen) == 1 else "these {} plays".format(len(chosen)))):
        return 1
    plays.remove_plays(con, [i for g in chosen for i in g["ids"]])
    print("Removed.")
    return 0


def cmd_export(con, args):
    """styli export: write every play to a CSV file."""
    if args.file == "-":
        plays.write_csv(con, sys.stdout)
        return 0
    with open(args.file, "w", newline="", encoding="utf-8") as fh:
        n = plays.write_csv(con, fh)
    print("Exported {} plays to {}".format(n, args.file))
    return 0
