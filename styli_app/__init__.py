"""Styli: log vinyl plays by side and track cartridge / stylus hours.

Record and side running times are fetched automatically from MusicBrainz (https://musicbrainz.org),
which lists vinyl track positions such as A1, A2, B1 so each side can be timed separately.

The engine and the command line need only the Python standard library; the window (the gui
sub-package) also needs PyQt6. Runs on Linux, macOS and Windows.

How the package is organised:

  common            the version, dates and durations, comparing names, terminal prompts
  paths             where the library and caches are kept
  privacy           private folders, and secrets in the system keyring
  db                the SQLite database: schema, connecting, settings
  library           records and their sides
  plays             logging plays, the play log, play counts, CSV export
  stats             lifetime charts, the weekly heatmap, Top 10
  cartridges        cartridges / styli: in use, wear, photos and specifications
  cartridge_lookup  photos and specifications from Wikipedia and product pages
  web               fetching pages, and the error every lookup raises
  musicbrainz       finding albums and pressings, timing sides
  covers            album art
  discogs           your Discogs collection as a source of records
  details           album summaries and track listings
  cli/              the `styli` command: options, main(), and the commands by area
  gui/              the window: theme, widgets, charts, one module per tab, dialogs

Licence: MIT. Share freely.
"""

from styli_app.common import VERSION

__version__ = VERSION
