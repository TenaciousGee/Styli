#!/usr/bin/env python3
"""Styli: log vinyl plays by side and track cartridge / stylus hours.

This starts Styli: `styli gui` (or `python3 styli.py gui`) opens the window, and
`styli --help` lists the commands. The program itself is the styli_app folder next to
this file; see styli_app/__init__.py for a guide to how it's organised.

Data is kept in a single SQLite file:
  Linux    $XDG_DATA_HOME/styli/styli.db (default ~/.local/share/...)
  macOS    ~/Library/Application Support/styli/styli.db
  Windows  %APPDATA%\\styli\\styli.db
Override with the STYLI_DB environment variable or --db.

Licence: MIT. Share freely.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # find styli_app, even when started from a link

from styli_app.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
