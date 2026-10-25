"""Small helpers used everywhere: the version, dates and durations, comparing names, and
asking questions at the terminal.

Play times are stored as ISO text with the local time zone (e.g. 2026-10-18T20:30:00+01:00),
so they read back exactly as they were logged."""

import datetime as dt
import os
import re
import sys
import unicodedata


VERSION = "1.11.0"

# MusicBrainz asks every client to send a meaningful User-Agent with contact
# details. Set STYLI_CONTACT to your email or a project URL.
APP_NAME = "Styli"

OLD_NAME = "vinyltrack"  # the app's earlier name, used to find and move an existing library

CONTACT = os.environ.get("STYLI_CONTACT") or os.environ.get("VINYLTRACK_CONTACT") or ""

USER_AGENT = "Styli/{} ( {} )".format(VERSION, CONTACT or "personal record-play and stylus-hours tracker"
)

ASSUME_YES = False


def now_local():
    """The current time in the local time zone, to the second (how play times are stored)."""
    return dt.datetime.now().astimezone().replace(microsecond=0)


def to_dt(iso):
    """Turn a stored ISO timestamp back into a datetime, assuming local time if it has no zone."""
    d = dt.datetime.fromisoformat(iso)
    return d if d.tzinfo else d.astimezone()


def fmt_dur(sec):
    """Seconds as m:ss, or h:mm:ss for an hour or more."""
    sec = int(round(sec or 0))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return "{}:{:02d}:{:02d}".format(h, m, s) if h else "{}:{:02d}".format(m, s)


def fmt_hours(sec):
    """Seconds as hours to one decimal place, e.g. '12.3 h'."""
    return "{:.1f} h".format((sec or 0) / 3600)


def parse_duration(text):
    """Accepts '21:34', '1:02:10' or plain minutes such as '21.5'."""
    text = text.strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return int(round(float(text) * 60))
    parts = text.split(":")
    if not all(p.isdigit() for p in parts) or len(parts) not in (2, 3):
        raise ValueError("Use mm:ss, h:mm:ss or minutes, e.g. 21:34")
    nums = [int(p) for p in parts]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def parse_when(text):
    """'2026-10-11 20:30', '2026-10-11', 'yesterday 21:00', 'today 9:15'."""
    if not text:
        return now_local()
    t = text.strip().lower()
    for word, offset in (("yesterday", 1), ("today", 0)):
        if t.startswith(word):
            day = dt.date.today() - dt.timedelta(days=offset)
            rest = t[len(word):].strip()
            clock = dt.time.fromisoformat(rest.zfill(5)) if rest else dt.time(12, 0)
            return dt.datetime.combine(day, clock).astimezone().replace(microsecond=0)
    try:
        d = dt.datetime.fromisoformat(text.strip())
    except ValueError:
        raise ValueError("Could not read date/time '{}'. Try 2026-10-11 20:30".format(text))
    return (d if d.tzinfo else d.astimezone()).replace(microsecond=0)


def interactive():
    """True when there's a person at the terminal to answer questions (and --yes wasn't given)."""
    return sys.stdin.isatty() and not ASSUME_YES


def ask(prompt, default=""):
    """Ask a question at the terminal; Enter (or no terminal) gives the default."""
    try:
        ans = input(prompt).strip()
    except EOFError:
        ans = ""
    return ans or default


def confirm(prompt, default=True):
    """Ask a yes/no question. Without a terminal, --yes means yes, otherwise the default is used."""
    if not interactive():
        return True if ASSUME_YES else default
    hint = "[Y/n]" if default else "[y/N]"
    ans = ask("{} {} ".format(prompt, hint)).lower()
    if not ans:
        return default
    return ans.startswith("y")


def err(msg):
    """Print an error message for the command line."""
    print("Error: " + msg, file=sys.stderr)


def fmt_plays(album, sides):
    """'2 album plays (5 sides)'."""
    text = "{} album play{}".format(album, "" if album == 1 else "s")
    if sides:
        text += " ({} side{})".format(sides, "" if sides == 1 else "s")
    return text


SEARCH_STOPWORDS = {"the", "a", "an", "and"}


def norm_tokens(text):
    """Lower-case words with accents and punctuation removed; '&' counts as 'and'."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    return re.findall(r"[a-z0-9]+", text.lower().replace("&", " and "))


def key_tokens(text):
    """The meaningful words of some text ('the', 'a', 'an' and 'and' left out), for comparing names."""
    return {w for w in norm_tokens(text) if w not in SEARCH_STOPWORDS}


def side_letter(i):
    """The letter for the nth side: 0 is A, 1 is B, and so on."""
    return chr(65 + i) if i < 26 else "S{}".format(i + 1)
