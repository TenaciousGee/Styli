"""Discogs (https://www.discogs.com/developers): your collection as a source of records.

Linking your account with a personal access token, reading your collection and turning a
Discogs release into sides. Discogs allows 60 requests a minute, so discogs_get keeps to about
one a second, and the collection is saved locally and refreshed when it's 12 hours old.
Discogs track lists often lack lengths, so missing ones are filled in from the MusicBrainz
release linked to it, or one with the same barcode.

Discogs' API terms ask for "Data provided by Discogs." beside any Discogs data shown, linked
to its page on discogs.com: see DISCOGS_CREDIT and the functions after it."""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from styli_app import covers, db, details, musicbrainz, privacy, web
from styli_app.common import USER_AGENT, parse_duration


DISCOGS_API = "https://api.discogs.com"

DISCOGS_TOKEN_PAGE = "https://www.discogs.com/settings/developers"

# Discogs' API terms ask for these words, linked to the page the data came from, next to any
# Discogs data shown (https://support.discogs.com/hc/en-us/articles/360009334593).
DISCOGS_CREDIT = "Data provided by Discogs."


def discogs_release_url(discogs_id, about=None):
    """The discogs.com page of a release, for the credit beside its details."""
    return (about or {}).get("discogs_url") or "https://www.discogs.com/release/{}".format(int(discogs_id))


def discogs_collection_url(user):
    """The discogs.com page of someone's collection, for the credit beside it."""
    return "https://www.discogs.com/user/{}/collection".format(urllib.parse.quote(user or ""))


def discogs_credit_line(url):
    """The credit for the command line: the required words, then the page they refer to."""
    return "{} {}".format(DISCOGS_CREDIT, url)


DISCOGS_STALE_HOURS = 12

_dg_last = [0.0]

_dg_lock = threading.Lock()  # Discogs allows 60 requests a minute with a token


class DiscogsError(web.MBError):
    """A Discogs request failed. Its message says why."""
    pass


def discogs_get(path, token, params=None):
    """Ask the Discogs API for something with your access token, keeping to its rate limit."""
    with _dg_lock:
        wait = 1.05 - (time.monotonic() - _dg_last[0])
        if wait > 0:
            time.sleep(wait)
        url = DISCOGS_API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                                   "Authorization": "Discogs token={}".format(token)})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    _dg_last[0] = time.monotonic()
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                _dg_last[0] = time.monotonic()
                if e.code == 429 and attempt < 2:
                    time.sleep(6 * (attempt + 1))
                    continue
                if e.code == 401:
                    raise DiscogsError("Discogs didn't accept the access token. Check it, or create a new one.")
                if e.code == 403:
                    raise DiscogsError("Discogs says this collection is private to its owner.")
                if e.code == 404:
                    raise DiscogsError("Discogs couldn't find that.")
                raise DiscogsError("Discogs returned HTTP {}".format(e.code))
            except urllib.error.URLError as e:
                raise DiscogsError("couldn't reach Discogs ({})".format(e.reason))
        raise DiscogsError("Discogs is busy, try again shortly")


def discogs_identity(token):
    """The Discogs username a personal access token belongs to (also checks the token works)."""
    return discogs_get("/oauth/identity", token)["username"]


DISCOGS_PROBLEM = None  # why the token couldn't be read, if it couldn't


def discogs_account(con):
    """(token, username) for the linked Discogs account, or (None, None)."""
    global DISCOGS_PROBLEM
    user = db.get_setting(con, "discogs_user")
    if not user:
        return None, None
    try:
        token = privacy.get_secret(con, "discogs_token")
        DISCOGS_PROBLEM = None
    except privacy.SecretError as e:
        DISCOGS_PROBLEM = "Couldn't read your Discogs token: {}. Unlock it, or link the account again.".format(e)
        return None, None
    return (token, user) if token else (None, None)


def save_discogs_account(con, token, user):
    """Remember a checked token. Returns "keyring" or "file" (where the token was saved)."""
    where = privacy.set_secret(con, "discogs_token", token)
    db.set_setting(con, "discogs_user", user)
    return where


def discogs_link(con, token):
    """Check a personal access token and remember it. Returns the Discogs username."""
    token = token.strip()
    user = discogs_identity(token)
    save_discogs_account(con, token, user)
    return user


def discogs_unlink(con):
    """Forget the linked Discogs account and remove its token from the keyring and database."""
    privacy.delete_secret(con, "discogs_token")
    con.execute("DELETE FROM settings WHERE key = 'discogs_user'")
    con.commit()


def token_storage_note(con):
    """A sentence on where the Discogs token is kept, for the status line and Preferences."""
    store = privacy.secret_store(con, "discogs_token")
    if store == "keyring":
        return "The access token is kept in your system keyring."
    if store == "file":
        return ("The access token is kept in Styli's database, readable only by your user account. "
                "Install the Python keyring package to keep it in your system keyring instead.")
    return ""


def discogs_collection_page(token, user, page=1):
    """One page (100 items) of the user's whole collection. Returns (items, pages, total)."""
    d = discogs_get("/users/{}/collection/folders/0/releases".format(urllib.parse.quote(user)), token,
                    {"page": page, "per_page": 100, "sort": "artist", "sort_order": "asc"})
    pag = d.get("pagination") or {}
    return d.get("releases", []), pag.get("pages", 1), pag.get("items", 0)


def _discogs_cache_path(user):
    """Where the local copy of someone's Discogs collection is saved."""
    return covers.cache_dir().parent / "discogs-{}.json".format(re.sub(r"\W", "_", user))


def load_discogs_cache(user):
    """(items, age in hours) from the last download, or (None, None)."""
    path = _discogs_cache_path(user)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["items"], (time.time() - data["fetched"]) / 3600
    except (OSError, ValueError, KeyError):
        return None, None


def save_discogs_cache(user, items):
    """Save a downloaded Discogs collection so it opens instantly next time."""
    path = _discogs_cache_path(user)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fetched": time.time(), "items": items}), encoding="utf-8")


def discogs_collection(token, user, refresh=False, progress=None):
    """The whole collection, from the local copy unless it's stale or refresh is asked for."""
    items, age = load_discogs_cache(user)
    if items is not None and not refresh and age < DISCOGS_STALE_HOURS:
        return items
    items, page, pages = [], 1, 1
    while page <= pages:
        batch, pages, total = discogs_collection_page(token, user, page)
        items.extend(batch)
        if progress:
            progress(len(items), total)
        page += 1
    save_discogs_cache(user, items)
    return items


def _clean_artist(name):
    """An artist name without the (2), (3) Discogs adds to tell namesakes apart."""
    return re.sub(r"\s\(\d+\)$", "", name or "")  # Discogs adds (2), (3) to tell namesakes apart


def discogs_artists(artists):
    """Join Discogs artist credits into one line, as printed on the sleeve."""
    artists = artists or []
    out = ""
    for i, a in enumerate(artists):
        out += _clean_artist(a.get("anv") or a.get("name"))
        join = (a.get("join") or "").strip()
        if i < len(artists) - 1:
            out += ", " if join in ("", ",") else " {} ".format(join)
    return out or "Unknown artist"


def discogs_formats(formats):
    """Describe Discogs formats in short, e.g. '2xVinyl, LP, Album'."""
    parts = []
    for f in formats or []:
        qty = str(f.get("qty") or "1")
        bits = ["{}x{}".format(qty, f.get("name")) if qty not in ("", "1") else (f.get("name") or "")]
        bits += [d for d in f.get("descriptions") or [] if d not in bits]
        parts.append(", ".join(b for b in bits if b))
    return "; ".join(parts)


def discogs_item_info(item):
    """Plain summary of a collection item."""
    bi = item.get("basic_information") or {}
    labels = bi.get("labels") or []
    lab = "{} {}".format(labels[0].get("name", ""), labels[0].get("catno", "")).strip() if labels else ""
    return {"id": bi.get("id"), "artist": discogs_artists(bi.get("artists")), "title": bi.get("title") or "",
            "year": str(bi.get("year") or "") if bi.get("year") else "", "format": discogs_formats(bi.get("formats")),
            "label": lab, "cover": bi.get("cover_image") or bi.get("thumb") or None,
            "vinyl": any((f.get("name") or "").lower() == "vinyl" for f in bi.get("formats") or []),
            "added": (item.get("date_added") or "")[:10]}


def describe_discogs_item(item):
    """One line describing a collection item for the command line."""
    i = discogs_item_info(item)
    return "{} - {}{}  [{}]{}".format(i["artist"], i["title"], " ({})".format(i["year"]) if i["year"] else "",
                                       i["format"], "  " + i["label"] if i["label"] else "")


def _duration_ms(text):
    """A Discogs track duration ('4:31') in milliseconds, or None."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return parse_duration(text) * 1000
    except ValueError:
        return None


def discogs_to_release(rel):
    """A Discogs release reshaped like a MusicBrainz one, so build_sides can split it into sides."""
    tracks = []

    def add(t):
        pos = (t.get("position") or "").strip()
        if pos:
            tracks.append({"number": pos, "title": t.get("title", ""), "length": _duration_ms(t.get("duration"))})

    for t in rel.get("tracklist") or []:
        kind = t.get("type_") or "track"
        if kind == "heading":
            continue
        if kind == "index" and t.get("sub_tracks") and not (t.get("position") and t.get("duration")):
            for st in t["sub_tracks"]:
                add(st)
        else:
            add(t)
    return {"id": None, "title": rel.get("title"), "date": str(rel.get("year") or ""),
            "artist-credit": [{"name": discogs_artists(rel.get("artists")), "joinphrase": ""}],
            "media": [{"format": discogs_formats(rel.get("formats")), "tracks": tracks}]}


def mb_release_for_discogs(discogs_id):
    """The MusicBrainz release linked to this exact Discogs release, if anyone has linked them."""
    try:
        data = musicbrainz.mb_get("url", {"resource": "https://www.discogs.com/release/{}".format(discogs_id),
                              "inc": "release-rels"})
    except web.MBError:
        return None
    for rel in data.get("relations") or []:
        if rel.get("release"):
            return rel["release"]["id"]
    return None


def discogs_barcode(rel):
    """A release's barcode from its Discogs identifiers, if it has a usable one."""
    for ident in rel.get("identifiers") or []:
        if (ident.get("type") or "").lower() == "barcode":
            digits = re.sub(r"\D", "", ident.get("value") or "")
            if len(digits) >= 8:
                return digits
    return None


def prepare_discogs_release(token, discogs_id):
    """Everything needed to add a Discogs release: details, sides and cover.

    Discogs track lists often lack lengths, so missing ones are filled from MusicBrainz:
    first the release MusicBrainz links to this Discogs entry, then a barcode match.
    """
    rel = discogs_get("/releases/{}".format(int(discogs_id)), token)
    shaped = discogs_to_release(rel)
    sides = musicbrainz.build_sides(shaped, allow_prompt=False)
    out = {"discogs_id": int(discogs_id), "artist": discogs_artists(rel.get("artists")), "title": rel.get("title"),
           "year": str(rel.get("year") or "") if rel.get("year") else "", "format": discogs_formats(rel.get("formats")),
           "mbid": None, "rgid": None, "notes": [], "estimated": not musicbrainz.release_has_side_letters(shaped)}
    images = rel.get("images") or []
    primary = [i for i in images if i.get("type") == "primary"] or images
    out["cover_url"] = (primary[0].get("uri") or primary[0].get("uri150")) if primary else None
    need = not sides or any(s["missing"] for s in sides)
    if need:
        mb_id, how = mb_release_for_discogs(discogs_id), "linked to this Discogs release"
        if not mb_id and discogs_barcode(rel):
            try:
                found, _ = musicbrainz.search_releases(barcode=discogs_barcode(rel), vinyl=False, limit=10)
            except web.MBError:
                found = []
            vinyl = [r for r in found if musicbrainz.is_vinyl(r)]
            mb_id, how = ((vinyl or found)[0]["id"] if found else None), "matched by barcode"
        if mb_id:
            try:
                mbrel = musicbrainz.fetch_release(mb_id)
            except web.MBError:
                mbrel = None
            mb_sides = musicbrainz.build_sides(mbrel, allow_prompt=False) if mbrel else []
            if mb_sides:
                if not sides:
                    sides, out["estimated"] = mb_sides, not musicbrainz.release_has_side_letters(mbrel)
                else:
                    by_label = {s["label"]: s for s in mb_sides}
                    for s in sides:
                        m = by_label.get(s["label"])
                        if s["missing"] and m and not m["missing"]:
                            s["seconds"], s["missing"] = m["seconds"], 0
                            for it, mit in zip(s.get("items") or [], m.get("items") or []):
                                it["seconds"] = it["seconds"] or mit["seconds"]
                out["mbid"] = mbrel.get("id")
                out["rgid"] = (mbrel.get("release-group") or {}).get("id")
                out["notes"].append("Track lengths missing on Discogs were taken from MusicBrainz ({}).".format(how))
    if any(s["missing"] for s in sides):
        out["notes"].append("Some track lengths are on neither Discogs nor MusicBrainz, so check the sides "
                            "marked below against the sleeve or label.")
    if out["estimated"] and sides:
        out["notes"].append("The track list doesn't mark sides (A1, B1...), so the split between sides "
                            "has been estimated.")
    out["sides"] = sides
    out["tracks"] = details.tracks_from_sides(sides)
    out["about"] = details.about_from_discogs(rel)
    return out
