"""Reports: status, top and stats."""

from styli_app import cartridges, stats
from styli_app.common import err, fmt_hours, fmt_plays


# ---- reports: status, the library, Top 10, stats, history and the play log ---------------------------

def cmd_status(con, args):
    """styli status: wear on each cartridge / stylus in use, and a listening summary."""
    in_use = cartridges.styli_in_use(con)
    cur = cartridges.active_stylus(con)
    if not in_use:
        print("No cartridge / stylus in use. Add one with: styli stylus add \"Name\" --hours <rated life>")
    for n, s in enumerate(in_use):
        m = cartridges.stylus_summary(con, s)
        print("{}Cartridge / stylus #{}  {}  (installed {}){}".format("\n" if n else "", s["id"], s["name"], m["installed"].date(),
                                                       "  <- on the turntable" if cur and s["id"] == cur["id"] else ""))
        print("  Used       {} of {:g} h  ({:.1f}%)".format(fmt_hours(m["used"]), s["rated_hours"], m["pct"]))
        print("  {}".format(cartridges.stylus_bar(m["pct"])))
        print("  Remaining  {}".format(fmt_hours(m["remaining"])))
        print("  Sides played on this cartridge / stylus: {}".format(m["sides_played"]))
        if m["per_week_h"] > 0:
            line = "  Recent rate {:.1f} h/week".format(m["per_week_h"])
            if m["eta"]:
                line += ", rated life reached around {}".format(m["eta"].strftime("%B %Y"))
            print(line)
        if m["level"] == "over":
            print("  !! Rated life reached. Inspect or replace the cartridge / stylus.")
        elif m["level"] == "warn":
            print("  ! Past the {:g}% warning level.".format(s["warn_pct"]))
    retired = con.execute("SELECT COUNT(*) FROM styli WHERE active = 0").fetchone()[0]
    if retired:
        print("\n{} retired cartridge{} / styl{} (see `styli stylus list`).".format(
            retired, "" if retired == 1 else "s", "us" if retired == 1 else "i"))
    n_rec = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    n_all = con.execute("SELECT COUNT(*), COALESCE(SUM(seconds), 0) FROM plays").fetchone()
    print("\nLibrary: {} records. All-time: {} sides, {}.".format(n_rec, n_all[0], fmt_hours(n_all[1])))
    top = con.execute(
        "SELECT record_label, COUNT(*) n, SUM(seconds) t FROM plays "
        "GROUP BY record_label ORDER BY t DESC LIMIT 5").fetchall()
    if top:
        print("Most played:")
        for r in top:
            print("  {:>6}  {}  ({} sides)".format(fmt_hours(r["t"]), r["record_label"], r["n"]))
    return 0


def cmd_top(con, args):
    """styli top: your most played albums, all time or over a recent period."""
    since = stats.period_since(args.period)
    top = stats.top_albums(con, args.n, since)
    name = {k: n for k, n, _ in stats.TOP_PERIODS}[args.period]
    if not top:
        print("Nothing played {}yet.".format("in that time " if since else ""))
        return 0
    print("Top {} ({}):".format(len(top), name.lower()))
    for i, t in enumerate(top, 1):
        r = t["record"]
        print("  {:>2}. {} - {}  {}, {}".format(i, r["artist"], r["title"], fmt_plays(t["album"], t["sides"]),
                                               fmt_hours(t["seconds"])))
    return 0


def cmd_stats(con, args):
    """styli stats: a cartridge / stylus's plays over its life and when in the week you play, as text charts."""
    s = cartridges.find_stylus(con, " ".join(args.stylus)) if args.stylus else cartridges.active_stylus(con)
    if not s:
        err("no cartridge / stylus found." if args.stylus else "no cartridge / stylus is on the turntable.")
        return 1
    t = stats.stylus_timeline(con, s, args.by)
    print("{}: {} sides played, {} of {:g} h since {}".format(
        s["name"], t["sides"], fmt_hours(t["seconds"]), s["rated_hours"], t["installed"].strftime("%d %b %Y")))
    fmts = {"day": "%a %d %b", "week": "w/c %d %b %y", "month": "%b %Y"}
    top = max([b["sides"] for b in t["buckets"]] + [1])
    totals, run = [], 0
    for b in t["buckets"]:
        run += b["sides"]
        totals.append(run)
    shown = list(zip(t["buckets"], totals))[-60:]
    print("\nSides played each {} (running total on the right{}):".format(
        t["unit"], ", latest 60" if len(t["buckets"]) > 60 else ""))
    for b, total in shown:
        print("  {:<14} {:<30} {:>3}  {:>5}".format(b["start"].strftime(fmts[t["unit"]]),
                                                   "#" * int(round(30 * b["sides"] / top)), b["sides"] or "", total))
    if t["gap"]:
        print("Longest break: {} day{} ({} to {})".format(t["gap"][0], "" if t["gap"][0] == 1 else "s",
                                                       t["gap"][1].strftime("%d %b %Y"), t["gap"][2].strftime("%d %b %Y")))
    grid = stats.play_heatmap(con, s["id"])
    fav = stats.favourite_time(grid)
    if fav:
        print("You play most on {} {}s ({} sides).".format(fav[0], fav[1], fav[2]))
    most = max(c[0] for row in grid for c in row) or 1
    shades = " .:-=+*#%@"
    print("\nWhen you play (sides, by day and hour):")
    print("       " + "".join(str(h // 10) if h % 3 == 0 else " " for h in range(24)))
    print("       " + "".join(str(h % 10) if h % 3 == 0 else " " for h in range(24)))
    for d, row in enumerate(grid):
        line = "".join(shades[int(round((len(shades) - 1) * c[0] / most))] if c[0] else "\u00b7" for c in row)
        print("  {}  {}  {}".format(stats.WEEKDAYS[d][:3], line, sum(c[0] for c in row)))
    return 0
