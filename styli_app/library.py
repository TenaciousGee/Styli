"""Records in your library: saving them with their sides, correcting sides, and finding a record
from a number or a few words of its artist and title."""

import json

from styli_app import covers, details
from styli_app.common import ask, err, interactive, now_local


def record_sides(con, record_id):
    """A record's sides (label and running time), in order."""
    return con.execute(
        "SELECT * FROM sides WHERE record_id = ? ORDER BY rowid", (record_id,)
    ).fetchall()


def save_record(con, mbid, artist, title, year, fmt, sides, rgid=None, discogs_id=None, cover_url=None,
                tracks=None, about=None):
    """Add a record to the library with its sides, and its track listing and summary when known. Returns its id."""
    cur = con.execute(
        "INSERT INTO records (mbid, artist, title, year, format, added_at, rgid, discogs_id, cover_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mbid, artist, title, year, fmt, now_local().isoformat(), rgid, discogs_id, cover_url),
    )
    rid = cur.lastrowid
    for s in sides:
        con.execute(
            "INSERT INTO sides (record_id, label, seconds, tracks, missing) VALUES (?, ?, ?, ?, ?)",
            (rid, s["label"], s["seconds"], s.get("tracks", 0), s.get("missing", 0)),
        )
    if about:
        con.execute("UPDATE records SET about = ? WHERE id = ?", (json.dumps(about), rid))
    con.commit()
    if tracks:
        details.save_tracks(con, rid, tracks)
    covers.remove_record_cover(rid)  # record numbers can be reused, so never inherit an old cover
    return rid


def resolve_record(con, ref):
    """Find a library record from a number or words in its artist and title, asking when several match."""
    ref = ref.strip()
    if ref.isdigit():
        row = con.execute("SELECT * FROM records WHERE id = ?", (int(ref),)).fetchone()
        if row:
            return row
    words = ref.lower().split()
    rows = con.execute("SELECT * FROM records ORDER BY artist, title").fetchall()
    matches = [r for r in rows if all(w in "{} {}".format(r["artist"], r["title"]).lower() for w in words)]
    if not matches:
        err("no record in your library matches '{}'. See `styli list`, or add it with `styli add`.".format(ref))
        return None
    if len(matches) == 1:
        return matches[0]
    print("Several records match '{}':".format(ref))
    for r in matches:
        print("  #{:<4} {} - {}".format(r["id"], r["artist"], r["title"]))
    if not interactive():
        err("be more specific or use the record number.")
        return None
    ans = ask("Record number: ")
    for r in matches:
        if ans == str(r["id"]):
            return r
    err("no record chosen.")
    return None


def replace_sides(con, record_id, sides):
    """Replace all of a record's sides with a new set (after editing them)."""
    con.execute("DELETE FROM sides WHERE record_id = ?", (record_id,))
    for s in sides:
        con.execute(
            "INSERT INTO sides (record_id, label, seconds, tracks, missing) VALUES (?, ?, ?, ?, ?)",
            (record_id, s["label"], s["seconds"], s.get("tracks", 0), s.get("missing", 0)))
    con.commit()
