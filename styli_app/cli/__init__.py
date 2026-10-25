"""The command line: the options and sub-commands (what `styli --help` shows), and main(), which
runs one command.

Each sub-command is a cmd_<name> function in one of the cmd_*.py modules here, listed in
COMMANDS. It gets the open database and the parsed options, prints its result and returns an
exit code (0 for success). Running styli with no command opens the window when started from a
menu, or shows the status in a terminal."""

import argparse
import sys

from styli_app import common, db, paths, stats
from styli_app.cli import cmd_cartridges
from styli_app.cli import cmd_plays
from styli_app.cli import cmd_records
from styli_app.cli import cmd_reports
from styli_app.cli import cmd_setup
from styli_app.common import VERSION, err


def build_parser():
    """The command-line options and sub-commands (what styli --help shows)."""
    p = argparse.ArgumentParser(
        prog="styli",
        description="Log vinyl plays by side and track cartridge / stylus wear. Running times come from MusicBrainz.",
        epilog="Examples:\n"
               "  styli stylus add \"Ortofon 2M Red\" --hours 1000\n"
               "  styli add --artist \"the rolling stones\" --title \"some girls\"\n"
               "  styli add fleetwood mac rumours\n"
               "  styli play \"dark side\"          (both sides)\n"
               "  styli play 3 B                  (side B of record #3)\n"
               "  styli status\n"
               "  styli stats                     (lifetime chart and weekly heatmap)\n"
               "  styli top --period quarter      (most played albums, past 3 months)\n"
               "  styli gui                       (open the window)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(paths.default_db_path()), help="database file (default: %(default)s)")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask questions; accept defaults")
    p.add_argument("--version", action="version", version="Styli " + VERSION)
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("add", help="add a record, fetching side lengths from MusicBrainz")
    a.add_argument("query", nargs="*", help="artist's full name then album, e.g. fleetwood mac rumours")
    a.add_argument("--artist")
    a.add_argument("--title")
    a.add_argument("--barcode", help="search by the barcode on the sleeve")
    a.add_argument("--mbid", help="use an exact MusicBrainz release ID")
    a.add_argument("--any-format", action="store_true", help="list CD, cassette etc. pressings too")
    a.add_argument("--manual", action="store_true", help="skip MusicBrainz and type side lengths")
    a.add_argument("--side", action="append", metavar="LABEL=mm:ss", help="manual side length (repeatable)")
    a.add_argument("--year")
    a.add_argument("--discogs", action="store_true", help="choose from your linked Discogs collection")
    a.add_argument("--discogs-id", type=int, help="add this Discogs release number")

    pl = sub.add_parser("play", help="log a play of one or more sides")
    pl.add_argument("record", help="record number or words from artist/title")
    pl.add_argument("sides", nargs="*", help="sides played, e.g. A, B or AB (default: all)")
    pl.add_argument("--when", help="when it was played, e.g. 'yesterday 21:00' or '2026-10-11 20:30'")
    pl.add_argument("--duration", help="log a custom length instead (needle lifted early), mm:ss")
    pl.add_argument("--times", type=int, default=1, help="log the same play several times")
    pl.add_argument("--stylus", "--cartridge", dest="stylus", help="cartridge / stylus used, by number or name (default: the one on the turntable)")

    sub.add_parser("status", help="show cartridge / stylus wear and listening summary")

    tp = sub.add_parser("top", help="your most played albums")
    tp.add_argument("-n", type=int, default=10, help="how many (default 10)")
    tp.add_argument("--period", choices=[k for k, _, _ in stats.TOP_PERIODS], default="all",
                    help="all time (default), or the past year, quarter (3 months) or month")

    stt = sub.add_parser("stats", help="lifetime chart and weekly heatmap for a cartridge / stylus")
    stt.add_argument("stylus", nargs="*", help="cartridge / stylus by number or name (default: the one on the turntable)")
    stt.add_argument("--by", choices=stats.CHART_UNITS, help="group plays by day, week or month (default: picked from the lifetime)")

    inf = sub.add_parser("info", help="summary and track listing of a record")
    inf.add_argument("record")
    inf.add_argument("--refresh", action="store_true", help="fetch the details again")

    l = sub.add_parser("list", help="list records in your library")
    l.add_argument("filter", nargs="*")

    h = sub.add_parser("history", help="show recent plays")
    h.add_argument("-n", type=int, default=20)
    h.add_argument("--stylus", "--cartridge", dest="stylus", help="only plays with this cartridge / stylus (number or name), retired ones too")

    lg = sub.add_parser("log", help="the play log: one line each time you logged a play")
    lg.add_argument("-n", type=int, default=30)

    u = sub.add_parser("undo", help="remove a logged play (the last one, or a number from `styli log`)")
    u.add_argument("number", nargs="?", type=int, help="number from `styli log`")
    u.add_argument("-n", type=int, default=1, help="remove the last N logged plays")

    sd = sub.add_parser("side", help="set or correct the length of a side")
    sd.add_argument("record")
    sd.add_argument("label")
    sd.add_argument("length", nargs="?", help="mm:ss")
    sd.add_argument("--delete", action="store_true")

    rm = sub.add_parser("remove", help="remove a record (its logged plays are kept)")
    rm.add_argument("record")

    ex = sub.add_parser("export", help="export plays to CSV")
    ex.add_argument("file", nargs="?", default="vinyl-plays.csv", help="file name, or - for screen")

    sub.add_parser("gui", help="open the window")
    sub.add_parser("covers", help="save album covers for any records that don't have one yet")
    dg = sub.add_parser("discogs", help="link your Discogs account and browse your collection")
    dgs = dg.add_subparsers(dest="discogs_cmd")
    dl = dgs.add_parser("link", help="link with a personal access token from Discogs")
    dl.add_argument("token")
    dgs.add_parser("unlink", help="forget the access token")
    dgs.add_parser("status", help="show the linked account")
    dli = dgs.add_parser("list", help="list your collection")
    dli.add_argument("filter", nargs="*")
    dli.add_argument("--refresh", action="store_true", help="download it again from Discogs")
    sub.add_parser("desktop-entry", help="add the window to your Linux applications menu")

    st = sub.add_parser("stylus", aliases=["cartridge"], help="manage cartridges / styli (add, list, use, retire, show, lookup, edit)")
    ss = st.add_subparsers(dest="stylus_cmd")
    sa = ss.add_parser("add", help="fit a new cartridge / stylus; it becomes the active one")
    sa.add_argument("name")
    sa.add_argument("--hours", type=float, required=True, help="rated life in hours from the maker")
    sa.add_argument("--used", type=float, default=0, help="hours already on it, if not new")
    sa.add_argument("--warn", type=float, default=80, help="warn at this %% of rated life (default 80)")
    sa.add_argument("--installed", help="date fitted, if not today")
    sa.add_argument("--replaces", help="cartridge / stylus this one replaces (number or name); it is retired")
    ss.add_parser("list", help="all cartridges / styli, in use and retired")
    su = ss.add_parser("use", help="put a cartridge / stylus on the turntable")
    su.add_argument("id")
    sr = ss.add_parser("retire", help="retire a worn or replaced cartridge / stylus (its plays are kept)")
    sr.add_argument("id")
    sw = ss.add_parser("show", help="hours, specs and monthly use of one cartridge / stylus")
    sw.add_argument("id")
    sl = ss.add_parser("lookup", help="fetch a photo and specs from Wikipedia or a product page")
    sl.add_argument("id")
    sl.add_argument("--url", help="manufacturer or shop page for the cartridge")
    sd2 = ss.add_parser("delete", help="delete a cartridge / stylus added by mistake (only if never used)")
    sd2.add_argument("id")
    se = ss.add_parser("edit")
    se.add_argument("id")
    se.add_argument("--url", help="product page for the cartridge")
    se.add_argument("--name")
    se.add_argument("--hours", type=float)
    se.add_argument("--used", type=float, help="hours already on it before tracking began")
    se.add_argument("--warn", type=float)
    return p


COMMANDS = {
    "add": cmd_records.cmd_add, "play": cmd_plays.cmd_play, "status": cmd_reports.cmd_status, "list": cmd_records.cmd_list,
    "history": cmd_plays.cmd_history, "stats": cmd_reports.cmd_stats, "top": cmd_reports.cmd_top, "undo": cmd_plays.cmd_undo, "log": cmd_plays.cmd_log, "side": cmd_records.cmd_side, "remove": cmd_records.cmd_remove,
    "export": cmd_plays.cmd_export, "stylus": cmd_cartridges.cmd_stylus, "gui": cmd_setup.cmd_gui, "covers": cmd_records.cmd_covers, "info": cmd_records.cmd_info, "discogs": cmd_setup.cmd_discogs, "cartridge": cmd_cartridges.cmd_stylus, "desktop-entry": cmd_setup.cmd_desktop,
}


def main(argv=None):
    """Run one command: open the library, do what was asked and close it again. Returns the exit code."""
    args = build_parser().parse_args(argv)
    if args.db == str(paths.default_db_path()):
        try:
            moved = paths.migrate_old_library()
        except OSError as e:
            moved = None
            err("couldn't move your library from its old folder ({}). It's still there, untouched.".format(e))
        if moved:
            print(moved, file=sys.stderr)
    common.ASSUME_YES = args.yes
    con = db.connect(args.db)
    try:
        cmd = args.cmd
        if cmd is None:  # double-clicked or launched from a menu: open the window
            cmd = "gui" if not sys.stdin.isatty() else "status"
        return COMMANDS[cmd](con, args) or 0
    except KeyboardInterrupt:
        print()
        return 130
    finally:
        con.close()
