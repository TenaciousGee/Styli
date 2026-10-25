"""Setting things up: linking Discogs, opening the window, and the Linux menu entry and icon."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


from styli_app import discogs, paths, web
from styli_app.common import CONTACT, OLD_NAME, err


def cmd_discogs(con, args):
    """styli discogs: link, unlink or check your Discogs account, or list your collection."""
    act = args.discogs_cmd or "status"
    if act == "link":
        try:
            user = discogs.discogs_link(con, args.token)
        except web.MBError as e:
            err(str(e))
            return 1
        print("Linked to Discogs as {}. Add records from your collection with: styli add --discogs [words]".format(user))
        print(discogs.token_storage_note(con))
        return 0
    if act == "unlink":
        discogs.discogs_unlink(con)
        print("Discogs account unlinked. The access token has been removed from this computer.")
        return 0
    token, user = discogs.discogs_account(con)
    if not token:
        if discogs.DISCOGS_PROBLEM:
            err(discogs.DISCOGS_PROBLEM)
            return 1
        print("Not linked. Create a personal access token at {}\nthen run: styli discogs link <token>".format(
            discogs.DISCOGS_TOKEN_PAGE))
        return 0 if act == "status" else 1
    if act == "status":
        items, age = discogs.load_discogs_cache(user)
        print("Linked to Discogs as {}.{}".format(user, " {} items in the local copy of your collection.".format(
            len(items)) if items is not None else ""))
        print(discogs.token_storage_note(con))
        return 0
    try:
        items = discogs.discogs_collection(token, user, refresh=args.refresh,
                                   progress=lambda n, t: print("  read {} of {}".format(n, t)))
    except web.MBError as e:
        err(str(e))
        return 1
    have = {r[0] for r in con.execute("SELECT discogs_id FROM records WHERE discogs_id IS NOT NULL")}
    words = " ".join(args.filter or []).lower().split()
    shown = 0
    for it in items:
        text = discogs.describe_discogs_item(it)
        if all(w in text.lower() for w in words):
            i = discogs.discogs_item_info(it)
            print("{:>10}  {}{}".format(i["id"], text, "  [in library]" if i["id"] in have else ""))
            shown += 1
    print("{} of {} items. Add one with: styli add --discogs-id <number>".format(shown, len(items)))
    print(discogs.discogs_credit_line(discogs.discogs_collection_url(user)))
    return 0


def cmd_gui(con, args):
    """styli gui: open the window (the styli_app.gui package, which needs PyQt6)."""
    try:
        from styli_app.gui import app
    except ImportError as e:
        if (getattr(e, "name", "") or "").startswith("PyQt6") or "PyQt6" in str(e):
            err("PyQt6 is not installed. Install it with your package manager, e.g.\n"
                "  Debian/Ubuntu/Mint: sudo apt install python3-pyqt6\n"
                "  Fedora:             sudo dnf install python3-pyqt6\n"
                "  Arch/Manjaro:       sudo pacman -S python-pyqt6\n"
                "  Others:             pip install PyQt6")
        else:
            err("couldn't open the window ({}). Is the styli_app folder complete?".format(e))
        return 1
    return app.run(con)


ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)


def install_icon():
    """Put styli.png (kept next to this script) into the user's icon theme, at the sizes
    Linux desktops look for. Returns the icon name to use, or None if there's no icon file."""
    src = paths.app_root() / "styli.png"
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
    """styli desktop-entry: add Styli, with its icon, to the Linux applications menu."""
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
    exec_line = '{}"{}" "{}" --db "{}" gui'.format(env, sys.executable, paths.app_root() / "styli.py", args.db)
    icon = install_icon() or "media-optical"
    target.write_text(
        "[Desktop Entry]\nType=Application\nName=Styli\nGenericName=Record play and stylus tracker\n"
        "Comment=Log record plays and track cartridge / stylus wear\n"
        "Exec={}\nIcon={}\nTerminal=false\nCategories=AudioVideo;Audio;Utility;\n".format(exec_line, icon),
        encoding="utf-8")
    print("Added Styli to your applications menu ({}).".format(target))
    return 0
