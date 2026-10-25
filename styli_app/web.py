"""Fetching web pages and images, and the error every lookup raises when it fails.

MBError is named for MusicBrainz, where it started, but means "a lookup failed" for any site:
its message says what went wrong."""

import urllib.error
import urllib.request

from styli_app.common import VERSION


class MBError(Exception):
    """A lookup failed: offline, refused, or the site didn't have what was asked for. The message says which."""
    pass


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
