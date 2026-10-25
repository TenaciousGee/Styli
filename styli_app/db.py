"""The library database: one SQLite file holding records and their sides, track listings, every
side played, cartridges / styli, and settings.

connect() creates it and quietly adds anything newer versions need, so older libraries keep
working."""

import sqlite3
from pathlib import Path


from styli_app import paths, privacy


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
    """Open (or create) the library database, bring older libraries up to date and lock down its folders."""
    path = Path(path)
    privacy.private_files()
    path.parent.mkdir(parents=True, exist_ok=True)
    paths.DATA_DIR = path.parent
    old_photos = paths.DATA_DIR / "styli"  # cartridge photos' folder before the rename
    if old_photos.is_dir() and any(old_photos.glob("stylus-*.img")) and not (paths.DATA_DIR / "cartridges").exists():
        old_photos.rename(paths.DATA_DIR / "cartridges")
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA secure_delete = ON")  # deleted data is overwritten, not left behind in the file
    privacy.secure_folders(path)
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
    """One saved preference from the settings table, or default if it isn't set."""
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(con, key, value):
    """Save one preference to the settings table (adding or replacing it)."""
    con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
    con.commit()
