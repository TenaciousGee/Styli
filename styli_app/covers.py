"""Album art from the Cover Art Archive (https://coverartarchive.org) or Discogs.

Each library record's cover is saved beside the database (covers/record-N.img), so it works
offline for good; covers seen while searching go in a cache that can be cleared. Records entered
by hand are matched to a MusicBrainz album by artist and title. A cover (or match) that isn't
available is remembered for a week, so it isn't asked for again and again."""

import hashlib
import os
import shutil
import threading
import time
import urllib.error
import urllib.request

from styli_app import musicbrainz, paths, web
from styli_app.common import USER_AGENT, key_tokens


CAA_ROOT = "https://coverartarchive.org"

MISS_RETRY_DAYS = 7


def cache_dir():
    """Where downloaded cover previews are kept (can be cleared at any time)."""
    return paths.cache_base() / "styli" / "covers"


def library_cover_path(record_id):
    """Each record's own cover, kept with the library so it works offline for good.

    It is saved the first time the cover is fetched, or replaced when you choose
    your own image.
    """
    return (paths.DATA_DIR or paths.default_db_path().parent) / "covers" / "record-{}.img".format(record_id)


custom_cover_path = library_cover_path  # older name


def remove_record_cover(record_id):
    """Delete a record's saved cover (when the record is removed or replaced)."""
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
    for g in musicbrainz.search_albums(artist=artist, title=title)[:10]:
        if key_tokens(g.get("title")) == want:
            return g.get("id")
    return None


def search_cache_size():
    """Bytes used by the cover preview cache, for Preferences."""
    d = cache_dir()
    return sum(f.stat().st_size for f in d.glob("*") if f.is_file()) if d.exists() else 0


def clear_search_cache():
    """Empty the cover preview cache. Covers saved with your library are kept."""
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
        rel = musicbrainz.mb_get("release/{}".format(mbid), {"inc": "release-groups"})
    except web.MBError:
        return None
    return (rel.get("release-group") or {}).get("id")


def fetch_image_cached(url):
    """Any image by URL (Discogs covers), cached on disk. Returns a path or None."""
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / "url-{}.img".format(hashlib.sha1(url.encode("utf-8")).hexdigest())
    if path.exists() and path.stat().st_size:
        return path
    try:
        data, ctype, _ = web.http_get(url)
    except web.MBError:
        return None
    if not data:
        return None
    tmp = path.with_name(path.name + ".{}.tmp".format(threading.get_ident()))
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))
    return path
