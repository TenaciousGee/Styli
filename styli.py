#!/usr/bin/env python3
"""
Styli: log vinyl plays by side and track cartridge / stylus hours.

Record and side running times are fetched automatically from MusicBrainz
(https://musicbrainz.org), which lists vinyl track positions such as A1, A2,
B1 so each side can be timed separately.

Run `styli gui` for the desktop window (PyQt6, in styli_gui.py), or use the
commands below. Album art comes from the Cover Art Archive.

The commands need only the Python standard library (3.8+); the window also
needs PyQt6. Runs on Linux, macOS and Windows.
Data is kept in a single SQLite file:
  Linux    $XDG_DATA_HOME/styli/styli.db (default ~/.local/share/...)
  macOS    ~/Library/Application Support/styli/styli.db
  Windows  %APPDATA%\\styli\\styli.db
Override with the STYLI_DB environment variable or --db. A library kept under
the app's earlier name (vinyltrack) is moved across automatically.
`vt` works as a short name for the `styli` command.

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
import subprocess
import sqlite3
import unicodedata
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "1.10.0"
MB_ROOT = "https://musicbrainz.org/ws/2"
# MusicBrainz asks every client to send a meaningful User-Agent with contact
# details. Set STYLI_CONTACT to your email or a project URL.
APP_NAME = "Styli"
OLD_NAME = "vinyltrack"  # the app's earlier name, used to find and move an existing library
CONTACT = os.environ.get("STYLI_CONTACT") or os.environ.get("VINYLTRACK_CONTACT") or ""
USER_AGENT = "Styli/{} ( {} )".format(VERSION, CONTACT or "personal record-play and stylus-hours tracker"
)
RECENT_WINDOW_DAYS = 56  # window used to estimate listening rate in `status`

ASSUME_YES = False
DATA_DIR = None  # folder holding the database; set by connect()


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def data_base():
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))


def cache_base():
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches"
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))


# --------------------------------------------------------------------------
# Privacy: folders only you can read, and secrets kept in the system keyring
# --------------------------------------------------------------------------

def private_files():
    """New files and folders are readable by you only (Linux and Mac; Windows keeps each person's
    AppData private already)."""
    if os.name == "posix":
        os.umask(0o077)


def make_private(path):
    """Limit an existing file or folder to your own user account (Linux and Mac)."""
    if os.name != "posix":
        return
    try:
        os.chmod(str(path), 0o700 if Path(path).is_dir() else 0o600)
    except OSError:
        pass


def secure_folders(db_path):
    """Lock down the database, the folders Styli keeps beside it, and Styli's cache."""
    db_path = Path(db_path)
    folder = db_path.parent
    if folder == default_db_path().parent or folder.name.lower() == "styli":
        make_private(folder)  # only a folder that's Styli's own, never e.g. Downloads
    for suffix in ("", "-wal", "-shm", "-journal"):
        f = Path(str(db_path) + suffix)
        if f.exists():
            make_private(f)
    for sub in (folder / "covers", folder / "cartridges", cache_base() / "styli"):
        if sub.is_dir():
            make_private(sub)
            for root, dirs, files in os.walk(str(sub)):
                for name in dirs + files:
                    make_private(Path(root) / name)


KEYRING_SERVICE = "Styli"


def system_keyring():
    """The keyring module when a real keyring is available (GNOME Keyring or KWallet on Linux,
    Keychain on a Mac, Credential Manager on Windows), otherwise None."""
    try:
        import keyring
        backend = keyring.get_keyring()
    except Exception:
        return None
    if getattr(backend, "priority", 1) <= 0 or type(backend).__module__.endswith((".fail", ".null")):
        return None
    return keyring


class SecretError(Exception):
    pass


def _secret_name(con, name):
    """Each library has its own entry, so a copy of the database on another computer (or the demo
    library) never picks up your token."""
    lib = get_setting(con, "library_id")
    if not lib:
        lib = hashlib.sha1(os.urandom(16)).hexdigest()[:12]
        set_setting(con, "library_id", lib)
    return "{}-{}".format(name, lib)


def secret_store(con, name):
    """Where a secret is kept: "keyring", "file" or None (not saved)."""
    return get_setting(con, name + "_store") or ("file" if get_setting(con, name) else None)


def _forget_in_database(con, name):
    con.execute("PRAGMA secure_delete = ON")  # overwrite it, rather than leave it in free space
    con.execute("DELETE FROM settings WHERE key = ?", (name,))
    con.commit()


def set_secret(con, name, value):
    """Save a secret in the system keyring, or in the database if there's no keyring.
    Returns where it went: "keyring" or "file"."""
    kr = system_keyring()
    if kr:
        try:
            kr.set_password(KEYRING_SERVICE, _secret_name(con, name), value)
            _forget_in_database(con, name)
            set_setting(con, name + "_store", "keyring")
            return "keyring"
        except Exception:
            pass  # a locked or refused keyring: fall back rather than lose the token
    set_setting(con, name, value)
    set_setting(con, name + "_store", "file")
    return "file"


def get_secret(con, name):
    """A saved secret, moving it into the keyring if it was saved before one was available."""
    store = secret_store(con, name)
    if store == "keyring":
        kr = system_keyring()
        if not kr:
            raise SecretError("the system keyring isn't available")
        try:
            return kr.get_password(KEYRING_SERVICE, _secret_name(con, name))
        except Exception as e:
            raise SecretError("the system keyring couldn't be read ({})".format(e))
    value = get_setting(con, name)
    if value and system_keyring():
        set_secret(con, name, value)  # upgrade: out of the database and into the keyring
    return value


def delete_secret(con, name):
    if secret_store(con, name) == "keyring":
        kr = system_keyring()
        if kr:
            try:
                kr.delete_password(KEYRING_SERVICE, _secret_name(con, name))
            except Exception:
                pass
    _forget_in_database(con, name)
    con.execute("DELETE FROM settings WHERE key = ?", (name + "_store",))
    con.commit()


def default_db_path():
    env = os.environ.get("STYLI_DB") or os.environ.get("VINYLTRACK_DB")
    if env:
        return Path(env).expanduser()
    return data_base() / "styli" / "styli.db"


def migrate_old_library():
    """Move a library kept under the app's earlier name into Styli's folder.

    Only runs when there is an old library and no Styli library yet, so nothing is
    ever overwritten. Returns a message describing the move, or None.
    """
    if os.environ.get("STYLI_DB") or os.environ.get("VINYLTRACK_DB"):
        return None
    old, new = data_base() / OLD_NAME, data_base() / "styli"
    old_db = old / (OLD_NAME + ".db")
    if not old_db.exists() or (new / "styli.db").exists():
        return None
    new.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm", "-journal"):
        src = old / (OLD_NAME + ".db" + suffix)
        if src.exists():
            shutil.move(str(src), str(new / ("styli.db" + suffix)))
    for sub, dest in (("covers", "covers"), ("styli", "cartridges")):
        if (old / sub).is_dir() and not (new / dest).exists():
            shutil.move(str(old / sub), str(new / dest))
    shutil.rmtree(str(cache_base() / OLD_NAME), ignore_errors=True)  # only downloaded previews
    return "Moved your library to {}".format(new)

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
CREATE TABLE IF NOT EXISTS tracks (
    record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    n         INTEGER NOT NULL,
    position  TEXT,
    side      TEXT,
    title     TEXT,
    seconds   INTEGER
);
CREATE INDEX IF NOT EXISTS tracks_record ON tracks(record_id);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path):
    global DATA_DIR
    path = Path(path)
    private_files()
    path.parent.mkdir(parents=True, exist_ok=True)
    DATA_DIR = path.parent
    old_photos = DATA_DIR / "styli"  # cartridge photos' folder before the rename
    if old_photos.is_dir() and any(old_photos.glob("stylus-*.img")) and not (DATA_DIR / "cartridges").exists():
        old_photos.rename(DATA_DIR / "cartridges")
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA secure_delete = ON")  # deleted data is overwritten, not left behind in the file
    secure_folders(path)
    con.executescript(SCHEMA)
    cols = {r["name"] for r in con.execute("PRAGMA table_info(records)")}
    if "rgid" not in cols:  # album ID, added in v1.1 for album art
        con.execute("ALTER TABLE records ADD COLUMN rgid TEXT")
        con.commit()
    for col, kind in (("discogs_id", "INTEGER"), ("cover_url", "TEXT"), ("about", "TEXT")):  # v1.4 / v1.6
        if col not in cols:
            con.execute("ALTER TABLE records ADD COLUMN {} {}".format(col, kind))
            con.commit()
    pcols = {r["name"] for r in con.execute("PRAGMA table_info(plays)")}
    if "log_id" not in pcols:  # groups the sides logged together, added in v1.7
        con.execute("ALTER TABLE plays ADD COLUMN log_id INTEGER")
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


TOP_PERIODS = (("all", "All time", None), ("year", "Past 12 months", 365), ("quarter", "Past 3 months", 91),
               ("month", "Past month", 30))


def period_since(key):
    """The start of a Top 10 period ('all', 'year', 'quarter' or 'month'), or None for all time."""
    days = {k: d for k, _, d in TOP_PERIODS}.get(key)
    return now_local() - dt.timedelta(days=days) if days else None


def top_albums(con, limit=10, since=None):
    """Most played records, most first: by full album plays, then sides played, then time,
    then whichever was played most recently."""
    counts = play_counts(con, since=since)
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
    c = play_counts(con, record_id, since).get(record_id, {"album": 0, "sides": 0, "last": None})
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
    sides_listed = [s["label"] for s in record_sides(con, record_id)]
    fav_side = max(by_side.items(), key=lambda kv: kv[1]) if by_side and len(sides_listed) > 1 else None
    return {"album": c["album"], "sides": c["sides"], "seconds": sum(r["seconds"] for r in rows),
            "listens": len({r["log_id"] for r in rows}), "first": times[0] if times else None,
            "last": times[-1] if times else None, "favourite_side": fav_side,
            "favourite_time": favourite_time(grid), "months": list(zip(months, per_month))}


def fmt_plays(album, sides):
    """'2 album plays (5 sides)'."""
    text = "{} album play{}".format(album, "" if album == 1 else "s")
    if sides:
        text += " ({} side{})".format(sides, "" if sides == 1 else "s")
    return text


def record_sides(con, record_id):
    return con.execute(
        "SELECT * FROM sides WHERE record_id = ? ORDER BY rowid", (record_id,)
    ).fetchall()


def save_record(con, mbid, artist, title, year, fmt, sides, rgid=None, discogs_id=None, cover_url=None,
                tracks=None, about=None):
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
        save_tracks(con, rid, tracks)
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


def album_sample_release(rgid):
    """A representative pressing of an album with its tracks: official vinyl first, then the earliest."""
    data = mb_get("release", {"release-group": rgid, "inc": "recordings+labels", "limit": 10})
    rels = [r for r in data.get("releases", []) if any(m.get("tracks") for m in r.get("media") or [])]
    if not rels:
        return None
    return sorted(rels, key=lambda r: (0 if is_vinyl(r) else 1, pressing_sort_key(r)))[0]


def release_tracklist(rel):
    """(tracks, side lengths) for a MusicBrainz-shaped release."""
    sides = build_sides(rel, allow_prompt=False)
    return tracks_from_sides(sides), {s["label"]: s["seconds"] for s in sides}


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
                "items": [{"position": str(t.get("number") or ""), "seconds": sec,
                           "title": t.get("title") or (t.get("recording") or {}).get("title") or ""}
                          for t, sec in zip(ts, secs)],
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
            print("  Fix later with: styli side <record> {} <mm:ss>".format(s["label"]))
    return sides


# --------------------------------------------------------------------------
# Album art (Cover Art Archive, https://coverartarchive.org)
# --------------------------------------------------------------------------

CAA_ROOT = "https://coverartarchive.org"
MISS_RETRY_DAYS = 7


def cache_dir():
    return cache_base() / "styli" / "covers"


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


def ensure_record_cover(record_id, mbid, rgid, size=500, cover_url=None, artist=None, title=None):
    """Make sure a record has its own saved cover. Returns (path or None, album id looked up or None).

    Uses the pressing's cover if it has one, otherwise the album's main cover. Records
    entered by hand (no MusicBrainz or Discogs link) are matched to an album by name.
    """
    dest = library_cover_path(record_id)
    if dest.exists() and dest.stat().st_size:
        return dest, None
    found, src = None, None
    if not (mbid or rgid or cover_url) and artist and title:
        rgid = found = match_album_cached(artist, title)
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
    con.executemany("DELETE FROM plays WHERE id = ?", [(i,) for i in ids])
    con.commit()


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


# --------------------------------------------------------------------------
# Lifetime charts and the weekly heatmap
# --------------------------------------------------------------------------

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
    if unit == "week":
        return day + dt.timedelta(days=7)
    if unit == "month":
        return (day.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return day + dt.timedelta(days=1)


def auto_unit(first, last):
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


def play_dates(con):
    """Every day that has a play logged on it (for marking them on the calendar)."""
    return {to_dt(r[0]).date() for r in con.execute("SELECT DISTINCT played_at FROM plays")}


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
    return (DATA_DIR or default_db_path().parent) / "cartridges" / "stylus-{}.img".format(sid)


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
        "User-Agent": "Mozilla/5.0 (compatible; Styli/{}; cartridge details lookup)".format(VERSION),
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


DISCOGS_PROBLEM = None  # why the token couldn't be read, if it couldn't


def discogs_account(con):
    """(token, username) for the linked Discogs account, or (None, None)."""
    global DISCOGS_PROBLEM
    user = get_setting(con, "discogs_user")
    if not user:
        return None, None
    try:
        token = get_secret(con, "discogs_token")
        DISCOGS_PROBLEM = None
    except SecretError as e:
        DISCOGS_PROBLEM = "Couldn't read your Discogs token: {}. Unlock it, or link the account again.".format(e)
        return None, None
    return (token, user) if token else (None, None)


def save_discogs_account(con, token, user):
    """Remember a checked token. Returns "keyring" or "file" (where the token was saved)."""
    where = set_secret(con, "discogs_token", token)
    set_setting(con, "discogs_user", user)
    return where


def discogs_link(con, token):
    """Check a personal access token and remember it. Returns the Discogs username."""
    token = token.strip()
    user = discogs_identity(token)
    save_discogs_account(con, token, user)
    return user


def discogs_unlink(con):
    delete_secret(con, "discogs_token")
    con.execute("DELETE FROM settings WHERE key = 'discogs_user'")
    con.commit()


def token_storage_note(con):
    """A sentence on where the Discogs token is kept, for the status line and Preferences."""
    store = secret_store(con, "discogs_token")
    if store == "keyring":
        return "The access token is kept in your system keyring."
    if store == "file":
        return ("The access token is kept in Styli's database, readable only by your user account. "
                "Install the Python keyring package to keep it in your system keyring instead.")
    return ""


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
                            for it, mit in zip(s.get("items") or [], m.get("items") or []):
                                it["seconds"] = it["seconds"] or mit["seconds"]
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
    out["tracks"] = tracks_from_sides(sides)
    out["about"] = about_from_discogs(rel)
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
# Album details: track listing and a short summary
# --------------------------------------------------------------------------

ABOUT_RETRY_DAYS = 30  # look again after this long if nothing was found


def tracks_from_sides(sides):
    """Flatten the tracks kept on each side (by build_sides) into a track listing."""
    out = []
    for s in sides or []:
        for it in s.get("items") or []:
            out.append({"position": it.get("position") or "", "side": s["label"], "title": it.get("title") or "",
                        "seconds": it.get("seconds")})
    return out


def get_tracks(con, record_id):
    return con.execute("SELECT * FROM tracks WHERE record_id = ? ORDER BY n", (record_id,)).fetchall()


def save_tracks(con, record_id, tracks):
    con.execute("DELETE FROM tracks WHERE record_id = ?", (record_id,))
    for n, t in enumerate(tracks or []):
        con.execute("INSERT INTO tracks (record_id, n, position, side, title, seconds) VALUES (?, ?, ?, ?, ?, ?)",
                    (record_id, n, t.get("position") or "", t.get("side") or "", t.get("title") or "",
                     t.get("seconds")))
    con.commit()


def get_about(rec):
    try:
        return json.loads(rec["about"]) if rec["about"] else None
    except (ValueError, TypeError, IndexError, KeyError):
        return None


def set_about(con, record_id, about):
    con.execute("UPDATE records SET about = ? WHERE id = ?", (json.dumps(about), record_id))
    con.commit()


def details_needed(con, rec, token=None):
    """(need tracks, need summary) for a record, given what's already saved."""
    about = get_about(rec)
    stale = about is not None and not about.get("text") and \
        time.time() - about.get("fetched", 0) > ABOUT_RETRY_DAYS * 86400
    can_tracks = bool(rec["mbid"] or (rec["discogs_id"] and token))
    need_tracks = can_tracks and not get_tracks(con, rec["id"]) and not (about or {}).get("tracks_tried")
    return need_tracks, about is None or stale


def clean_discogs_text(text):
    """Discogs notes use [a=Artist], [l=Label], [url=...]...[/url] style markup."""
    text = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", text or "", flags=re.S | re.I)
    text = re.sub(r"\[[almr]=([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[[almr]\d+\]", "", text)
    text = re.sub(r"\[/?[bi]\]", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def about_from_discogs(rel):
    labels = rel.get("labels") or []
    lab = "{} {}".format(labels[0].get("name", ""), labels[0].get("catno", "")).strip() if labels else ""
    notes = clean_discogs_text(rel.get("notes"))
    if len(notes) > 600:
        notes = notes[:600].rsplit(" ", 1)[0] + "..."
    return {"genres": list(dict.fromkeys((rel.get("genres") or []) + (rel.get("styles") or []))),
            "released": rel.get("released_formatted") or rel.get("released") or str(rel.get("year") or ""),
            "label": re.sub(r"\s\(\d+\)", "", lab), "country": rel.get("country") or "", "notes": notes,
            "discogs_url": rel.get("uri") or ""}


def about_from_musicbrainz(mbrel, rgid=None):
    out = {"released": mbrel.get("date") or "", "country": mbrel.get("country") or "",
           "label": release_label(mbrel)}
    rgid = rgid or (mbrel.get("release-group") or {}).get("id")
    if rgid:
        try:
            rg = mb_get("release-group/{}".format(rgid), {"inc": "genres"})
            genres = sorted(rg.get("genres") or [], key=lambda g: -g.get("count", 0))
            out["genres"] = [g["name"].title() for g in genres[:5]]
            out["first_released"] = rg.get("first-release-date") or ""
        except MBError:
            pass
    return out


def lookup_album_wikipedia(artist, title):
    """A short summary of the album from Wikipedia, if it has an article on it."""
    params = {"action": "query", "format": "json", "generator": "search", "gsrlimit": 6,
              "gsrsearch": "{} {} album".format(title, artist), "prop": "extracts|info", "inprop": "url",
              "exintro": 1, "explaintext": 1, "exsentences": 4, "redirects": 1}
    data, _, _ = http_get("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params))
    pages = sorted((json.loads(data.decode("utf-8")).get("query") or {}).get("pages", {}).values(),
                   key=lambda p: p.get("index", 99))
    want = key_tokens(title)
    who = key_tokens(artist) - {"band", "various", "artists"}
    for p in pages:
        name = key_tokens(re.sub(r"\(.*?\)", "", p.get("title") or ""))
        text = (p.get("extract") or "").strip()
        low = text.lower()
        if not want or name != want or not text:
            continue
        if who and not (who & key_tokens(text[:400])):
            continue  # an article with the same name about something else
        first = re.split(r"(?<=[.!?])\s", low, 1)[0]
        is_album = re.search(r"\b(album|ep|soundtrack|lp|mixtape)\b", first)
        other = re.search(r"\b(song|single|film|novel|book|play|musical|tv|television)\b", first)
        if not is_album or (other and other.start() < is_album.start()):
            continue  # e.g. "Dreams is a song by Fleetwood Mac from their album Rumours"
        return {"text": text, "url": p.get("fullurl") or "", "source": "Wikipedia"}
    return None


def fetch_details(rec, token=None, need_tracks=True, need_about=True):
    """Track listing and summary for a record. Safe to run in a background thread (no database use).

    rec is a plain dict with id, artist, title, mbid, rgid and discogs_id. Returns
    {"tracks": list or None, "about": dict or None, "error": message or None}.
    """
    out = {"tracks": None, "about": None, "error": None}
    discogs_rel = mbrel = None
    try:
        if rec.get("discogs_id") and token:
            discogs_rel = discogs_get("/releases/{}".format(int(rec["discogs_id"])), token)
        if rec.get("mbid") and (need_tracks or (need_about and not discogs_rel)):
            mbrel = fetch_release(rec["mbid"])
    except MBError as e:
        out["error"] = str(e)
    if need_tracks:
        if mbrel:
            out["tracks"] = tracks_from_sides(build_sides(mbrel, allow_prompt=False))
        elif discogs_rel:
            out["tracks"] = tracks_from_sides(build_sides(discogs_to_release(discogs_rel), allow_prompt=False))
    if need_about:
        about = {}
        if discogs_rel:
            about.update(about_from_discogs(discogs_rel))
        elif mbrel:
            about.update(about_from_musicbrainz(mbrel, rec.get("rgid")))
        try:
            wiki = lookup_album_wikipedia(rec.get("artist") or "", rec.get("title") or "")
            if wiki:
                about.update(wiki)
        except MBError as e:
            out["error"] = out["error"] or str(e)
        if about or not out["error"]:
            about["fetched"] = time.time()
            about["tracks_tried"] = True
            out["about"] = about
    return out


def store_details(con, record_id, result):
    """Save what fetch_details found (call from the thread that owns the database)."""
    if result.get("tracks"):
        save_tracks(con, record_id, result["tracks"])
    if result.get("about") is not None:
        rec = con.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        merged = dict(get_about(rec) or {})
        merged.update(result["about"])
        set_about(con, record_id, merged)


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
    rid = save_record(con, rel.get("id", mbid), artist, title, year, fmt, sides, rgid,
                      tracks=tracks_from_sides(sides))
    print("Saved as #{}. Log a play with: styli play {} [side]".format(rid, rid))
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
        err("link your Discogs account first: styli discogs link <token>\n"
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
                       info["rgid"], info["discogs_id"], info["cover_url"], info["tracks"], info["about"])
    print("Saved as #{}. Log a play with: styli play {} [side]".format(rid2, rid2))
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
            err('give the artist and album, e.g. styli add --artist "fleetwood mac" --title rumours')
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
            err("no cartridge / stylus matches '{}'. See `styli stylus list`.".format(args.stylus))
            return 1
    stylus, total = log_play(con, rec, entries, when, args.times, chosen_stylus)
    what = ", ".join("side {} ({})".format(s, fmt_dur(sec)) for s, sec in entries)
    times = " x{}".format(args.times) if args.times > 1 else ""
    print("Logged {}{} of {} - {}: {}".format(what, times, rec["artist"], rec["title"], fmt_dur(total)))
    if stylus:
        print(stylus_alert(con, stylus))
    else:
        print("No cartridge / stylus set up, so this play is not counted against one. Add one with `styli stylus add`.")
    return 0


def cmd_status(con, args):
    in_use = styli_in_use(con)
    cur = active_stylus(con)
    if not in_use:
        print("No cartridge / stylus in use. Add one with: styli stylus add \"Name\" --hours <rated life>")
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


def cmd_list(con, args):
    rows = con.execute("SELECT * FROM records ORDER BY artist, title").fetchall()
    if args.filter:
        words = " ".join(args.filter).lower().split()
        rows = [r for r in rows if all(w in "{} {}".format(r["artist"], r["title"]).lower() for w in words)]
    if not rows:
        print("No records yet. Add one with: styli add \"artist album\"")
        return 0
    counts = play_counts(con)
    for r in rows:
        sides = record_sides(con, r["id"])
        c = counts.get(r["id"], {"album": 0, "sides": 0, "last": None})
        side_txt = "  ".join("{} {}{}".format(s["label"], fmt_dur(s["seconds"]), "*" if s["missing"] else "")
                             for s in sides)
        last = ", last {}".format(to_dt(c["last"]).date()) if c["last"] else ""
        print("#{:<4} {} - {}{}".format(r["id"], r["artist"], r["title"], " ({})".format(r["year"]) if r["year"] else ""))
        print("      {}   [{}{}]".format(side_txt, fmt_plays(c["album"], c["sides"]), last))
    if any(s["missing"] for r in rows for s in record_sides(con, r["id"])):
        print("\n* some track lengths were missing on MusicBrainz; fix with `styli side`.")
    return 0


def cmd_top(con, args):
    since = period_since(args.period)
    top = top_albums(con, args.n, since)
    name = {k: n for k, n, _ in TOP_PERIODS}[args.period]
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
    s = find_stylus(con, " ".join(args.stylus)) if args.stylus else active_stylus(con)
    if not s:
        err("no cartridge / stylus found." if args.stylus else "no cartridge / stylus is on the turntable.")
        return 1
    t = stylus_timeline(con, s, args.by)
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
    grid = play_heatmap(con, s["id"])
    fav = favourite_time(grid)
    if fav:
        print("You play most on {} {}s ({} sides).".format(fav[0], fav[1], fav[2]))
    most = max(c[0] for row in grid for c in row) or 1
    shades = " .:-=+*#%@"
    print("\nWhen you play (sides, by day and hour):")
    print("       " + "".join(str(h // 10) if h % 3 == 0 else " " for h in range(24)))
    print("       " + "".join(str(h % 10) if h % 3 == 0 else " " for h in range(24)))
    for d, row in enumerate(grid):
        line = "".join(shades[int(round((len(shades) - 1) * c[0] / most))] if c[0] else "\u00b7" for c in row)
        print("  {}  {}  {}".format(WEEKDAYS[d][:3], line, sum(c[0] for c in row)))
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


def _latest_logged(con, n):
    """The n most recently logged plays (by when they were logged, not when they were played)."""
    log = play_log(con, limit=100000)
    return sorted(log, key=lambda g: -max(g["ids"]))[:n]


def cmd_log(con, args):
    log = play_log(con, limit=args.n)
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
    if args.number:
        log = play_log(con, limit=args.number)
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
    remove_plays(con, [i for g in chosen for i in g["ids"]])
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
            print("Your other cartridges / styli stay in use. Retire a replaced one with: styli stylus retire <number>")
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
        print("No cartridges / styli yet. Add one with: styli stylus add \"Name\" --hours <rated life>")
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
        print("Wikipedia has no article on '{}'. Try a product page: styli stylus lookup {} --url <link>".format(
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
        print("The page gives a stylus life of {:g} h. Set it with: styli stylus edit {} --hours {:g}".format(
            hours, s["id"], hours))
    return 0


def match_album_cached(artist, title):
    """match_album, remembering for a week when nothing matched (so it isn't searched every time)."""
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    tag = hashlib.sha1("{}\n{}".format(artist.lower(), title.lower()).encode("utf-8")).hexdigest()[:16]
    miss = d / "match-{}.none".format(tag)
    if miss.exists() and time.time() - miss.stat().st_mtime < MISS_RETRY_DAYS * 86400:
        return None
    found = match_album(artist, title)
    if not found:
        miss.touch()
    return found


def match_album(artist, title):
    """The MusicBrainz album ID for an artist and album title, if one matches the title exactly."""
    want = key_tokens(title)
    for g in search_albums(artist=artist, title=title)[:10]:
        if key_tokens(g.get("title")) == want:
            return g.get("id")
    return None


def cmd_covers(con, args):
    rows = [r for r in con.execute("SELECT * FROM records ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE")
            if not library_cover_path(r["id"]).exists()]
    print("Styli {}: looking for covers for {} record{} (MusicBrainz allows about one search a second).".format(
        VERSION, len(rows), "" if len(rows) == 1 else "s"))
    saved, missing = 0, []
    for n, r in enumerate(rows, 1):
        label = "{} - {}".format(r["artist"], r["title"])
        try:
            path, rgid = ensure_record_cover(r["id"], r["mbid"], r["rgid"], cover_url=r["cover_url"],
                                             artist=r["artist"], title=r["title"])
        except MBError as e:
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
        saved, "" if saved == 1 else "s", len(missing), library_cover_path(0).parent))
    if missing:
        print("You can choose your own image for any of these with Change cover in the window.")
    return 0


def cmd_discogs(con, args):
    act = args.discogs_cmd or "status"
    if act == "link":
        try:
            user = discogs_link(con, args.token)
        except MBError as e:
            err(str(e))
            return 1
        print("Linked to Discogs as {}. Add records from your collection with: styli add --discogs [words]".format(user))
        print(token_storage_note(con))
        return 0
    if act == "unlink":
        discogs_unlink(con)
        print("Discogs account unlinked. The access token has been removed from this computer.")
        return 0
    token, user = discogs_account(con)
    if not token:
        if DISCOGS_PROBLEM:
            err(DISCOGS_PROBLEM)
            return 1
        print("Not linked. Create a personal access token at {}\nthen run: styli discogs link <token>".format(
            DISCOGS_TOKEN_PAGE))
        return 0 if act == "status" else 1
    if act == "status":
        items, age = load_discogs_cache(user)
        print("Linked to Discogs as {}.{}".format(user, " {} items in the local copy of your collection.".format(
            len(items)) if items is not None else ""))
        print(token_storage_note(con))
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
    print("{} of {} items. Add one with: styli add --discogs-id <number>".format(shown, len(items)))
    return 0


def cmd_info(con, args):
    rec = resolve_record(con, args.record)
    if not rec:
        return 1
    token, _ = discogs_account(con)
    need_tracks, need_about = details_needed(con, rec, token)
    if args.refresh:
        need_tracks, need_about = bool(rec["mbid"] or (rec["discogs_id"] and token)), True
    if need_tracks or need_about:
        print("Getting album details...", file=sys.stderr)
        result = fetch_details(dict(rec), token, need_tracks, need_about)
        if result["error"]:
            err(result["error"])
        store_details(con, rec["id"], result)
        rec = con.execute("SELECT * FROM records WHERE id = ?", (rec["id"],)).fetchone()
    about = get_about(rec) or {}
    print("{} - {}".format(rec["artist"], rec["title"]))
    facts = [x for x in (", ".join(about.get("genres") or []), "Released " + about["released"]
                         if about.get("released") else "", about.get("label")) if x]
    if facts:
        print("  " + "  |  ".join(facts))
    c = play_counts(con, rec["id"]).get(rec["id"])
    print("  " + (fmt_plays(c["album"], c["sides"]) if c else "Not played yet"))
    if about.get("text"):
        print()
        for line in about["text"].split("\n"):
            print("  " + line)
        if about.get("url"):
            print("  ({})".format(about["url"]))
    if about.get("notes"):
        print("\n  Pressing notes: " + about["notes"])
    tracks = get_tracks(con, rec["id"])
    lengths = {s["label"]: s["seconds"] for s in record_sides(con, rec["id"])}
    side = None
    for t in tracks:
        if t["side"] != side:
            side = t["side"]
            print("\n  Side {}  ({})".format(side, fmt_dur(lengths.get(side, 0))))
        print("    {:<5} {:<50} {}".format(t["position"], t["title"][:50], fmt_dur(t["seconds"]) if t["seconds"] else ""))
    if not tracks:
        print("\n  No track listing saved for this record.")
    return 0


def cmd_gui(con, args):
    """Open the PyQt6 window, which lives in styli_gui.py next to this file."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        import styli_gui
    except ImportError as e:
        if (getattr(e, "name", "") or "").startswith("PyQt6") or "PyQt6" in str(e):
            err("PyQt6 is not installed. Install it with your package manager, e.g.\n"
                "  Debian/Ubuntu/Mint: sudo apt install python3-pyqt6\n"
                "  Fedora:             sudo dnf install python3-pyqt6\n"
                "  Arch/Manjaro:       sudo pacman -S python-pyqt6\n"
                "  Others:             pip install PyQt6")
        else:
            err("styli_gui.py needs to be in the same folder as styli.py ({}).".format(here))
        return 1
    return styli_gui.run(con, sys.modules[__name__])


ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)


def install_icon():
    """Put styli.png (kept next to this script) into the user's icon theme, at the sizes
    Linux desktops look for. Returns the icon name to use, or None if there's no icon file."""
    src = Path(__file__).resolve().parent / "styli.png"
    if not src.exists():
        return None
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "icons" / "hicolor"
    image = None
    try:  # PyQt6 makes neat smaller copies; without it the desktop scales the large one itself
        from PyQt6.QtGui import QImage
        from PyQt6.QtCore import Qt
        image = QImage(str(src))
        if image.isNull():
            image = None
    except Exception:
        image = None
    for size in ICON_SIZES:
        folder = base / "{0}x{0}".format(size) / "apps"
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / "styli.png"
        if size == 512 or image is None:
            if size == 512:
                shutil.copyfile(str(src), str(out))
            continue
        image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation).save(str(out), "PNG")
    try:
        os.utime(str(base))  # tells icon caches the theme folder has changed
    except OSError:
        pass
    if shutil.which("gtk-update-icon-cache") and (base / "index.theme").exists():
        subprocess.run(["gtk-update-icon-cache", "-f", "-t", str(base)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return "styli"


def cmd_desktop(con, args):
    if not sys.platform.startswith("linux"):
        err("desktop menu entries are a Linux feature.")
        return 1
    apps = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    old = apps / (OLD_NAME + ".desktop")  # menu entry from before the rename
    if old.exists():
        old.unlink()
    target = apps / "styli.desktop"
    env = ""
    if CONTACT:
        env = 'env STYLI_CONTACT="{}" '.format(CONTACT.replace('"', ""))
    exec_line = '{}"{}" "{}" --db "{}" gui'.format(env, sys.executable, Path(__file__).resolve(), args.db)
    icon = install_icon() or "media-optical"
    target.write_text(
        "[Desktop Entry]\nType=Application\nName=Styli\nGenericName=Record play and stylus tracker\n"
        "Comment=Log record plays and track cartridge / stylus wear\n"
        "Exec={}\nIcon={}\nTerminal=false\nCategories=AudioVideo;Audio;Utility;\n".format(exec_line, icon),
        encoding="utf-8")
    print("Added Styli to your applications menu ({}).".format(target))
    return 0


# --------------------------------------------------------------------------
# Command-line interface
# --------------------------------------------------------------------------

def build_parser():
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
    p.add_argument("--db", default=str(default_db_path()), help="database file (default: %(default)s)")
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
    tp.add_argument("--period", choices=[k for k, _, _ in TOP_PERIODS], default="all",
                    help="all time (default), or the past year, quarter (3 months) or month")

    stt = sub.add_parser("stats", help="lifetime chart and weekly heatmap for a cartridge / stylus")
    stt.add_argument("stylus", nargs="*", help="cartridge / stylus by number or name (default: the one on the turntable)")
    stt.add_argument("--by", choices=CHART_UNITS, help="group plays by day, week or month (default: picked from the lifetime)")

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
    "add": cmd_add, "play": cmd_play, "status": cmd_status, "list": cmd_list,
    "history": cmd_history, "stats": cmd_stats, "top": cmd_top, "undo": cmd_undo, "log": cmd_log, "side": cmd_side, "remove": cmd_remove,
    "export": cmd_export, "stylus": cmd_stylus, "gui": cmd_gui, "covers": cmd_covers, "info": cmd_info, "discogs": cmd_discogs, "cartridge": cmd_stylus, "desktop-entry": cmd_desktop,
}


def main(argv=None):
    global ASSUME_YES
    args = build_parser().parse_args(argv)
    if args.db == str(default_db_path()):
        try:
            moved = migrate_old_library()
        except OSError as e:
            moved = None
            err("couldn't move your library from its old folder ({}). It's still there, untouched.".format(e))
        if moved:
            print(moved, file=sys.stderr)
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
    try:
        sys.exit(main())
    except BrokenPipeError:  # output piped into something like `head` that stopped reading
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
