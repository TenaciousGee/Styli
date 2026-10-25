"""Album details: what's shown under "About this album".

Track listings come from MusicBrainz or Discogs; summaries from Discogs or MusicBrainz, plus
Wikipedia's article on the album when it has one. fetch_details runs in the background and never
touches the database; store_details saves what it found. When nothing is found it tries again
after ABOUT_RETRY_DAYS."""

import json
import re
import time
import urllib.parse

from styli_app import discogs, musicbrainz, web
from styli_app.common import key_tokens


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
    """A record's saved track listing, in order."""
    return con.execute("SELECT * FROM tracks WHERE record_id = ? ORDER BY n", (record_id,)).fetchall()


def save_tracks(con, record_id, tracks):
    """Replace a record's saved track listing."""
    con.execute("DELETE FROM tracks WHERE record_id = ?", (record_id,))
    for n, t in enumerate(tracks or []):
        con.execute("INSERT INTO tracks (record_id, n, position, side, title, seconds) VALUES (?, ?, ?, ?, ?, ?)",
                    (record_id, n, t.get("position") or "", t.get("side") or "", t.get("title") or "",
                     t.get("seconds")))
    con.commit()


def get_about(rec):
    """A record's saved summary (genres, release details, Wikipedia extract), or None."""
    try:
        return json.loads(rec["about"]) if rec["about"] else None
    except (ValueError, TypeError, IndexError, KeyError):
        return None


def set_about(con, record_id, about):
    """Save a record's summary."""
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
    """Summary details from a Discogs release: genres and styles, release date, label, country and the pressing
    notes."""
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
    """Summary details from MusicBrainz: release date, country, label, and the album's genres and first release
    date."""
    out = {"released": mbrel.get("date") or "", "country": mbrel.get("country") or "",
           "label": musicbrainz.release_label(mbrel)}
    rgid = rgid or (mbrel.get("release-group") or {}).get("id")
    if rgid:
        try:
            rg = musicbrainz.mb_get("release-group/{}".format(rgid), {"inc": "genres"})
            genres = sorted(rg.get("genres") or [], key=lambda g: -g.get("count", 0))
            out["genres"] = [g["name"].title() for g in genres[:5]]
            out["first_released"] = rg.get("first-release-date") or ""
        except web.MBError:
            pass
    return out


def lookup_album_wikipedia(artist, title):
    """A short summary of the album from Wikipedia, if it has an article on it."""
    params = {"action": "query", "format": "json", "generator": "search", "gsrlimit": 6,
              "gsrsearch": "{} {} album".format(title, artist), "prop": "extracts|info", "inprop": "url",
              "exintro": 1, "explaintext": 1, "exsentences": 4, "redirects": 1}
    data, _, _ = web.http_get("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params))
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
            discogs_rel = discogs.discogs_get("/releases/{}".format(int(rec["discogs_id"])), token)
        if rec.get("mbid") and (need_tracks or (need_about and not discogs_rel)):
            mbrel = musicbrainz.fetch_release(rec["mbid"])
    except web.MBError as e:
        out["error"] = str(e)
    if need_tracks:
        if mbrel:
            out["tracks"] = tracks_from_sides(musicbrainz.build_sides(mbrel, allow_prompt=False))
        elif discogs_rel:
            out["tracks"] = tracks_from_sides(musicbrainz.build_sides(discogs.discogs_to_release(discogs_rel), allow_prompt=False))
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
        except web.MBError as e:
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
