"""Commands for your records: add, list, info, side, remove and covers."""

import sys

from styli_app import common, covers, details, discogs, library, musicbrainz, plays, web
from styli_app.common import (VERSION, ask, confirm, err, fmt_dur, fmt_plays, interactive, parse_duration,
                              side_letter, to_dt)


# ---- adding records: MusicBrainz, Discogs or by hand -------------------------------------------------

def cmd_add(con, args):
    """styli add: find an album on MusicBrainz (or Discogs, or enter it by hand), pick your pressing and save it."""
    if args.manual:
        return add_manual(con, args)
    if args.discogs or args.discogs_id:
        return add_from_discogs(con, args)
    try:
        mbid = args.mbid or pick_release(args)
        if not mbid:
            return 1
        if con.execute("SELECT 1 FROM records WHERE mbid = ?", (mbid,)).fetchone():
            r = con.execute("SELECT id, artist, title FROM records WHERE mbid = ?", (mbid,)).fetchone()
            print("Already in your library as #{}: {} - {}".format(r["id"], r["artist"], r["title"]))
            return 0
        rel = musicbrainz.fetch_release(mbid)
    except web.MBError as e:
        err(str(e))
        if interactive() and confirm("Enter the record manually instead?"):
            return add_manual(con, args)
        return 1

    artist = musicbrainz.artist_credit_str(rel.get("artist-credit"))
    title = rel.get("title") or "Untitled"
    year = (rel.get("date") or "")[:4]
    fmt = musicbrainz.media_summary(rel.get("media"))
    sides = musicbrainz.build_sides(rel)
    if not sides:
        err("MusicBrainz has no track listing for this release.")
        if interactive() and confirm("Enter side lengths manually?"):
            args.artist, args.title = args.artist or artist, args.title or title
            return add_manual(con, args)
        return 1
    musicbrainz.review_missing(sides)

    print("\n{} - {}{}  [{}]".format(artist, title, " ({})".format(year) if year else "", fmt))
    for s in sides:
        print("  Side {:<3} {:>8}  ({} tracks)".format(s["label"], fmt_dur(s["seconds"]), s["tracks"]))
    print("  Total    {:>8}".format(fmt_dur(sum(s["seconds"] for s in sides))))
    if not confirm("Save this record?"):
        print("Not saved.")
        return 1
    rgid = (rel.get("release-group") or {}).get("id")
    rid = library.save_record(con, rel.get("id", mbid), artist, title, year, fmt, sides, rgid,
                      tracks=details.tracks_from_sides(sides))
    print("Saved as #{}. Log a play with: styli play {} [side]".format(rid, rid))
    if covers.ensure_record_cover(rid, rel.get("id", mbid), rgid)[0]:
        print("Album cover saved.")
    return 0


def choose(items, describe, heading):
    """Let the user pick from a numbered list. Typing words filters the list."""
    if len(items) == 1:
        print("{}: {}".format(heading, describe(items[0])))
        return items[0]
    if not interactive():
        if common.ASSUME_YES:
            print("{}: {}".format(heading, describe(items[0])))
            return items[0]
        print(heading + ":")
        for i, it in enumerate(items, 1):
            print("{:>3}. {}\n      mbid {}".format(i, describe(it), it["id"]))
        err("several matches; rerun with --mbid <id>, or --yes to take the first.")
        return None
    shown = items
    while True:
        print("\n{} ({}):".format(heading, len(shown)))
        for i, it in enumerate(shown, 1):
            print("{:>3}. {}".format(i, describe(it)))
        ans = ask("Choose 1-{} [1], type words to filter the list, or q to cancel: ".format(len(shown)), "1")
        if ans.lower() == "q":
            return None
        if ans.isdigit() and 1 <= int(ans) <= len(shown):
            return shown[int(ans) - 1]
        words = ans.lower().split()
        matched = [it for it in items if all(w in describe(it).lower() for w in words)]
        if matched:
            shown = matched
        else:
            print("Nothing matches '{}'; showing the full list.".format(ans))
            shown = items


def add_from_discogs(con, args):
    """styli add --discogs: pick a record from your Discogs collection and save it."""
    token, user = discogs.discogs_account(con)
    if not token:
        err("link your Discogs account first: styli discogs link <token>\n"
            "Create a personal access token at {}".format(discogs.DISCOGS_TOKEN_PAGE))
        return 1
    try:
        rid = args.discogs_id
        if not rid:
            print("Reading your Discogs collection...")
            items = discogs.discogs_collection(token, user, progress=lambda n, t: print("  {} of {}".format(n, t)))
            if not args.any_format:
                items = [i for i in items if discogs.discogs_item_info(i)["vinyl"]]
            words = " ".join(args.query or []).lower().split()
            items = [i for i in items if all(w in discogs.describe_discogs_item(i).lower() for w in words)]
            if not items:
                err("nothing in your Discogs collection matches. Try fewer words, or --any-format.")
                return 1
            print(discogs.discogs_credit_line(discogs.discogs_collection_url(user)))
            pick = choose(items, discogs.describe_discogs_item, "Your Discogs collection")
            if not pick:
                return 1
            rid = discogs.discogs_item_info(pick)["id"]
        dup = con.execute("SELECT id, artist, title FROM records WHERE discogs_id = ?", (int(rid),)).fetchone()
        if dup:
            print("Already in your library as #{}: {} - {}".format(dup["id"], dup["artist"], dup["title"]))
            return 0
        print("Fetching the track list...")
        info = discogs.prepare_discogs_release(token, rid)
    except web.MBError as e:
        err(str(e))
        return 1
    for note in info["notes"]:
        print("Note: " + note)
    sides = info["sides"]
    if not sides:
        err("this release has no track list on Discogs or MusicBrainz. Use --manual to enter it.")
        return 1
    musicbrainz.review_missing(sides)
    print("\n{} - {}{}  [{}]".format(info["artist"], info["title"], " ({})".format(info["year"]) if info["year"] else "",
                                     info["format"]))
    print(discogs.discogs_credit_line(discogs.discogs_release_url(info["discogs_id"], info["about"])))
    for s in sides:
        print("  Side {:<3} {:>8}  ({} tracks)".format(s["label"], fmt_dur(s["seconds"]), s["tracks"]))
    if not confirm("Save this record?"):
        print("Not saved.")
        return 1
    rid2 = library.save_record(con, info["mbid"], info["artist"], info["title"], info["year"], info["format"], sides,
                       info["rgid"], info["discogs_id"], info["cover_url"], info["tracks"], info["about"])
    print("Saved as #{}. Log a play with: styli play {} [side]".format(rid2, rid2))
    if covers.ensure_record_cover(rid2, info["mbid"], info["rgid"], cover_url=info["cover_url"])[0]:
        print("Album cover saved.")
    return 0


def pick_release(args):
    """Walk through choosing a pressing at the terminal: barcode, or album then pressing. Returns its MusicBrainz
    id."""
    if args.barcode:
        results, _ = musicbrainz.search_releases(barcode=args.barcode, vinyl=False, limit=25)
        if not results:
            err("no release on MusicBrainz has that barcode. Try --artist and --title.")
            return None
        r = choose(results, musicbrainz.describe_release, "Pressings with that barcode")
        return r["id"] if r else None

    free = " ".join(args.query) if args.query else None
    artist, title = args.artist, args.title
    if not (free or artist or title):
        if not interactive():
            err('give the artist and album, e.g. styli add --artist "fleetwood mac" --title rumours')
            return None
        artist = ask("Artist: ") or None
        title = ask("Album (blank to list all their albums): ") or None
        if not (artist or title):
            return None
    albums = musicbrainz.search_albums(artist, title, free)
    if not albums:
        tip = (" With a single search box, type the artist's full name then the album, "
               "or use --artist and --title.") if free else ""
        err("no album found by that artist with that title. Check the spelling.{}".format(tip))
        return None
    album = choose(albums, musicbrainz.describe_album, "Albums")
    if not album:
        return None

    print("Loading pressings of {}...".format(album.get("title")))
    pressings, total = musicbrainz.list_pressings(album["id"])
    if not args.any_format:
        vinyl = [r for r in pressings if musicbrainz.is_vinyl(r)]
        if vinyl:
            pressings = vinyl
        else:
            print("MusicBrainz lists no vinyl pressings of this album, so all formats are shown.")
    if not pressings:
        err("MusicBrainz lists no pressings for this album. Use --manual to enter it yourself.")
        return None
    if total > musicbrainz.PRESSING_CAP:
        print("Note: this album has {} releases; the first {} are listed.".format(total, musicbrainz.PRESSING_CAP))
    print("Pick the pressing you own. The label, catalogue number and barcode on your copy help.")
    r = choose(pressings, lambda p: musicbrainz.describe_pressing(p, album.get("title")), "Pressings")
    return r["id"] if r else None


def add_manual(con, args):
    """styli add --manual: save a record with side lengths you type in."""
    artist = args.artist or (ask("Artist: ") if interactive() else "")
    title = args.title or (ask("Album title: ") if interactive() else "")
    if not artist or not title:
        err("manual entry needs --artist and --title.")
        return 1
    sides = []
    for spec in args.side or []:
        if "=" not in spec:
            err("--side expects LABEL=mm:ss, e.g. --side A=21:34")
            return 1
        lab, dur = spec.split("=", 1)
        sides.append({"label": lab.strip().upper(), "seconds": parse_duration(dur), "tracks": 0})
    if not sides and interactive():
        i = 0
        while True:
            ans = ask("Length of side {} (mm:ss, blank to finish): ".format(side_letter(i)))
            if not ans:
                break
            try:
                sides.append({"label": side_letter(i), "seconds": parse_duration(ans), "tracks": 0})
                i += 1
            except ValueError as e:
                print("  " + str(e))
    if not sides:
        err("at least one side length is needed.")
        return 1
    rid = library.save_record(con, None, artist, title, args.year or "", "Vinyl", sides)
    print("Saved as #{}: {} - {} ({})".format(
        rid, artist, title, ", ".join("{} {}".format(s["label"], fmt_dur(s["seconds"])) for s in sides)))
    return 0


def cmd_list(con, args):
    """styli list: the records in your library with their sides and play counts."""
    rows = con.execute("SELECT * FROM records ORDER BY artist, title").fetchall()
    if args.filter:
        words = " ".join(args.filter).lower().split()
        rows = [r for r in rows if all(w in "{} {}".format(r["artist"], r["title"]).lower() for w in words)]
    if not rows:
        print("No records yet. Add one with: styli add \"artist album\"")
        return 0
    counts = plays.play_counts(con)
    for r in rows:
        sides = library.record_sides(con, r["id"])
        c = counts.get(r["id"], {"album": 0, "sides": 0, "last": None})
        side_txt = "  ".join("{} {}{}".format(s["label"], fmt_dur(s["seconds"]), "*" if s["missing"] else "")
                             for s in sides)
        last = ", last {}".format(to_dt(c["last"]).date()) if c["last"] else ""
        print("#{:<4} {} - {}{}".format(r["id"], r["artist"], r["title"], " ({})".format(r["year"]) if r["year"] else ""))
        print("      {}   [{}{}]".format(side_txt, fmt_plays(c["album"], c["sides"]), last))
    if any(s["missing"] for r in rows for s in library.record_sides(con, r["id"])):
        print("\n* some track lengths were missing on MusicBrainz; fix with `styli side`.")
    return 0

# ---- correcting the library, and exporting -----------------------------------------------------------


def cmd_side(con, args):
    """styli side: correct or remove one side's running time."""
    rec = library.resolve_record(con, args.record)
    if not rec:
        return 1
    label = args.label.upper()
    if args.delete:
        con.execute("DELETE FROM sides WHERE record_id = ? AND label = ?", (rec["id"], label))
        con.commit()
        print("Removed side {} from {}.".format(label, rec["title"]))
        return 0
    if not args.length:
        err("give a length, e.g. styli side {} {} 21:34".format(rec["id"], label))
        return 1
    try:
        secs = parse_duration(args.length)
    except ValueError as e:
        err(str(e))
        return 1
    con.execute(
        "INSERT INTO sides (record_id, label, seconds, missing) VALUES (?, ?, ?, 0) "
        "ON CONFLICT(record_id, label) DO UPDATE SET seconds = excluded.seconds, missing = 0",
        (rec["id"], label, secs))
    con.commit()
    print("Side {} of {} set to {}. (Past plays keep the length they were logged with.)".format(
        label, rec["title"], fmt_dur(secs)))
    return 0


def cmd_remove(con, args):
    """styli remove: take a record out of the library (its plays are kept)."""
    rec = library.resolve_record(con, args.record)
    if not rec:
        return 1
    if not confirm("Remove {} - {} from the library? Logged plays are kept.".format(rec["artist"], rec["title"]), False):
        return 1
    con.execute("DELETE FROM records WHERE id = ?", (rec["id"],))
    con.commit()
    covers.remove_record_cover(rec["id"])
    print("Removed.")
    return 0

# ---- covers, Discogs, album details and the menu entry -----------------------------------------------


def cmd_covers(con, args):
    """styli covers: save covers for records that don't have one, matching hand-entered albums by name."""
    rows = [r for r in con.execute("SELECT * FROM records ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE")
            if not covers.library_cover_path(r["id"]).exists()]
    print("Styli {}: looking for covers for {} record{} (MusicBrainz allows about one search a second).".format(
        VERSION, len(rows), "" if len(rows) == 1 else "s"))
    saved, missing = 0, []
    for n, r in enumerate(rows, 1):
        label = "{} - {}".format(r["artist"], r["title"])
        try:
            path, rgid = covers.ensure_record_cover(r["id"], r["mbid"], r["rgid"], cover_url=r["cover_url"],
                                             artist=r["artist"], title=r["title"])
        except web.MBError as e:
            print("  [{}/{}] {}: {}".format(n, len(rows), label, e))
            if "could not reach" in str(e):
                err("MusicBrainz can't be reached, so stopping here. Check your internet connection and try again.")
                break
            missing.append(label)
            continue
        if rgid:
            con.execute("UPDATE records SET rgid = ? WHERE id = ?", (rgid, r["id"]))
            con.commit()
        if path:
            saved += 1
            print("  [{}/{}] {}: saved".format(n, len(rows), label))
        else:
            missing.append(label)
            print("  [{}/{}] {}: {}".format(n, len(rows), label,
                                           "no matching album on MusicBrainz" if not (r["mbid"] or r["rgid"] or rgid or r["cover_url"])
                                           else "no cover on the Cover Art Archive"))
    print("{} cover{} saved, {} without one. Covers are kept in {}".format(
        saved, "" if saved == 1 else "s", len(missing), covers.library_cover_path(0).parent))
    if missing:
        print("You can choose your own image for any of these with Change cover in the window.")
    return 0


def cmd_info(con, args):
    """styli info: a record's summary and track listing."""
    rec = library.resolve_record(con, args.record)
    if not rec:
        return 1
    token, _ = discogs.discogs_account(con)
    need_tracks, need_about = details.details_needed(con, rec, token)
    if args.refresh:
        need_tracks, need_about = bool(rec["mbid"] or (rec["discogs_id"] and token)), True
    if need_tracks or need_about:
        print("Getting album details...", file=sys.stderr)
        result = details.fetch_details(dict(rec), token, need_tracks, need_about)
        if result["error"]:
            err(result["error"])
        details.store_details(con, rec["id"], result)
        rec = con.execute("SELECT * FROM records WHERE id = ?", (rec["id"],)).fetchone()
    about = details.get_about(rec) or {}
    print("{} - {}".format(rec["artist"], rec["title"]))
    facts = [x for x in (", ".join(about.get("genres") or []), "Released " + about["released"]
                         if about.get("released") else "", about.get("label")) if x]
    if facts:
        print("  " + "  |  ".join(facts))
    c = plays.play_counts(con, rec["id"]).get(rec["id"])
    print("  " + (fmt_plays(c["album"], c["sides"]) if c else "Not played yet"))
    if about.get("text"):
        print()
        for line in about["text"].split("\n"):
            print("  " + line)
        if about.get("url"):
            print("  ({})".format(about["url"]))
    if about.get("notes"):
        print("\n  Pressing notes: " + about["notes"])
    tracks = details.get_tracks(con, rec["id"])
    lengths = {s["label"]: s["seconds"] for s in library.record_sides(con, rec["id"])}
    side = None
    for t in tracks:
        if t["side"] != side:
            side = t["side"]
            print("\n  Side {}  ({})".format(side, fmt_dur(lengths.get(side, 0))))
        print("    {:<5} {:<50} {}".format(t["position"], t["title"][:50], fmt_dur(t["seconds"]) if t["seconds"] else ""))
    if not tracks:
        print("\n  No track listing saved for this record.")
    if rec["discogs_id"]:
        print("\n  " + discogs.discogs_credit_line(discogs.discogs_release_url(rec["discogs_id"], about)))
    return 0
