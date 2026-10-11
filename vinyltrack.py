#!/usr/bin/env python3
"""
vinyltrack: log vinyl plays by side and track cartridge / stylus hours.

Record and side running times are fetched automatically from MusicBrainz
(https://musicbrainz.org), which lists vinyl track positions such as A1, A2,
B1 so each side can be timed separately.

Run `vt gui` for the desktop window (PyQt6, in vinyltrack_gui.py), or use the
commands below. Album art comes from the Cover Art Archive.

The commands need only the Python standard library (3.8+); the window also
needs PyQt6. Runs on Linux, macOS and Windows.
Data is kept in a single SQLite file:
  Linux    $XDG_DATA_HOME/vinyltrack/vinyltrack.db (default ~/.local/share/...)
  macOS    ~/Library/Application Support/vinyltrack/vinyltrack.db
  Windows  %APPDATA%\\vinyltrack\\vinyltrack.db
Override with the VINYLTRACK_DB environment variable or --db.

Licence: MIT. Share freely.
"""

import argparse
import csv
import datetime as dt
import hashlib
import html
import html.parser
import json
import os
import re
import shutil
import sqlite3
import unicodedata
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "1.4.0"
MB_ROOT = "https://musicbrainz.org/ws/2"
# MusicBrainz asks every client to send a meaningful User-Agent with contact
# details. Set VINYLTRACK_CONTACT to your email or a project URL.
USER_AGENT = "vinyltrack/{} ( {} )".format(
    VERSION, os.environ.get("VINYLTRACK_CONTACT", "personal stylus-hours tracker")
)
RECENT_WINDOW_DAYS = 56  # window used to estimate listening rate in `status`

ASSUME_YES = False
DATA_DIR = None  # folder holding the database; set by connect()


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def default_db_path():
    env = os.environ.get("VINYLTRACK_DB")
    if env:
        return Path(env).expanduser()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "vinyltrack" / "vinyltrack.db"


def now_local():
    return dt.datetime.now().astimezone().replace(microsecond=0)


def to_dt(iso):
    d = dt.datetime.fromisoformat(iso)
    return d if d.tzinfo else d.astimezone()


def fmt_dur(sec):
    sec = int(round(sec or 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return "{}:{:02d}:{:02d}".format(h, m, s) if h else "{}:{:02d}".format(m, s)


def fmt_hours(sec):
    return "{:.1f} h".format((sec or 0) / 3600)


def parse_duration(text):
    """Accepts '21:34', '1:02:10' or plain minutes such as '21.5'."""
    text = text.strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return int(round(float(text) * 60))
    parts = text.split(":")
    if not all(p.isdigit() for p in parts) or len(parts) not in (2, 3):
        raise ValueError("Use mm:ss, h:mm:ss or minutes, e.g. 21:34")
    nums = [int(p) for p in parts]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def parse_when(text):
    """'2026-10-11 20:30', '2026-10-11', 'yesterday 21:00', 'today 9:15'."""
    if not text:
        return now_local()
    t = text.strip().lower()
    for word, offset in (("yesterday", 1), ("today", 0)):
        if t.startswith(word):
            day = dt.date.today() - dt.timedelta(days=offset)
            rest = t[len(word):].strip()
            clock = dt.time.fromisoformat(rest.zfill(5)) if rest else dt.time(12, 0)
            return dt.datetime.combine(day, clock).astimezone().replace(microsecond=0)
    try:
        d = dt.datetime.fromisoformat(text.strip())
    except ValueError:
        raise ValueError("Could not read date/time '{}'. Try 2026-10-11 20:30".format(text))
    return (d if d.tzinfo else d.astimezone()).replace(microsecond=0)


def interactive():
    return sys.stdin.isatty() and not ASSUME_YES


def ask(prompt, default=""):
    try:
        ans = input(prompt).strip()
    except EOFError:
        ans = ""
    return ans or default


def confirm(prompt, default=True):
    if not interactive():
        return True if ASSUME_YES else default
    hint = "[Y/n]" if default else "[y/N]"
    ans = ask("{} {} ".format(prompt, hint)).lower()
    if not ans:
        return default
    return ans.startswith("y")


def err(msg):
    print("Error: " + msg, file=sys.stderr)


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS styli (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    rated_hours     REAL NOT NULL,
    initial_seconds INTEGER NOT NULL DEFAULT 0,
    warn_pct        REAL NOT NULL DEFAULT 80,
    installed_at    TEXT NOT NULL,
    retired_at      TEXT,
    active          INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS records (
    id        INTEGER PRIMARY KEY,
    mbid      TEXT UNIQUE,
    artist    TEXT NOT NULL,
    title     TEXT NOT NULL,
    year      TEXT,
    format    TEXT,
    added_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sides (
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    label     TEXT NOT NULL,
    seconds   INTEGER NOT NULL,
    tracks    INTEGER NOT NULL DEFAULT 0,
    missing   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (record_id, label)
);
CREATE TABLE IF NOT EXISTS plays (
    id           INTEGER PRIMARY KEY,
    record_id    INTEGER REFERENCES records(id) ON DELETE SET NULL,
    record_label TEXT NOT NULL,
    side         TEXT NOT NULL,
    seconds      INTEGER NOT NULL,
    played_at    TEXT NOT NULL,
    stylus_id    INTEGER REFERENCES styli(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS plays_stylus ON plays(stylus_id);
CREATE INDEX IF NOT EXISTS plays_time ON plays(played_at);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path):
    global DATA_DIR
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    DATA_DIR = path.parent
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    cols = {r["name"] for r in con.execute("PRAGMA table_info(records)")}
    if "rgid" not in cols:  # album ID, added in v1.1 for album art
        con.execute("ALTER TABLE records ADD COLUMN rgid TEXT")
        con.commit()
    for col, kind in (("discogs_id", "INTEGER"), ("cover_url", "TEXT")):  # Discogs, added in v1.4
        if col not in cols:
            con.execute("ALTER TABLE records ADD COLUMN {} {}".format(col, kind))
            con.commit()
    scols = {r["name"] for r in con.execute("PRAGMA table_info(styli)")}
    for col in ("specs", "url"):  # cartridge details, added in v1.3
        if col not in scols:
            con.execute("ALTER TABLE styli ADD COLUMN {} TEXT".format(col))
            con.commit()
    return con


def get_setting(con, key, default=None):
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(con, key, value):
    con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    con.commit()


def record_sides(con, record_id):
    return con.execute(
        "SELECT * FROM sides WHERE record_id = ? ORDER BY rowid", (record_id,)
    ).fetchall()


def save_record(con, mbid, artist, title, year, fmt, sides, rgid=None, discogs_id=None, cover_url=None):
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
    con.commit()
    remove_record_cover(rid)  # record numbers can be reused, so never inherit an old cover
    return rid


# --------------------------------------------------------------------------
# MusicBrainz
# --------------------------------------------------------------------------

class MBError(Exception):
    pass


_last_call = [0.0]
_mb_lock = threading.Lock()  # keeps background lookups to one request per second
_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')
LETTER_RE = re.compile(r"^\s*([A-Za-z]+)")


def mb_get(path, params):
    with _mb_lock:
        return _mb_get(path, params)


def _mb_get(path, params):
    # MusicBrainz allows roughly one request per second per client.
    wait = 1.1 - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    params = dict(params, fmt="json")
    url = "{}/{}?{}".format(MB_ROOT, path, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                _last_call[0] = time.monotonic()
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            _last_call[0] = time.monotonic()
            if e.code in (429, 503) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            if e.code == 404:
                raise MBError("MusicBrainz has no release with that ID")
            raise MBError("MusicBrainz returned HTTP {}".format(e.code))
        except urllib.error.URLError as e:
            raise MBError("could not reach MusicBrainz ({})".format(e.reason))
    raise MBError("MusicBrainz is busy, try again shortly")


def lucene_phrase(s):
    return '"{}"'.format(s.replace("\\", "\\\\").replace('"', '\\"'))


def search_releases(free=None, artist=None, title=None, barcode=None, vinyl=True, limit=25, offset=0):
    """Returns (releases, total_matches). MusicBrainz allows up to 100 per page."""
    parts = []
    if barcode:
        digits = re.sub(r"\D", "", barcode)
        parts.append("barcode:{}".format(digits))
    if artist:
        parts.append("artist:{}".format(lucene_phrase(artist)))
    if title:
        parts.append("release:{}".format(lucene_phrase(title)))
    if free:
        parts.append("({})".format(_LUCENE_SPECIAL.sub(r"\\\1", free)))
    if vinyl and not barcode:
        parts.append("format:vinyl")
    limit = max(1, min(100, int(limit)))
    data = mb_get("release/", {"query": " AND ".join(parts), "limit": limit, "offset": offset})
    return data.get("releases", []), data.get("count", 0)


def fetch_release(mbid):
    return mb_get("release/{}".format(mbid), {"inc": "recordings+artist-credits+labels+release-groups"})


SEARCH_STOPWORDS = {"the", "a", "an", "and"}
TYPE_RANK = {"Album": 0, "EP": 1, "Single": 2}
PRESSING_CAP = 500


def norm_tokens(text):
    """Lower-case words with accents and punctuation removed; '&' counts as 'and'."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return re.findall(r"[a-z0-9]+", text.lower().replace("&", " and "))


def key_tokens(text):
    return {w for w in norm_tokens(text) if w not in SEARCH_STOPWORDS}


def album_artist(g):
    return artist_credit_str(g.get("artist-credit"))


def search_albums(artist=None, title=None, free=None):
    """Find albums (MusicBrainz release groups) by the named artist only, best match first.

    Unlabelled words in a MusicBrainz search only match titles, so the artist and
    album are searched in their own fields, then results credited to anyone other
    than the artist typed (tributes, covers, various-artists compilations) are dropped.
    """
    a_words, t_words = norm_tokens(artist), norm_tokens(title)
    if a_words or t_words:
        parts = []
        if a_words:
            parts.append("artist:({})".format(" AND ".join(a_words)))
        if t_words:
            parts.append("releasegroup:({})".format(" AND ".join(t_words)))
        else:  # artist only: list their albums
            parts.append("primarytype:album")
        pages = 1 if t_words else 3
    else:
        words = " ".join(norm_tokens(free))
        if not words:
            return []
        parts = ["artist:({})".format(words), "releasegroup:({})".format(words)]
        pages = 1
    query = " AND ".join(parts)
    groups = []
    for page in range(pages):
        data = mb_get("release-group/", {"query": query, "limit": 100, "offset": page * 100})
        batch = data.get("release-groups", [])
        groups.extend(batch)
        if not batch or len(groups) >= data.get("count", 0):
            break
    return rank_albums(groups, artist, title, free)


def rank_albums(groups, artist=None, title=None, free=None):
    seen, candidates = set(), []
    structured = bool(norm_tokens(artist) or norm_tokens(title))
    qa, qt, q = key_tokens(artist), key_tokens(title), key_tokens(free)
    for g in groups:
        if g.get("id") in seen:
            continue
        seen.add(g.get("id"))
        a, t = key_tokens(album_artist(g)), key_tokens(g.get("title"))
        if structured:
            if (qt and not qt <= t) or (qa and not qa <= a):
                continue
            exact_artist = (not qa) or a == qa
            want = qt
        else:
            # every word of the credited artist must have been typed, and the
            # remaining words must all be in the album title
            if not a or not a <= q or not (q - a) <= t or not (t & q or not (q - a)):
                continue
            exact_artist = True
            want = q - a
        candidates.append((g, a, exact_artist, want))
    if structured:
        exact = [c for c in candidates if c[2]]
        candidates = exact or candidates
    elif candidates:
        # prefer the artist that accounts for the most typed words ("Fleetwood Mac" over "Mac")
        best = max(len(c[1]) for c in candidates)
        candidates = [c for c in candidates if len(c[1]) == best]

    def sort_key(c):
        g, _, _, want = c
        exact_title = 0 if want and key_tokens(g.get("title")) == want else 1
        return (exact_title, 1 if g.get("secondary-types") else 0,
                TYPE_RANK.get(g.get("primary-type"), 3), g.get("first-release-date") or "9999")

    return [c[0] for c in sorted(candidates, key=sort_key)]


def describe_album(g):
    types = [g.get("primary-type") or "Other"] + list(g.get("secondary-types") or [])
    year = (g.get("first-release-date") or "")[:4]
    text = "{} - {}{}  [{}]".format(album_artist(g), g.get("title", "?"),
                                    " ({})".format(year) if year else "", " + ".join(types))
    if g.get("disambiguation"):
        text += "  " + g["disambiguation"]
    return text


def list_pressings_page(rgid, offset=0):
    """One page (up to 100) of an album's pressings. Returns (releases, total)."""
    data = mb_get("release", {"release-group": rgid, "inc": "media+labels", "limit": 100, "offset": offset})
    return data.get("releases", []), data.get("release-count", 0)


def list_pressings(rgid, cap=PRESSING_CAP):
    """All releases (pressings) of one album, up to cap. Returns (releases, total)."""
    out, total = [], 0
    while True:
        data = mb_get("release", {"release-group": rgid, "inc": "media+labels",
                                  "limit": 100, "offset": len(out)})
        batch = data.get("releases", [])
        out.extend(batch)
        total = data.get("release-count", len(out))
        if not batch or len(out) >= total or len(out) >= cap:
            break
    return sorted(out, key=pressing_sort_key), total


def is_vinyl(r):
    return any("vinyl" in (m.get("format") or "").lower() for m in r.get("media") or [])


def pressing_sort_key(r):
    official = (r.get("status") or "Official") == "Official"
    return (0 if official else 1, r.get("date") or "9999", r.get("country") or "")


def pressing_notes(r, album_title=None):
    notes = []
    if r.get("status") and r["status"] != "Official":
        notes.append(r["status"])
    if album_title and r.get("title") and r["title"] != album_title:
        notes.append(r["title"])
    if r.get("disambiguation"):
        notes.append(r["disambiguation"])
    return "; ".join(notes)


def describe_pressing(r, album_title=None):
    bits = [r.get("date") or "no date", r.get("country") or "", media_summary(r.get("media")),
            release_label(r), "barcode " + r["barcode"] if r.get("barcode") else "",
            pressing_notes(r, album_title)]
    return "  ".join(b for b in bits if b)


def artist_credit_str(credits):
    out = ""
    for c in credits or []:
        name = c.get("name") or (c.get("artist") or {}).get("name", "")
        out += name + c.get("joinphrase", "")
    return out or "Unknown artist"


def media_summary(media):
    counts = {}
    for m in media or []:
        f = m.get("format") or "Unknown format"
        counts[f] = counts.get(f, 0) + 1
    return " + ".join(("{}x{}".format(n, f) if n > 1 else f) for f, n in counts.items())


def describe_release(r):
    bits = [artist_credit_str(r.get("artist-credit")), "-", r.get("title", "?")]
    meta = [x for x in ((r.get("date") or "")[:4], r.get("country")) if x]
    if meta:
        bits.append("(" + ", ".join(meta) + ")")
    line = " ".join(bits)
    fmt = media_summary(r.get("media"))
    tracks = r.get("track-count")
    extra = []
    if fmt:
        extra.append(fmt)
    if tracks:
        extra.append("{} tracks".format(tracks))
    labels = [
        "{} {}".format((li.get("label") or {}).get("name", ""), li.get("catalog-number") or "").strip()
        for li in r.get("label-info") or []
    ]
    labels = [l for l in labels if l]
    if labels:
        extra.append(labels[0])
    if r.get("disambiguation"):
        extra.append(r["disambiguation"])
    return line + ("\n      " + " | ".join(extra) if extra else "")


def track_seconds(t):
    ms = t.get("length") or (t.get("recording") or {}).get("length")
    return int(round(ms / 1000)) if ms else None


def side_letter(i):
    return chr(65 + i) if i < 26 else "S{}".format(i + 1)


def best_split(lengths):
    """Index where side two should start so both halves are closest in time."""
    total = sum(l or 0 for l in lengths)
    best, best_diff, run = 1, float("inf"), 0
    for i in range(1, len(lengths)):
        run += lengths[i - 1] or 0
        diff = abs(total / 2 - run)
        if diff < best_diff:
            best, best_diff = i, diff
    return best


def ask_split(medium, mi, n_media, tracks, lengths, suggested):
    label = medium.get("format") or "disc"
    which = " {} of {}".format(mi, n_media) if n_media > 1 else ""
    print("\nMusicBrainz lists {}{} without side letters (A1, B1...). Tracks:".format(label, which))
    for i, (t, l) in enumerate(zip(tracks, lengths), 1):
        print("  {:>2}. {:<45} {}".format(i, (t.get("title") or "")[:45], fmt_dur(l) if l else "?:??"))
    while True:
        ans = ask("Second side starts at track [{}] (0 = keep as one side): ".format(suggested + 1))
        if not ans:
            return suggested
        if ans.isdigit():
            n = int(ans)
            if n == 0:
                return None
            if 2 <= n <= len(tracks):
                return n - 1
        print("  Enter a track number between 2 and {}, or 0.".format(len(tracks)))


def build_sides(release, allow_prompt=True):
    media = [m for m in release.get("media") or [] if m.get("tracks")]
    sides, used, next_idx = [], set(), 0
    for mi, m in enumerate(media, 1):
        tracks = m["tracks"]
        nums = [str(t.get("number") or "") for t in tracks]
        groups = {}
        if all(LETTER_RE.match(n) for n in nums):
            for t, n in zip(tracks, nums):
                groups.setdefault(LETTER_RE.match(n).group(1).upper(), []).append(t)
        else:
            lengths = [track_seconds(t) for t in tracks]
            split = best_split(lengths) if len(tracks) > 1 else None
            if split is not None and allow_prompt and interactive():
                split = ask_split(m, mi, len(media), tracks, lengths, split)
            if split:
                groups[side_letter(next_idx)] = tracks[:split]
                groups[side_letter(next_idx + 1)] = tracks[split:]
            else:
                groups[side_letter(next_idx)] = tracks
        for label, ts in groups.items():
            if label in used:
                label = "{}{}".format(label, mi)
            used.add(label)
            secs = [track_seconds(t) for t in ts]
            sides.append({
                "label": label,
                "seconds": sum(s for s in secs if s),
                "tracks": len(ts),
                "missing": sum(1 for s in secs if not s),
            })
            if len(label) == 1:
                next_idx = max(next_idx, ord(label) - 64)
    return sides


def review_missing(sides):
    for s in sides:
        if not s["missing"]:
            continue
        print("Warning: side {} has {} of {} tracks with no length on MusicBrainz (counted {}).".format(
            s["label"], s["missing"], s["tracks"], fmt_dur(s["seconds"])))
        if interactive():
            while True:
                ans = ask("  Enter real length of side {} (mm:ss), or press Enter to keep: ".format(s["label"]))
                if not ans:
                    break
                try:
                    s["seconds"] = parse_duration(ans)
                    s["missing"] = 0
                    break
                except ValueError as e:
                    print("  " + str(e))
        else:
            print("  Fix later with: vt side <record> {} <mm:ss>".format(s["label"]))
    return sides


# --------------------------------------------------------------------------
# Album art (Cover Art Archive, https://coverartarchive.org)
# --------------------------------------------------------------------------

CAA_ROOT = "https://coverartarchive.org"
MISS_RETRY_DAYS = 7


def cache_dir():
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    return base / "vinyltrack" / "covers"


def library_cover_path(record_id):
    """Each record's own cover, kept with the library so it works offline for good.

    It is saved the first time the cover is fetched, or replaced when you choose
    your own image.
    """
    return (DATA_DIR or default_db_path().parent) / "covers" / "record-{}.img".format(record_id)


custom_cover_path = library_cover_path  # older name


def remove_record_cover(record_id):
    try:
        library_cover_path(record_id).unlink()
    except OSError:
        pass


def ensure_record_cover(record_id, mbid, rgid, size=500, cover_url=None):
    """Make sure a record has its own saved cover. Returns (path or None, album id looked up or None).

    Uses the pressing's cover if it has one, otherwise the album's main cover.
    """
    dest = library_cover_path(record_id)
    if dest.exists() and dest.stat().st_size:
        return dest, None
    found, src = None, None
    if cover_url:  # the exact pressing's cover from Discogs
        src = fetch_image_cached(cover_url)
    if not src and mbid:
        src = fetch_cover("release", mbid, size)
        if not src and not rgid:
            rgid = found = lookup_rgid(mbid)
    if not src and rgid:
        src = fetch_cover("release-group", rgid, size)
    if not src:
        return None, found
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".{}.tmp".format(threading.get_ident()))
    shutil.copyfile(str(src), str(tmp))
    os.replace(str(tmp), str(dest))
    return dest, found


def search_cache_size():
    d = cache_dir()
    return sum(f.stat().st_size for f in d.glob("*") if f.is_file()) if d.exists() else 0


def clear_search_cache():
    d = cache_dir()
    for f in d.glob("*") if d.exists() else []:
        try:
            f.unlink()
        except OSError:
            pass


def fetch_cover(kind, mbid, size=500):
    """Front cover for a 'release' or 'release-group'. Returns a cached file path or None."""
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / "{}-{}-{}.img".format(kind, mbid, size)
    miss = d / "{}-{}-{}.none".format(kind, mbid, size)
    if path.exists() and path.stat().st_size:
        return path
    if miss.exists() and time.time() - miss.stat().st_mtime < MISS_RETRY_DAYS * 86400:
        return None
    url = "{}/{}/{}/front-{}".format(CAA_ROOT, kind, mbid, size)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):  # no cover chosen for this release yet
            miss.touch()
        return None
    except (urllib.error.URLError, OSError, ValueError):
        return None  # offline: try again next time
    if not data:
        return None
    tmp = path.with_name(path.name + ".{}.tmp".format(threading.get_ident()))
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))
    return path


def lookup_rgid(mbid):
    """Album (release group) ID for a pressing, used to fall back to the album's cover."""
    try:
        rel = mb_get("release/{}".format(mbid), {"inc": "release-groups"})
    except MBError:
        return None
    return (rel.get("release-group") or {}).get("id")


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------

def resolve_record(con, ref):
    ref = ref.strip()
    if ref.isdigit():
        row = con.execute("SELECT * FROM records WHERE id = ?", (int(ref),)).fetchone()
        if row:
            return row
    words = ref.lower().split()
    rows = con.execute("SELECT * FROM records ORDER BY artist, title").fetchall()
    matches = [r for r in rows if all(w in "{} {}".format(r["artist"], r["title"]).lower() for w in words)]
    if not matches:
        err("no record in your library matches '{}'. See `vt list`, or add it with `vt add`.".format(ref))
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


def active_stylus(con):
    """The cartridge / stylus on the turntable now: plays are logged against it unless another is chosen."""
    sid = get_setting(con, "current_stylus")
    if sid and sid.isdigit():
        row = con.execute("SELECT * FROM styli WHERE id = ? AND active = 1", (int(sid),)).fetchone()
        if row:
            return row
    return con.execute("SELECT * FROM styli WHERE active = 1 ORDER BY id DESC").fetchone()


def stylus_used_seconds(con, s):
    row = con.execute("SELECT COALESCE(SUM(seconds), 0) FROM plays WHERE stylus_id = ?", (s["id"],)).fetchone()
    return s["initial_seconds"] + row[0]


def stylus_bar(pct, width=30):
    filled = min(width, int(round(width * pct / 100)))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def stylus_alert(con, s):
    used = stylus_used_seconds(con, s)
    rated = s["rated_hours"] * 3600
    pct = 100 * used / rated if rated else 0
    line = "Cartridge / stylus '{}': {} of {:g} h ({:.1f}%)".format(s["name"], fmt_hours(used), s["rated_hours"], pct)
    if pct >= 100:
        line += "\n  !! Rated life reached. Inspect or replace the cartridge / stylus."
    elif pct >= s["warn_pct"]:
        line += "\n  ! Past {:g}% of rated life, about {} left.".format(s["warn_pct"], fmt_hours(rated - used))
    return line


# --------------------------------------------------------------------------
# Core actions (shared by the command line and the window)
# --------------------------------------------------------------------------

def release_label(r):
    for li in r.get("label-info") or []:
        text = "{} {}".format((li.get("label") or {}).get("name", ""), li.get("catalog-number") or "").strip()
        if text:
            return text
    return ""


def release_has_side_letters(release):
    for m in release.get("media") or []:
        for t in m.get("tracks") or []:
            if not LETTER_RE.match(str(t.get("number") or "")):
                return False
    return True


def stylus_summary(con, s):
    used = stylus_used_seconds(con, s)
    rated = s["rated_hours"] * 3600
    pct = 100 * used / rated if rated else 0
    installed = to_dt(s["installed_at"])
    now = now_local()
    start = max(installed, now - dt.timedelta(days=RECENT_WINDOW_DAYS))
    recent = con.execute(
        "SELECT COALESCE(SUM(seconds), 0) FROM plays WHERE stylus_id = ? AND played_at >= ?",
        (s["id"], start.isoformat())).fetchone()[0]
    days = max(1.0, (now - start).total_seconds() / 86400)
    per_day = recent / days
    eta = now + dt.timedelta(days=(rated - used) / per_day) if per_day > 0 and used < rated else None
    level = "over" if pct >= 100 else ("warn" if pct >= s["warn_pct"] else "ok")
    sides = con.execute("SELECT COUNT(*) FROM plays WHERE stylus_id = ?", (s["id"],)).fetchone()[0]
    return {"used": used, "rated": rated, "pct": pct, "remaining": max(0, rated - used),
            "per_week_h": per_day * 7 / 3600, "eta": eta, "level": level,
            "installed": installed, "sides_played": sides}


def play_entries(sides, chosen, custom=None):
    if custom is not None:
        return [("+".join(chosen) + " (partial)", custom)]
    return [(c, sides[c]["seconds"]) for c in chosen]


def log_play(con, rec, entries, when, times=1, stylus=None):
    stylus = stylus or active_stylus(con)
    label = "{} - {}".format(rec["artist"], rec["title"])
    times = max(1, int(times))
    for _ in range(times):
        for side, secs in entries:
            con.execute(
                "INSERT INTO plays (record_id, record_label, side, seconds, played_at, stylus_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rec["id"], label, side, secs, when.isoformat(), stylus["id"] if stylus else None))
    con.commit()
    return stylus, sum(s for _, s in entries) * times


def replace_sides(con, record_id, sides):
    con.execute("DELETE FROM sides WHERE record_id = ?", (record_id,))
    for s in sides:
        con.execute(
            "INSERT INTO sides (record_id, label, seconds, tracks, missing) VALUES (?, ?, ?, ?, ?)",
            (record_id, s["label"], s["seconds"], s.get("tracks", 0), s.get("missing", 0)))
    con.commit()


def stylus_add(con, name, hours, used_h=0, warn=80, installed=None, replaces=None):
    """Fit a cartridge / stylus. Others stay in use unless it replaces one, which is then retired."""
    if replaces:  # the old one came off when this one went on
        stylus_retire(con, replaces, installed)
    cur = con.execute(
        "INSERT INTO styli (name, rated_hours, initial_seconds, warn_pct, installed_at, active) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (name, hours, int(round(used_h * 3600)), warn, (installed or now_local()).isoformat()))
    con.commit()
    set_current_stylus(con, cur.lastrowid)
    return cur.lastrowid


def stylus_use(con, sid):
    """Put a cartridge / stylus on the turntable (bringing it back into use if it was retired)."""
    con.execute("UPDATE styli SET active = 1, retired_at = NULL WHERE id = ?", (sid,))
    con.commit()
    set_current_stylus(con, sid)


def stylus_retire(con, sid, when=None):
    """Retire a worn or replaced cartridge / stylus. Its hours and plays are kept."""
    con.execute("UPDATE styli SET active = 0, retired_at = ? WHERE id = ?", ((when or now_local()).isoformat(), sid))
    con.commit()
    if get_setting(con, "current_stylus") == str(sid):
        nxt = con.execute("SELECT id FROM styli WHERE active = 1 ORDER BY id DESC").fetchone()
        set_current_stylus(con, nxt[0] if nxt else None)


def stylus_edit(con, sid, name=None, hours=None, warn=None, used_h=None, installed=None):
    row = con.execute("SELECT * FROM styli WHERE id = ?", (sid,)).fetchone()
    if not row:
        return False
    con.execute(
        "UPDATE styli SET name = ?, rated_hours = ?, warn_pct = ?, initial_seconds = ?, installed_at = ? "
        "WHERE id = ?", (
            name or row["name"],
            hours if hours is not None else row["rated_hours"],
            warn if warn is not None else row["warn_pct"],
            int(round(used_h * 3600)) if used_h is not None else row["initial_seconds"],
            installed.isoformat() if installed else row["installed_at"],
            sid))
    con.commit()
    return True


def write_csv(con, fh):
    rows = con.execute(
        "SELECT p.played_at, p.record_label, p.side, p.seconds, s.name stylus "
        "FROM plays p LEFT JOIN styli s ON s.id = p.stylus_id ORDER BY p.played_at").fetchall()
    w = csv.writer(fh)
    w.writerow(["played_at", "record", "side", "minutes", "stylus"])
    for r in rows:
        w.writerow([r["played_at"], r["record_label"], r["side"], round(r["seconds"] / 60, 2), r["stylus"] or ""])
    return len(rows)


# --------------------------------------------------------------------------
# Styli: several can be in use at once; one is "on the turntable"
# --------------------------------------------------------------------------

def styli_in_use(con):
    """Cartridges / styli that are fitted and in use, in a fixed order (the order they were added)."""
    return con.execute("SELECT * FROM styli WHERE active = 1 ORDER BY id").fetchall()


def set_current_stylus(con, sid):
    set_setting(con, "current_stylus", str(sid) if sid else "")


def stylus_stats(con, s):
    """Everything worth showing about one cartridge / stylus, retired or not."""
    m = stylus_summary(con, s)
    row = con.execute(
        "SELECT MIN(played_at), MAX(played_at), COUNT(DISTINCT record_label) FROM plays WHERE stylus_id = ?",
        (s["id"],)).fetchone()
    m.update(first=to_dt(row[0]) if row[0] else None, last=to_dt(row[1]) if row[1] else None,
             records=row[2], retired=to_dt(s["retired_at"]) if s["retired_at"] else None,
             in_use=bool(s["active"]))
    return m


def stylus_hours_by_month(con, sid):
    rows = con.execute("SELECT substr(played_at, 1, 7) m, SUM(seconds) FROM plays WHERE stylus_id = ? "
                       "GROUP BY m ORDER BY m", (sid,)).fetchall()
    return [(r[0], r[1]) for r in rows]


def find_stylus(con, ref):
    """A cartridge / stylus by number or by words from its name (in-use ones preferred)."""
    ref = str(ref).strip()
    if ref.isdigit():
        return con.execute("SELECT * FROM styli WHERE id = ?", (int(ref),)).fetchone()
    words = ref.lower().split()
    rows = con.execute("SELECT * FROM styli ORDER BY active DESC, id DESC").fetchall()
    for r in rows:
        if all(w in r["name"].lower() for w in words):
            return r
    return None


def stylus_delete(con, sid):
    """Only for cartridges / styli added by mistake: refuses if any plays were logged with it."""
    if con.execute("SELECT 1 FROM plays WHERE stylus_id = ? LIMIT 1", (sid,)).fetchone():
        return False
    con.execute("DELETE FROM styli WHERE id = ?", (sid,))
    con.commit()
    try:
        stylus_photo_path(sid).unlink()
    except OSError:
        pass
    if get_setting(con, "current_stylus") == str(sid):
        nxt = con.execute("SELECT id FROM styli WHERE active = 1 ORDER BY id DESC").fetchone()
        set_current_stylus(con, nxt[0] if nxt else None)
    return True


# --------------------------------------------------------------------------
# Cartridge photo and specifications
# --------------------------------------------------------------------------

SPEC_FIELDS = [
    ("type", "Type", r"^(cartridge |generator |transducer )?(type|principle|system|generator)\b"),
    ("stylus", "Stylus profile", r"^(stylus|diamond|tip)( type| shape| profile| tip| cut)?\b(?!.*(force|pressure|life))"),
    ("output", "Output", r"^output( voltage| level)?\b"),
    ("tracking", "Tracking force", r"tracking force|^vtf|stylus pressure|tracking weight|downforce"),
    ("compliance", "Compliance", r"compliance"),
    ("frequency", "Frequency response", r"frequency (response|range)"),
    ("separation", "Channel separation", r"channel separation|^separation"),
    ("load", "Load", r"load (impedance|resistance|capacitance)|recommended load|^load\b"),
    ("weight", "Weight", r"^(cartridge |body )?(weight|mass)\b"),
    ("life", "Stylus life", r"stylus life|life ?span|lifetime|life expectancy|playing life"),
]


def stylus_photo_path(sid):
    return (DATA_DIR or default_db_path().parent) / "styli" / "stylus-{}.img".format(sid)


def save_stylus_photo(sid, data):
    path = stylus_photo_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))
    return path


def get_specs(s):
    try:
        return json.loads(s["specs"] or "{}")
    except (ValueError, TypeError, IndexError, KeyError):
        return {}


def set_stylus_details(con, sid, specs=None, url=None):
    row = con.execute("SELECT * FROM styli WHERE id = ?", (sid,)).fetchone()
    merged = get_specs(row)
    for k, v in (specs or {}).items():
        if v:
            merged[k] = v
        elif k in merged and v == "":
            del merged[k]
    con.execute("UPDATE styli SET specs = ?, url = COALESCE(?, url) WHERE id = ?",
                (json.dumps(merged), url, sid))
    con.commit()


def suggest_hours(specs):
    """A rated life in hours if the specs mention one (the lower figure of a range)."""
    m = re.search(r"(\d[\d,.]*)\s*(?:-|to|–)?\s*(?:\d[\d,.]*)?\s*h(?:ou)?rs?\b", (specs or {}).get("life", ""), re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def http_get(url, max_bytes=5 * 1024 * 1024, timeout=20):
    """Fetch a web page or image. Returns (bytes, content type, final url)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; vinyltrack/{}; +stylus lookup)".format(VERSION),
        "Accept": "text/html,application/xhtml+xml,image/*;q=0.9,*/*;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
            return data[:max_bytes], resp.headers.get("Content-Type", ""), resp.geturl()
    except urllib.error.HTTPError as e:
        raise MBError("the page returned HTTP {}".format(e.code))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise MBError("couldn't fetch the page ({})".format(getattr(e, "reason", e)))


def lookup_wikipedia(name):
    """Photo and short description from Wikipedia, when it has an article on the cartridge."""
    params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": name, "gsrlimit": 6,
              "prop": "pageimages|extracts|info", "inprop": "url", "exintro": 1, "explaintext": 1,
              "exsentences": 3, "piprop": "thumbnail", "pithumbsize": 640}
    data, _, _ = http_get("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params))
    pages = sorted((json.loads(data.decode("utf-8")).get("query") or {}).get("pages", {}).values(),
                   key=lambda p: p.get("index", 99))
    want = key_tokens(name)
    models = {w for w in want if re.search(r"\d", w)}
    for p in pages:
        title = key_tokens(p.get("title"))
        if (models and models & title) or len(want & title) >= 2:
            return {"title": p.get("title"), "description": (p.get("extract") or "").strip(),
                    "image_url": (p.get("thumbnail") or {}).get("source"), "url": p.get("fullurl"),
                    "specs": {}, "source": "Wikipedia"}
    return None


class _PageReader(html.parser.HTMLParser):
    """Pulls out meta tags, JSON-LD and 'label: value' lines from a product page."""
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "table", "ul", "ol"}
    SKIP = {"script", "style", "noscript", "svg", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.lines, self.jsonld, self.title = {}, [], [], ""
        self._buf, self._cells, self._cell, self._dt, self._skip, self._in_title = [], None, None, None, 0, False
        self._ld = None

    def _flush(self):
        text = " ".join("".join(self._buf).split())
        if text:
            self.lines.append(text)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._ld = []
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "tr":
            self._flush()
            self._cells = []
        elif tag in ("td", "th") and self._cells is not None:
            self._cell = []
        elif tag in ("dt", "dd"):
            self._flush()
        elif tag in self.BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag == "script" and self._ld is not None:
            self.jsonld.append("".join(self._ld))
            self._ld = None
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in ("td", "th") and self._cell is not None:
            self._cells.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._cells is not None:
            cells = [c for c in self._cells if c]
            if len(cells) >= 2:
                self.lines.append("{}: {}".format(cells[0], " ".join(cells[1:])))
            elif cells:
                self.lines.append(cells[0])
            self._cells = None
        elif tag == "dt":
            self._dt = " ".join("".join(self._buf).split())
            self._buf = []
        elif tag == "dd":
            text = " ".join("".join(self._buf).split())
            self._buf = []
            if self._dt:
                self.lines.append("{}: {}".format(self._dt, text))
                self._dt = None
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._ld is not None:
            self._ld.append(data)
            return
        if self._skip:
            return
        if self._in_title:
            self.title += data
        elif self._cell is not None:
            self._cell.append(data)
        else:
            self._buf.append(data)


def _walk_jsonld(obj):
    if isinstance(obj, list):
        for o in obj:
            yield from _walk_jsonld(o)
    elif isinstance(obj, dict):
        yield obj
        for k in ("@graph", "mainEntity", "offers"):
            if k in obj:
                yield from _walk_jsonld(obj[k])


def extract_specs(lines):
    specs = {}
    for line in lines:
        m = re.match(r"^\s*([^:]{2,45}?)\s*[:：]\s*(.{1,120})$", line)
        if not m:
            continue
        key, value = m.group(1).strip().lower().lstrip("-•* "), m.group(2).strip()
        for field, _, pattern in SPEC_FIELDS:
            if field not in specs and re.search(pattern, key):
                specs[field] = value
                break
    return specs


def name_from_page_title(title):
    """'Ortofon 2M Red MM Cartridge | Hi-Fi Shop' -> 'Ortofon 2M Red MM Cartridge'."""
    for sep in (" | ", " \u2013 ", " \u2014 ", " - ", " :: "):
        if sep in (title or ""):
            title = title.split(sep)[0]
    return " ".join((title or "").split())


def scrape_product_page(url):
    """Photo, description and specs from a manufacturer's or shop's product page."""
    data, ctype, final = http_get(url)
    charset = re.search(r"charset=([\w-]+)", ctype or "")
    text = data.decode(charset.group(1) if charset else "utf-8", errors="replace")
    reader = _PageReader()
    reader.feed(text)
    reader.close()
    lines, image, description, title = list(reader.lines), None, "", ""
    for raw in reader.jsonld:
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        for node in _walk_jsonld(parsed):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "Product" not in types:
                continue
            title = title or node.get("name") or ""
            img = node.get("image")
            if isinstance(img, list):
                img = img[0] if img else None
            if isinstance(img, dict):
                img = img.get("url")
            image = image or img
            description = description or html.unescape(re.sub(r"<[^>]+>", " ", node.get("description") or ""))
            for prop in node.get("additionalProperty") or []:
                if isinstance(prop, dict) and prop.get("name"):
                    lines.append("{}: {}".format(prop["name"], prop.get("value", "")))
    image = reader.meta.get("og:image") or reader.meta.get("twitter:image") or image
    description = description or reader.meta.get("og:description") or reader.meta.get("description") or ""
    lines.extend(re.split(r"[\r\n•]+|\s{2,}", description))
    title = reader.meta.get("og:title") or title or reader.title.strip()
    description = " ".join(description.split())
    if len(description) > 400:
        description = description[:400].rsplit(" ", 1)[0] + "..."
    return {"title": title, "description": description, "specs": extract_specs(lines),
            "image_url": urllib.parse.urljoin(final, image) if image else None, "url": final,
            "source": urllib.parse.urlparse(final).netloc}


# --------------------------------------------------------------------------
# Discogs (https://www.discogs.com/developers): your collection as a source
# --------------------------------------------------------------------------

DISCOGS_API = "https://api.discogs.com"
DISCOGS_TOKEN_PAGE = "https://www.discogs.com/settings/developers"
DISCOGS_STALE_HOURS = 12
_dg_last = [0.0]
_dg_lock = threading.Lock()  # Discogs allows 60 requests a minute with a token


class DiscogsError(MBError):
    pass


def discogs_get(path, token, params=None):
    with _dg_lock:
        wait = 1.05 - (time.monotonic() - _dg_last[0])
        if wait > 0:
            time.sleep(wait)
        url = DISCOGS_API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                                   "Authorization": "Discogs token={}".format(token)})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    _dg_last[0] = time.monotonic()
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                _dg_last[0] = time.monotonic()
                if e.code == 429 and attempt < 2:
                    time.sleep(6 * (attempt + 1))
                    continue
                if e.code == 401:
                    raise DiscogsError("Discogs didn't accept the access token. Check it, or create a new one.")
                if e.code == 403:
                    raise DiscogsError("Discogs says this collection is private to its owner.")
                if e.code == 404:
                    raise DiscogsError("Discogs couldn't find that.")
                raise DiscogsError("Discogs returned HTTP {}".format(e.code))
            except urllib.error.URLError as e:
                raise DiscogsError("couldn't reach Discogs ({})".format(e.reason))
        raise DiscogsError("Discogs is busy, try again shortly")


def discogs_identity(token):
    return discogs_get("/oauth/identity", token)["username"]


def discogs_account(con):
    token, user = get_setting(con, "discogs_token"), get_setting(con, "discogs_user")
    return (token, user) if token and user else (None, None)


def discogs_link(con, token):
    """Check a personal access token and remember it. Returns the Discogs username."""
    token = token.strip()
    user = discogs_identity(token)
    set_setting(con, "discogs_token", token)
    set_setting(con, "discogs_user", user)
    return user


def discogs_unlink(con):
    con.execute("DELETE FROM settings WHERE key IN ('discogs_token', 'discogs_user')")
    con.commit()


def discogs_collection_page(token, user, page=1):
    """One page (100 items) of the user's whole collection. Returns (items, pages, total)."""
    d = discogs_get("/users/{}/collection/folders/0/releases".format(urllib.parse.quote(user)), token,
                    {"page": page, "per_page": 100, "sort": "artist", "sort_order": "asc"})
    pag = d.get("pagination") or {}
    return d.get("releases", []), pag.get("pages", 1), pag.get("items", 0)


def _discogs_cache_path(user):
    return cache_dir().parent / "discogs-{}.json".format(re.sub(r"\W", "_", user))


def load_discogs_cache(user):
    """(items, age in hours) from the last download, or (None, None)."""
    path = _discogs_cache_path(user)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["items"], (time.time() - data["fetched"]) / 3600
    except (OSError, ValueError, KeyError):
        return None, None


def save_discogs_cache(user, items):
    path = _discogs_cache_path(user)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched": time.time(), "items": items}), encoding="utf-8")


def discogs_collection(token, user, refresh=False, progress=None):
    """The whole collection, from the local copy unless it's stale or refresh is asked for."""
    items, age = load_discogs_cache(user)
    if items is not None and not refresh and age < DISCOGS_STALE_HOURS:
        return items
    items, page, pages = [], 1, 1
    while page <= pages:
        batch, pages, total = discogs_collection_page(token, user, page)
        items.extend(batch)
        if progress:
            progress(len(items), total)
        page += 1
    save_discogs_cache(user, items)
    return items


def _clean_artist(name):
    return re.sub(r"\s\(\d+\)$", "", name or "")  # Discogs adds (2), (3) to tell namesakes apart


def discogs_artists(artists):
    artists = artists or []
    out = ""
    for i, a in enumerate(artists):
        out += _clean_artist(a.get("anv") or a.get("name"))
        join = (a.get("join") or "").strip()
        if i < len(artists) - 1:
            out += ", " if join in ("", ",") else " {} ".format(join)
    return out or "Unknown artist"


def discogs_formats(formats):
    parts = []
    for f in formats or []:
        qty = str(f.get("qty") or "1")
        bits = ["{}x{}".format(qty, f.get("name")) if qty not in ("", "1") else (f.get("name") or "")]
        bits += [d for d in f.get("descriptions") or [] if d not in bits]
        parts.append(", ".join(b for b in bits if b))
    return "; ".join(parts)


def discogs_item_info(item):
    """Plain summary of a collection item."""
    bi = item.get("basic_information") or {}
    labels = bi.get("labels") or []
    lab = "{} {}".format(labels[0].get("name", ""), labels[0].get("catno", "")).strip() if labels else ""
    return {"id": bi.get("id"), "artist": discogs_artists(bi.get("artists")), "title": bi.get("title") or "",
            "year": str(bi.get("year") or "") if bi.get("year") else "", "format": discogs_formats(bi.get("formats")),
            "label": lab, "cover": bi.get("cover_image") or bi.get("thumb") or None,
            "vinyl": any((f.get("name") or "").lower() == "vinyl" for f in bi.get("formats") or []),
            "added": (item.get("date_added") or "")[:10]}


def describe_discogs_item(item):
    i = discogs_item_info(item)
    return "{} - {}{}  [{}]{}".format(i["artist"], i["title"], " ({})".format(i["year"]) if i["year"] else "",
                                       i["format"], "  " + i["label"] if i["label"] else "")


def _duration_ms(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        return parse_duration(text) * 1000
    except ValueError:
        return None


def discogs_to_release(rel):
    """A Discogs release reshaped like a MusicBrainz one, so build_sides can split it into sides."""
    tracks = []

    def add(t):
        pos = (t.get("position") or "").strip()
        if pos:
            tracks.append({"number": pos, "title": t.get("title", ""), "length": _duration_ms(t.get("duration"))})

    for t in rel.get("tracklist") or []:
        kind = t.get("type_") or "track"
        if kind == "heading":
            continue
        if kind == "index" and t.get("sub_tracks") and not (t.get("position") and t.get("duration")):
            for st in t["sub_tracks"]:
                add(st)
        else:
            add(t)
    return {"id": None, "title": rel.get("title"), "date": str(rel.get("year") or ""),
            "artist-credit": [{"name": discogs_artists(rel.get("artists")), "joinphrase": ""}],
            "media": [{"format": discogs_formats(rel.get("formats")), "tracks": tracks}]}


def mb_release_for_discogs(discogs_id):
    """The MusicBrainz release linked to this exact Discogs release, if anyone has linked them."""
    try:
        data = mb_get("url", {"resource": "https://www.discogs.com/release/{}".format(discogs_id),
                              "inc": "release-rels"})
    except MBError:
        return None
    for rel in data.get("relations") or []:
        if rel.get("release"):
            return rel["release"]["id"]
    return None


def discogs_barcode(rel):
    for ident in rel.get("identifiers") or []:
        if (ident.get("type") or "").lower() == "barcode":
            digits = re.sub(r"\D", "", ident.get("value") or "")
            if len(digits) >= 8:
                return digits
    return None


def prepare_discogs_release(token, discogs_id):
    """Everything needed to add a Discogs release: details, sides and cover.

    Discogs track lists often lack lengths, so missing ones are filled from MusicBrainz:
    first the release MusicBrainz links to this Discogs entry, then a barcode match.
    """
    rel = discogs_get("/releases/{}".format(int(discogs_id)), token)
    shaped = discogs_to_release(rel)
    sides = build_sides(shaped, allow_prompt=False)
    out = {"discogs_id": int(discogs_id), "artist": discogs_artists(rel.get("artists")), "title": rel.get("title"),
           "year": str(rel.get("year") or "") if rel.get("year") else "", "format": discogs_formats(rel.get("formats")),
           "mbid": None, "rgid": None, "notes": [], "estimated": not release_has_side_letters(shaped)}
    images = rel.get("images") or []
    primary = [i for i in images if i.get("type") == "primary"] or images
    out["cover_url"] = (primary[0].get("uri") or primary[0].get("uri150")) if primary else None
    need = not sides or any(s["missing"] for s in sides)
    if need:
        mb_id, how = mb_release_for_discogs(discogs_id), "linked to this Discogs release"
        if not mb_id and discogs_barcode(rel):
            try:
                found, _ = search_releases(barcode=discogs_barcode(rel), vinyl=False, limit=10)
            except MBError:
                found = []
            vinyl = [r for r in found if is_vinyl(r)]
            mb_id, how = ((vinyl or found)[0]["id"] if found else None), "matched by barcode"
        if mb_id:
            try:
                mbrel = fetch_release(mb_id)
            except MBError:
                mbrel = None
            mb_sides = build_sides(mbrel, allow_prompt=False) if mbrel else []
            if mb_sides:
                if not sides:
                    sides, out["estimated"] = mb_sides, not release_has_side_letters(mbrel)
                else:
                    by_label = {s["label"]: s for s in mb_sides}
                    for s in sides:
                        m = by_label.get(s["label"])
                        if s["missing"] and m and not m["missing"]:
                            s["seconds"], s["missing"] = m["seconds"], 0
                out["mbid"] = mbrel.get("id")
                out["rgid"] = (mbrel.get("release-group") or {}).get("id")
                out["notes"].append("Track lengths missing on Discogs were taken from MusicBrainz ({}).".format(how))
    if any(s["missing"] for s in sides):
        out["notes"].append("Some track lengths are on neither Discogs nor MusicBrainz, so check the sides "
                            "marked below against the sleeve or label.")
    if out["estimated"] and sides:
        out["notes"].append("The track list doesn't mark sides (A1, B1...), so the split between sides "
                            "has been estimated.")
    out["sides"] = sides
    return out


def fetch_image_cached(url):
    """Any image by URL (Discogs covers), cached on disk. Returns a path or None."""
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / "url-{}.img".format(hashlib.sha1(url.encode("utf-8")).hexdigest())
    if path.exists() and path.stat().st_size:
        return path
    try:
        data, ctype, _ = http_get(url)
    except MBError:
        return None
    if not data:
        return None
    tmp = path.with_name(path.name + ".{}.tmp".format(threading.get_ident()))
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))
    return path


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_add(con, args):
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
        rel = fetch_release(mbid)
    except MBError as e:
        err(str(e))
        if interactive() and confirm("Enter the record manually instead?"):
            return add_manual(con, args)
        return 1

    artist = artist_credit_str(rel.get("artist-credit"))
    title = rel.get("title") or "Untitled"
    year = (rel.get("date") or "")[:4]
    fmt = media_summary(rel.get("media"))
    sides = build_sides(rel)
    if not sides:
        err("MusicBrainz has no track listing for this release.")
        if interactive() and confirm("Enter side lengths manually?"):
            args.artist, args.title = args.artist or artist, args.title or title
            return add_manual(con, args)
        return 1
    review_missing(sides)

    print("\n{} - {}{}  [{}]".format(artist, title, " ({})".format(year) if year else "", fmt))
    for s in sides:
        print("  Side {:<3} {:>8}  ({} tracks)".format(s["label"], fmt_dur(s["seconds"]), s["tracks"]))
    print("  Total    {:>8}".format(fmt_dur(sum(s["seconds"] for s in sides))))
    if not confirm("Save this record?"):
        print("Not saved.")
        return 1
    rgid = (rel.get("release-group") or {}).get("id")
    rid = save_record(con, rel.get("id", mbid), artist, title, year, fmt, sides, rgid)
    print("Saved as #{}. Log a play with: vt play {} [side]".format(rid, rid))
    if ensure_record_cover(rid, rel.get("id", mbid), rgid)[0]:
        print("Album cover saved.")
    return 0


def choose(items, describe, heading):
    """Let the user pick from a numbered list. Typing words filters the list."""
    if len(items) == 1:
        print("{}: {}".format(heading, describe(items[0])))
        return items[0]
    if not interactive():
        if ASSUME_YES:
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
    token, user = discogs_account(con)
    if not token:
        err("link your Discogs account first: vt discogs link <token>\n"
            "Create a personal access token at {}".format(DISCOGS_TOKEN_PAGE))
        return 1
    try:
        rid = args.discogs_id
        if not rid:
            print("Reading your Discogs collection...")
            items = discogs_collection(token, user, progress=lambda n, t: print("  {} of {}".format(n, t)))
            if not args.any_format:
                items = [i for i in items if discogs_item_info(i)["vinyl"]]
            words = " ".join(args.query or []).lower().split()
            items = [i for i in items if all(w in describe_discogs_item(i).lower() for w in words)]
            if not items:
                err("nothing in your Discogs collection matches. Try fewer words, or --any-format.")
                return 1
            pick = choose(items, describe_discogs_item, "Your Discogs collection")
            if not pick:
                return 1
            rid = discogs_item_info(pick)["id"]
        dup = con.execute("SELECT id, artist, title FROM records WHERE discogs_id = ?", (int(rid),)).fetchone()
        if dup:
            print("Already in your library as #{}: {} - {}".format(dup["id"], dup["artist"], dup["title"]))
            return 0
        print("Fetching the track list...")
        info = prepare_discogs_release(token, rid)
    except MBError as e:
        err(str(e))
        return 1
    for note in info["notes"]:
        print("Note: " + note)
    sides = info["sides"]
    if not sides:
        err("this release has no track list on Discogs or MusicBrainz. Use --manual to enter it.")
        return 1
    review_missing(sides)
    print("\n{} - {}{}  [{}]".format(info["artist"], info["title"], " ({})".format(info["year"]) if info["year"] else "",
                                     info["format"]))
    for s in sides:
        print("  Side {:<3} {:>8}  ({} tracks)".format(s["label"], fmt_dur(s["seconds"]), s["tracks"]))
    if not confirm("Save this record?"):
        print("Not saved.")
        return 1
    rid2 = save_record(con, info["mbid"], info["artist"], info["title"], info["year"], info["format"], sides,
                       info["rgid"], info["discogs_id"], info["cover_url"])
    print("Saved as #{}. Log a play with: vt play {} [side]".format(rid2, rid2))
    if ensure_record_cover(rid2, info["mbid"], info["rgid"], cover_url=info["cover_url"])[0]:
        print("Album cover saved.")
    return 0


def pick_release(args):
    if args.barcode:
        results, _ = search_releases(barcode=args.barcode, vinyl=False, limit=25)
        if not results:
            err("no release on MusicBrainz has that barcode. Try --artist and --title.")
            return None
        r = choose(results, describe_release, "Pressings with that barcode")
        return r["id"] if r else None

    free = " ".join(args.query) if args.query else None
    artist, title = args.artist, args.title
    if not (free or artist or title):
        if not interactive():
            err('give the artist and album, e.g. vt add --artist "fleetwood mac" --title rumours')
            return None
        artist = ask("Artist: ") or None
        title = ask("Album (blank to list all their albums): ") or None
        if not (artist or title):
            return None
    albums = search_albums(artist, title, free)
    if not albums:
        tip = (" With a single search box, type the artist's full name then the album, "
               "or use --artist and --title.") if free else ""
        err("no album found by that artist with that title. Check the spelling.{}".format(tip))
        return None
    album = choose(albums, describe_album, "Albums")
    if not album:
        return None

    print("Loading pressings of {}...".format(album.get("title")))
    pressings, total = list_pressings(album["id"])
    if not args.any_format:
        vinyl = [r for r in pressings if is_vinyl(r)]
        if vinyl:
            pressings = vinyl
        else:
            print("MusicBrainz lists no vinyl pressings of this album, so all formats are shown.")
    if not pressings:
        err("MusicBrainz lists no pressings for this album. Use --manual to enter it yourself.")
        return None
    if total > PRESSING_CAP:
        print("Note: this album has {} releases; the first {} are listed.".format(total, PRESSING_CAP))
    print("Pick the pressing you own. The label, catalogue number and barcode on your copy help.")
    r = choose(pressings, lambda p: describe_pressing(p, album.get("title")), "Pressings")
    return r["id"] if r else None


def add_manual(con, args):
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
    rid = save_record(con, None, artist, title, args.year or "", "Vinyl", sides)
    print("Saved as #{}: {} - {} ({})".format(
        rid, artist, title, ", ".join("{} {}".format(s["label"], fmt_dur(s["seconds"])) for s in sides)))
    return 0


def cmd_play(con, args):
    rec = resolve_record(con, args.record)
    if not rec:
        return 1
    sides = {s["label"]: s for s in record_sides(con, rec["id"])}
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
    entries = play_entries(sides, chosen, custom)
    chosen_stylus = None
    if args.stylus:
        chosen_stylus = find_stylus(con, args.stylus)
        if not chosen_stylus:
            err("no cartridge / stylus matches '{}'. See `vt stylus list`.".format(args.stylus))
            return 1
    stylus, total = log_play(con, rec, entries, when, args.times, chosen_stylus)
    what = ", ".join("side {} ({})".format(s, fmt_dur(sec)) for s, sec in entries)
    times = " x{}".format(args.times) if args.times > 1 else ""
    print("Logged {}{} of {} - {}: {}".format(what, times, rec["artist"], rec["title"], fmt_dur(total)))
    if stylus:
        print(stylus_alert(con, stylus))
    else:
        print("No cartridge / stylus set up, so this play is not counted against one. Add one with `vt stylus add`.")
    return 0


def cmd_status(con, args):
    in_use = styli_in_use(con)
    cur = active_stylus(con)
    if not in_use:
        print("No cartridge / stylus in use. Add one with: vt stylus add \"Name\" --hours <rated life>")
    for n, s in enumerate(in_use):
        m = stylus_summary(con, s)
        print("{}Cartridge / stylus #{}  {}  (installed {}){}".format("\n" if n else "", s["id"], s["name"], m["installed"].date(),
                                                       "  <- on the turntable" if cur and s["id"] == cur["id"] else ""))
        print("  Used       {} of {:g} h  ({:.1f}%)".format(fmt_hours(m["used"]), s["rated_hours"], m["pct"]))
        print("  {}".format(stylus_bar(m["pct"])))
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
        print("\n{} retired cartridge{} / styl{} (see `vt stylus list`).".format(
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


def cmd_list(con, args):
    rows = con.execute("SELECT * FROM records ORDER BY artist, title").fetchall()
    if args.filter:
        words = " ".join(args.filter).lower().split()
        rows = [r for r in rows if all(w in "{} {}".format(r["artist"], r["title"]).lower() for w in words)]
    if not rows:
        print("No records yet. Add one with: vt add \"artist album\"")
        return 0
    for r in rows:
        sides = record_sides(con, r["id"])
        plays = con.execute("SELECT COUNT(*), MAX(played_at) FROM plays WHERE record_id = ?", (r["id"],)).fetchone()
        side_txt = "  ".join("{} {}{}".format(s["label"], fmt_dur(s["seconds"]), "*" if s["missing"] else "")
                             for s in sides)
        last = ", last {}".format(to_dt(plays[1]).date()) if plays[1] else ""
        print("#{:<4} {} - {}{}".format(r["id"], r["artist"], r["title"], " ({})".format(r["year"]) if r["year"] else ""))
        print("      {}   [{} plays{}]".format(side_txt, plays[0], last))
    if any(s["missing"] for r in rows for s in record_sides(con, r["id"])):
        print("\n* some track lengths were missing on MusicBrainz; fix with `vt side`.")
    return 0


def cmd_history(con, args):
    where, params = "", []
    if args.stylus:
        s = find_stylus(con, args.stylus)
        if not s:
            err("no cartridge / stylus matches '{}'.".format(args.stylus))
            return 1
        where, params = "WHERE p.stylus_id = ?", [s["id"]]
        m = stylus_stats(con, s)
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


def cmd_undo(con, args):
    rows = con.execute("SELECT * FROM plays ORDER BY id DESC LIMIT ?", (args.n,)).fetchall()
    if not rows:
        print("Nothing to undo.")
        return 0
    for p in rows:
        print("  {}  side {}  {}".format(p["record_label"], p["side"], fmt_dur(p["seconds"])))
    if not confirm("Remove {} play entr{}?".format(len(rows), "y" if len(rows) == 1 else "ies")):
        return 1
    con.executemany("DELETE FROM plays WHERE id = ?", [(p["id"],) for p in rows])
    con.commit()
    print("Removed.")
    return 0


def cmd_side(con, args):
    rec = resolve_record(con, args.record)
    if not rec:
        return 1
    label = args.label.upper()
    if args.delete:
        con.execute("DELETE FROM sides WHERE record_id = ? AND label = ?", (rec["id"], label))
        con.commit()
        print("Removed side {} from {}.".format(label, rec["title"]))
        return 0
    if not args.length:
        err("give a length, e.g. vt side {} {} 21:34".format(rec["id"], label))
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
    rec = resolve_record(con, args.record)
    if not rec:
        return 1
    if not confirm("Remove {} - {} from the library? Logged plays are kept.".format(rec["artist"], rec["title"]), False):
        return 1
    con.execute("DELETE FROM records WHERE id = ?", (rec["id"],))
    con.commit()
    remove_record_cover(rec["id"])
    print("Removed.")
    return 0


def cmd_export(con, args):
    if args.file == "-":
        write_csv(con, sys.stdout)
        return 0
    with open(args.file, "w", newline="", encoding="utf-8") as fh:
        n = write_csv(con, fh)
    print("Exported {} plays to {}".format(n, args.file))
    return 0


def cmd_stylus(con, args):
    act = args.stylus_cmd or "list"
    if act == "add":
        try:
            installed = parse_when(args.installed) if args.installed else None
        except ValueError as e:
            err(str(e))
            return 1
        old = find_stylus(con, args.replaces) if args.replaces else None
        if args.replaces and not old:
            err("no cartridge / stylus matches '{}'.".format(args.replaces))
            return 1
        sid = stylus_add(con, args.name, args.hours, args.used, args.warn, installed, old["id"] if old else None)
        print("Cartridge / stylus #{} '{}' is on the turntable (rated {:g} h, warning at {:g}%).".format(
            sid, args.name, args.hours, args.warn))
        if old:
            print("Retired #{} '{}'; its {} of plays are kept.".format(old["id"], old["name"],
                                                                     fmt_hours(stylus_used_seconds(con, old))))
        elif len(styli_in_use(con)) > 1:
            print("Your other cartridges / styli stay in use. Retire a replaced one with: vt stylus retire <number>")
        return 0
    if act in ("use", "retire", "edit", "show", "lookup", "delete"):
        row = find_stylus(con, args.id)
        if not row:
            err("no cartridge / stylus matches '{}'.".format(args.id))
            return 1
        sid = row["id"]
        if act == "use":
            stylus_use(con, sid)
            print("Cartridge / stylus #{} '{}' is now on the turntable.".format(sid, row["name"]))
        elif act == "retire":
            stylus_retire(con, sid)
            print("Cartridge / stylus #{} retired after {}. Its plays are kept.".format(sid, fmt_hours(stylus_used_seconds(con, row))))
        elif act == "delete":
            if not stylus_delete(con, sid):
                err("plays were logged with this cartridge / stylus, so it can only be retired.")
                return 1
            print("Cartridge / stylus #{} deleted.".format(sid))
        elif act == "edit":
            stylus_edit(con, sid, args.name, args.hours, args.warn, args.used)
            if args.url:
                set_stylus_details(con, sid, url=args.url)
            print("Cartridge / stylus #{} updated.".format(sid))
        elif act == "show":
            return show_stylus(con, row)
        else:
            return lookup_stylus(con, row, args.url)
        return 0
    rows = con.execute("SELECT * FROM styli ORDER BY active DESC, id").fetchall()
    if not rows:
        print("No cartridges / styli yet. Add one with: vt stylus add \"Name\" --hours <rated life>")
    cur = active_stylus(con)
    for s in rows:
        m = stylus_stats(con, s)
        state = ("ON TURNTABLE" if cur and s["id"] == cur["id"] else "in use") if s["active"] else \
            "retired {}".format(m["retired"].date() if m["retired"] else "")
        print("#{:<3} {:<28} {:>9} / {:g} h ({:5.1f}%)  {:>4} sides  fitted {}  {}".format(
            s["id"], s["name"][:28], fmt_hours(m["used"]), s["rated_hours"], m["pct"], m["sides_played"],
            m["installed"].date(), state))
    return 0


def show_stylus(con, s):
    m = stylus_stats(con, s)
    print("#{}  {}".format(s["id"], s["name"]))
    print("  {:<19} {}".format("Status", "in use" if m["in_use"] else "retired {}".format(
        m["retired"].date() if m["retired"] else "")))
    print("  {:<19} {} of {:g} h ({:.1f}%) over {} sides of {} records".format(
        "Used", fmt_hours(m["used"]), s["rated_hours"], m["pct"], m["sides_played"], m["records"]))
    print("  {:<19} {}".format("Fitted", m["installed"].date()))
    if m["first"]:
        print("  {:<19} {} to {}".format("Played", m["first"].date(), m["last"].date()))
    specs = get_specs(s)
    for key, label, _ in SPEC_FIELDS:
        if specs.get(key):
            print("  {:<19} {}".format(label, specs[key]))
    if specs.get("description"):
        print("  {}".format(specs["description"]))
    if s["url"]:
        print("  {:<19} {}".format("Details", s["url"]))
    months = stylus_hours_by_month(con, s["id"])
    if months:
        print("  Hours by month:")
        for month, secs in months[-12:]:
            print("    {}  {:>6}  {}".format(month, fmt_hours(secs), "#" * max(1, int(secs / 3600))))
    return 0


def lookup_stylus(con, s, url=None):
    try:
        found = scrape_product_page(url) if url else lookup_wikipedia(s["name"])
    except MBError as e:
        err(str(e))
        return 1
    if not found:
        print("Wikipedia has no article on '{}'. Try a product page: vt stylus lookup {} --url <link>".format(
            s["name"], s["id"]))
        return 1
    print("Found on {}: {}".format(found["source"], found["title"]))
    if found.get("description"):
        print("  " + found["description"][:300])
    specs = dict(found["specs"])
    if found.get("description"):
        specs["description"] = found["description"]
    for key, label, _ in SPEC_FIELDS:
        if specs.get(key):
            print("  {:<18} {}".format(label, specs[key]))
    set_stylus_details(con, s["id"], specs, found.get("url"))
    if found.get("image_url"):
        try:
            data, ctype, _ = http_get(found["image_url"])
            if ctype.startswith("image/") or data[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1"):
                save_stylus_photo(s["id"], data)
                print("Photo saved.")
        except MBError:
            print("Couldn't download the photo.")
    hours = suggest_hours(specs)
    if hours:
        print("The page gives a stylus life of {:g} h. Set it with: vt stylus edit {} --hours {:g}".format(
            hours, s["id"], hours))
    return 0


def cmd_covers(con, args):
    rows = con.execute("SELECT * FROM records ORDER BY artist, title").fetchall()
    saved = missing = 0
    for r in rows:
        if library_cover_path(r["id"]).exists():
            continue
        path, rgid = ensure_record_cover(r["id"], r["mbid"], r["rgid"], cover_url=r["cover_url"])
        if rgid:
            con.execute("UPDATE records SET rgid = ? WHERE id = ?", (rgid, r["id"]))
            con.commit()
        if path:
            saved += 1
        else:
            missing += 1
            print("No cover found: {} - {}".format(r["artist"], r["title"]))
    print("{} cover{} saved, {} without one. Covers are kept in {}".format(
        saved, "" if saved == 1 else "s", missing, library_cover_path(0).parent))
    return 0


def cmd_discogs(con, args):
    act = args.discogs_cmd or "status"
    if act == "link":
        try:
            user = discogs_link(con, args.token)
        except MBError as e:
            err(str(e))
            return 1
        print("Linked to Discogs as {}. Add records from your collection with: vt add --discogs [words]".format(user))
        return 0
    if act == "unlink":
        discogs_unlink(con)
        print("Discogs account unlinked. The access token has been removed from this computer.")
        return 0
    token, user = discogs_account(con)
    if not token:
        print("Not linked. Create a personal access token at {}\nthen run: vt discogs link <token>".format(
            DISCOGS_TOKEN_PAGE))
        return 0 if act == "status" else 1
    if act == "status":
        items, age = load_discogs_cache(user)
        print("Linked to Discogs as {}.{}".format(user, " {} items in the local copy of your collection.".format(
            len(items)) if items is not None else ""))
        return 0
    try:
        items = discogs_collection(token, user, refresh=args.refresh,
                                   progress=lambda n, t: print("  read {} of {}".format(n, t)))
    except MBError as e:
        err(str(e))
        return 1
    have = {r[0] for r in con.execute("SELECT discogs_id FROM records WHERE discogs_id IS NOT NULL")}
    words = " ".join(args.filter or []).lower().split()
    shown = 0
    for it in items:
        text = describe_discogs_item(it)
        if all(w in text.lower() for w in words):
            i = discogs_item_info(it)
            print("{:>10}  {}{}".format(i["id"], text, "  [in library]" if i["id"] in have else ""))
            shown += 1
    print("{} of {} items. Add one with: vt add --discogs-id <number>".format(shown, len(items)))
    return 0


def cmd_gui(con, args):
    """Open the PyQt6 window, which lives in vinyltrack_gui.py next to this file."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import vinyltrack_gui
    except ImportError as e:
        if (getattr(e, "name", "") or "").startswith("PyQt6") or "PyQt6" in str(e):
            err("PyQt6 is not installed. Install it with your package manager, e.g.\n"
                "  Debian/Ubuntu/Mint: sudo apt install python3-pyqt6\n"
                "  Fedora:             sudo dnf install python3-pyqt6\n"
                "  Arch/Manjaro:       sudo pacman -S python-pyqt6\n"
                "  Others:             pip install PyQt6")
        else:
            err("vinyltrack_gui.py needs to be in the same folder as vinyltrack.py ({}).".format(here))
        return 1
    return vinyltrack_gui.run(con, sys.modules[__name__])


def cmd_desktop(con, args):
    if not sys.platform.startswith("linux"):
        err("desktop menu entries are a Linux feature.")
        return 1
    apps = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    target = apps / "vinyltrack.desktop"
    env = ""
    if os.environ.get("VINYLTRACK_CONTACT"):
        env = 'env VINYLTRACK_CONTACT="{}" '.format(os.environ["VINYLTRACK_CONTACT"].replace('"', ""))
    exec_line = '{}"{}" "{}" --db "{}" gui'.format(env, sys.executable, Path(__file__).resolve(), args.db)
    target.write_text(
        "[Desktop Entry]\nType=Application\nName=Vinyl Cartridge / Stylus Tracker\n"
        "Comment=Log record plays and track cartridge / stylus wear\n"
        "Exec={}\nIcon=media-optical\nTerminal=false\nCategories=AudioVideo;Audio;Utility;\n".format(exec_line),
        encoding="utf-8")
    print("Added 'Vinyl Cartridge / Stylus Tracker' to your applications menu ({}).".format(target))
    return 0


# --------------------------------------------------------------------------
# Command-line interface
# --------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="vt",
        description="Log vinyl plays by side and track cartridge / stylus wear. Running times come from MusicBrainz.",
        epilog="Examples:\n"
               "  vt stylus add \"Ortofon 2M Red\" --hours 1000\n"
               "  vt add --artist \"the rolling stones\" --title \"some girls\"\n"
               "  vt add fleetwood mac rumours\n"
               "  vt play \"dark side\"          (both sides)\n"
               "  vt play 3 B                  (side B of record #3)\n"
               "  vt status\n"
               "  vt gui                       (open the window)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=str(default_db_path()), help="database file (default: %(default)s)")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask questions; accept defaults")
    p.add_argument("--version", action="version", version="vinyltrack " + VERSION)
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

    l = sub.add_parser("list", help="list records in your library")
    l.add_argument("filter", nargs="*")

    h = sub.add_parser("history", help="show recent plays")
    h.add_argument("-n", type=int, default=20)
    h.add_argument("--stylus", "--cartridge", dest="stylus", help="only plays with this cartridge / stylus (number or name), retired ones too")

    u = sub.add_parser("undo", help="remove the most recent play entries")
    u.add_argument("-n", type=int, default=1)

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
    "add": cmd_add, "play": cmd_play, "status": cmd_status, "list": cmd_list,
    "history": cmd_history, "undo": cmd_undo, "side": cmd_side, "remove": cmd_remove,
    "export": cmd_export, "stylus": cmd_stylus, "gui": cmd_gui, "covers": cmd_covers, "discogs": cmd_discogs, "cartridge": cmd_stylus, "desktop-entry": cmd_desktop,
}


def main(argv=None):
    global ASSUME_YES
    args = build_parser().parse_args(argv)
    ASSUME_YES = args.yes
    con = connect(args.db)
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


if __name__ == "__main__":
    sys.exit(main())
