"""Privacy: folders only you can read, and secrets kept in the system keyring.

On Linux and Mac, Styli's folders and files are made readable by your user account only
(Windows already keeps each person's AppData private).

Secrets (the Discogs access token) go in the system keyring through the optional keyring
package: GNOME Keyring or KWallet on Linux, Keychain on a Mac, Credential Manager on Windows.
Without a keyring they're kept in the database, which is private to you. Each library has its
own keyring entry, so a copied database never carries the token with it."""

import hashlib
import os
from pathlib import Path


from styli_app import db, paths


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
    if folder == paths.default_db_path().parent or folder.name.lower() == "styli":
        make_private(folder)  # only a folder that's Styli's own, never e.g. Downloads
    for suffix in ("", "-wal", "-shm", "-journal"):
        f = Path(str(db_path) + suffix)
        if f.exists():
            make_private(f)
    for sub in (folder / "covers", folder / "cartridges", paths.cache_base() / "styli"):
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
    """The system keyring has a saved secret but couldn't be read (locked, refused or missing)."""
    pass


def _secret_name(con, name):
    """Each library has its own entry, so a copy of the database on another computer (or the demo
    library) never picks up your token."""
    lib = db.get_setting(con, "library_id")
    if not lib:
        lib = hashlib.sha1(os.urandom(16)).hexdigest()[:12]
        db.set_setting(con, "library_id", lib)
    return "{}-{}".format(name, lib)


def secret_store(con, name):
    """Where a secret is kept: "keyring", "file" or None (not saved)."""
    return db.get_setting(con, name + "_store") or ("file" if db.get_setting(con, name) else None)


def _forget_in_database(con, name):
    """Remove a secret from the settings table, overwriting it rather than leaving it in free space."""
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
            db.set_setting(con, name + "_store", "keyring")
            return "keyring"
        except Exception:
            pass  # a locked or refused keyring: fall back rather than lose the token
    db.set_setting(con, name, value)
    db.set_setting(con, name + "_store", "file")
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
    value = db.get_setting(con, name)
    if value and system_keyring():
        set_secret(con, name, value)  # upgrade: out of the database and into the keyring
    return value


def delete_secret(con, name):
    """Forget a secret wherever it's kept: the system keyring and the database."""
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
