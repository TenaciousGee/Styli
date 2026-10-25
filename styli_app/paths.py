"""Where things are kept: the library folder and the cache folder on each system, and moving a
library across from the app's earlier name (vinyltrack).

DATA_DIR is the folder of the library in use, set by db.connect(); covers and cartridge photos
are saved inside it."""

import os
import shutil
import sys
from pathlib import Path


from styli_app.common import OLD_NAME


DATA_DIR = None  # folder holding the database; set by connect()


def app_root():
    """The folder Styli is installed in: the launcher (styli.py) and its icon (styli.png) are here,
    beside the styli_app package."""
    return Path(__file__).resolve().parent.parent


def data_base():
    """The folder where each operating system keeps app data (Styli's library lives inside it)."""
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))


def cache_base():
    """The folder where each operating system keeps disposable caches (cover previews, search results)."""
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches"
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))


def default_db_path():
    """Where the library database lives, unless STYLI_DB or --db says otherwise."""
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
