"""MusicBrainz (https://musicbrainz.org): finding albums and pressings, and turning a release's
track list into sides with running times.

Searches are album first: find the album (a "release group"), then pick the pressing (a
"release") you own, since pressings can differ. MusicBrainz asks clients to make at most one
request a second and to say who they are (set STYLI_CONTACT); every request goes through
mb_get, which does both."""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from styli_app import details, web
from styli_app.common import (USER_AGENT, ask, fmt_dur, interactive, key_tokens, norm_tokens, parse_duration,
                              side_letter)


MB_ROOT = "https://musicbrainz.org/ws/2"

_last_call = [0.0]

_mb_lock = threading.Lock()  # keeps background lookups to one request per second

_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')

LETTER_RE = re.compile(r"^\s*([A-Za-z]+)")


def mb_get(path, params):
    """Ask the MusicBrainz web service for something (one request at a time, across all threads)."""
    with _mb_lock:
        return _mb_get(path, params)


def _mb_get(path, params):
    # MusicBrainz allows roughly one request per second per client.
    """Do one MusicBrainz request, keeping to its one-request-a-second limit and retrying when it's busy."""
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
                raise web.MBError("MusicBrainz has no release with that ID")
            raise web.MBError("MusicBrainz returned HTTP {}".format(e.code))
        except urllib.error.URLError as e:
            raise web.MBError("could not reach MusicBrainz ({})".format(e.reason))
    raise web.MBError("MusicBrainz is busy, try again shortly")


def lucene_phrase(s):
    """Quote text so MusicBrainz's search treats it as one exact phrase."""
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
    """One release (pressing) with its tracks, artists, labels and album id."""
    return mb_get("release/{}".format(mbid), {"inc": "recordings+artist-credits+labels+release-groups"})


TYPE_RANK = {"Album": 0, "EP": 1, "Single": 2}

PRESSING_CAP = 500


def album_artist(g):
    """The credited artist of a MusicBrainz album as one line, e.g. 'Simon & Garfunkel'."""
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
    """Keep albums really credited to the artist asked for, best match first: exact title, studio albums before
    compilations and live albums, then the earliest."""
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
    """One line describing an album for the command line: artist, title, year and type."""
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
    return details.tracks_from_sides(sides), {s["label"]: s["seconds"] for s in sides}


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
    """True if any disc of a release is vinyl."""
    return any("vinyl" in (m.get("format") or "").lower() for m in r.get("media") or [])


def pressing_sort_key(r):
    """Sort order for pressings: official ones first, then by date and country."""
    official = (r.get("status") or "Official") == "Official"
    return (0 if official else 1, r.get("date") or "9999", r.get("country") or "")


def pressing_notes(r, album_title=None):
    """Anything unusual about a pressing worth showing (bootleg, different title, MusicBrainz's note)."""
    notes = []
    if r.get("status") and r["status"] != "Official":
        notes.append(r["status"])
    if album_title and r.get("title") and r["title"] != album_title:
        notes.append(r["title"])
    if r.get("disambiguation"):
        notes.append(r["disambiguation"])
    return "; ".join(notes)


def describe_pressing(r, album_title=None):
    """One line describing a pressing: date, country, format, label and barcode."""
    bits = [r.get("date") or "no date", r.get("country") or "", media_summary(r.get("media")),
            release_label(r), "barcode " + r["barcode"] if r.get("barcode") else "",
            pressing_notes(r, album_title)]
    return "  ".join(b for b in bits if b)


def artist_credit_str(credits):
    """Join a MusicBrainz artist credit into one line, e.g. 'David Bowie & Queen'."""
    out = ""
    for c in credits or []:
        name = c.get("name") or (c.get("artist") or {}).get("name", "")
        out += name + c.get("joinphrase", "")
    return out or "Unknown artist"


def media_summary(media):
    """The discs of a release in short, e.g. '2x12" Vinyl'."""
    counts = {}
    for m in media or []:
        f = m.get("format") or "Unknown format"
        counts[f] = counts.get(f, 0) + 1
    return " + ".join(("{}x{}".format(n, f) if n > 1 else f) for f, n in counts.items())


def describe_release(r):
    """Two lines describing a release for the command line (title line, then format and label)."""
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
    """A track's length in seconds, from the track or its recording, or None if unknown."""
    ms = t.get("length") or (t.get("recording") or {}).get("length")
    return int(round(ms / 1000)) if ms else None


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
    """At the terminal, ask where side two starts on a disc listed without side letters."""
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
    """Split a release's tracks into sides with running times. Uses the A1/B1 positions when they're there,
    otherwise splits each disc in two at the most even point."""
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
    """Warn about sides with tracks of unknown length, and at a terminal let you type the real length."""
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


def release_label(r):
    """The label and catalogue number of a release, e.g. 'Warner Bros. BSK 3010'."""
    for li in r.get("label-info") or []:
        text = "{} {}".format((li.get("label") or {}).get("name", ""), li.get("catalog-number") or "").strip()
        if text:
            return text
    return ""


def release_has_side_letters(release):
    """True if every track position starts with a side letter (A1, B2...)."""
    for m in release.get("media") or []:
        for t in m.get("tracks") or []:
            if not LETTER_RE.match(str(t.get("number") or "")):
                return False
    return True
