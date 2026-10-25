"""Cartridges / styli: several can be in use at once; one is "on the turntable".

Plays are logged against the one on the turntable unless another is chosen. Retired ones keep
their plays and hours for the record. Also here: wear (hours used and left, warning levels, an
estimated end date), and each one's saved photo and specifications."""

import datetime as dt
import json
import os
import re

from styli_app import db, paths
from styli_app.common import fmt_hours, now_local, to_dt


RECENT_WINDOW_DAYS = 56  # window used to estimate listening rate in `status`


def active_stylus(con):
    """The cartridge / stylus on the turntable now: plays are logged against it unless another is chosen."""
    sid = db.get_setting(con, "current_stylus")
    if sid and sid.isdigit():
        row = con.execute("SELECT * FROM styli WHERE id = ? AND active = 1", (int(sid),)).fetchone()
        if row:
            return row
    return con.execute("SELECT * FROM styli WHERE active = 1 ORDER BY id DESC").fetchone()


def stylus_used_seconds(con, s):
    """A cartridge / stylus's total playing time: hours it had before plus every play logged with it."""
    row = con.execute("SELECT COALESCE(SUM(seconds), 0) FROM plays WHERE stylus_id = ?", (s["id"],)).fetchone()
    return s["initial_seconds"] + row[0]


def stylus_bar(pct, width=30):
    """A text progress bar for the command line, e.g. [######----]."""
    filled = min(width, int(round(width * pct / 100)))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def stylus_alert(con, s):
    """A line about a cartridge / stylus's wear, with a warning past its warning level or rated life."""
    used = stylus_used_seconds(con, s)
    rated = s["rated_hours"] * 3600
    pct = 100 * used / rated if rated else 0
    line = "Cartridge / stylus '{}': {} of {:g} h ({:.1f}%)".format(s["name"], fmt_hours(used), s["rated_hours"], pct)
    if pct >= 100:
        line += "\n  !! Rated life reached. Inspect or replace the cartridge / stylus."
    elif pct >= s["warn_pct"]:
        line += "\n  ! Past {:g}% of rated life, about {} left.".format(s["warn_pct"], fmt_hours(rated - used))
    return line


def stylus_summary(con, s):
    """Wear figures for a cartridge / stylus: hours used and left, percentage, recent rate per week, an estimated
    date it reaches its rated life, and a level (ok, warn or over)."""
    used = stylus_used_seconds(con, s)
    rated = s["rated_hours"] * 3600
    pct = 100 * used / rated if rated else 0
    installed = to_dt(s["installed_at"])
    now = now_local()
    start = max(installed, now - dt.timedelta(days=RECENT_WINDOW_DAYS))
    recent = con.execute(
        "SELECT COALESCE(SUM(seconds), 0) FROM plays WHERE stylus_id = ? AND played_at >= ?",
        (s["id"], start.isoformat())).fetchone()[0]
    days = max(1.0, (now - start).total_seconds() / 86400)
    per_day = recent / days
    eta = now + dt.timedelta(days=(rated - used) / per_day) if per_day > 0 and used < rated else None
    level = "over" if pct >= 100 else ("warn" if pct >= s["warn_pct"] else "ok")
    sides = con.execute("SELECT COUNT(*) FROM plays WHERE stylus_id = ?", (s["id"],)).fetchone()[0]
    return {"used": used, "rated": rated, "pct": pct, "remaining": max(0, rated - used),
            "per_week_h": per_day * 7 / 3600, "eta": eta, "level": level,
            "installed": installed, "sides_played": sides}


def stylus_add(con, name, hours, used_h=0, warn=80, installed=None, replaces=None):
    """Fit a cartridge / stylus. Others stay in use unless it replaces one, which is then retired."""
    if replaces:  # the old one came off when this one went on
        stylus_retire(con, replaces, installed)
    cur = con.execute(
        "INSERT INTO styli (name, rated_hours, initial_seconds, warn_pct, installed_at, active) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (name, hours, int(round(used_h * 3600)), warn, (installed or now_local()).isoformat()))
    con.commit()
    set_current_stylus(con, cur.lastrowid)
    return cur.lastrowid


def stylus_use(con, sid):
    """Put a cartridge / stylus on the turntable (bringing it back into use if it was retired)."""
    con.execute("UPDATE styli SET active = 1, retired_at = NULL WHERE id = ?", (sid,))
    con.commit()
    set_current_stylus(con, sid)


def stylus_retire(con, sid, when=None):
    """Retire a worn or replaced cartridge / stylus. Its hours and plays are kept."""
    con.execute("UPDATE styli SET active = 0, retired_at = ? WHERE id = ?", ((when or now_local()).isoformat(), sid))
    con.commit()
    if db.get_setting(con, "current_stylus") == str(sid):
        nxt = con.execute("SELECT id FROM styli WHERE active = 1 ORDER BY id DESC").fetchone()
        set_current_stylus(con, nxt[0] if nxt else None)


def stylus_edit(con, sid, name=None, hours=None, warn=None, used_h=None, installed=None):
    """Change a cartridge / stylus's details; anything not given stays as it was."""
    row = con.execute("SELECT * FROM styli WHERE id = ?", (sid,)).fetchone()
    if not row:
        return False
    con.execute(
        "UPDATE styli SET name = ?, rated_hours = ?, warn_pct = ?, initial_seconds = ?, installed_at = ? "
        "WHERE id = ?", (
            name or row["name"],
            hours if hours is not None else row["rated_hours"],
            warn if warn is not None else row["warn_pct"],
            int(round(used_h * 3600)) if used_h is not None else row["initial_seconds"],
            installed.isoformat() if installed else row["installed_at"],
            sid))
    con.commit()
    return True


def styli_in_use(con):
    """Cartridges / styli that are fitted and in use, in a fixed order (the order they were added)."""
    return con.execute("SELECT * FROM styli WHERE active = 1 ORDER BY id").fetchall()


def set_current_stylus(con, sid):
    """Remember which cartridge / stylus is on the turntable."""
    db.set_setting(con, "current_stylus", str(sid) if sid else "")


def stylus_stats(con, s):
    """Everything worth showing about one cartridge / stylus, retired or not."""
    m = stylus_summary(con, s)
    row = con.execute(
        "SELECT MIN(played_at), MAX(played_at), COUNT(DISTINCT record_label) FROM plays WHERE stylus_id = ?",
        (s["id"],)).fetchone()
    m.update(first=to_dt(row[0]) if row[0] else None, last=to_dt(row[1]) if row[1] else None,
             records=row[2], retired=to_dt(s["retired_at"]) if s["retired_at"] else None,
             in_use=bool(s["active"]))
    return m


def stylus_hours_by_month(con, sid):
    """(YYYY-MM, seconds) for each month a cartridge / stylus was played."""
    rows = con.execute("SELECT substr(played_at, 1, 7) m, SUM(seconds) FROM plays WHERE stylus_id = ? "
                       "GROUP BY m ORDER BY m", (sid,)).fetchall()
    return [(r[0], r[1]) for r in rows]


def find_stylus(con, ref):
    """A cartridge / stylus by number or by words from its name (in-use ones preferred)."""
    ref = str(ref).strip()
    if ref.isdigit():
        return con.execute("SELECT * FROM styli WHERE id = ?", (int(ref),)).fetchone()
    words = ref.lower().split()
    rows = con.execute("SELECT * FROM styli ORDER BY active DESC, id DESC").fetchall()
    for r in rows:
        if all(w in r["name"].lower() for w in words):
            return r
    return None


def stylus_delete(con, sid):
    """Only for cartridges / styli added by mistake: refuses if any plays were logged with it."""
    if con.execute("SELECT 1 FROM plays WHERE stylus_id = ? LIMIT 1", (sid,)).fetchone():
        return False
    con.execute("DELETE FROM styli WHERE id = ?", (sid,))
    con.commit()
    try:
        stylus_photo_path(sid).unlink()
    except OSError:
        pass
    if db.get_setting(con, "current_stylus") == str(sid):
        nxt = con.execute("SELECT id FROM styli WHERE active = 1 ORDER BY id DESC").fetchone()
        set_current_stylus(con, nxt[0] if nxt else None)
    return True


SPEC_FIELDS = [
    ("type", "Type", r"^(cartridge |generator |transducer )?(type|principle|system|generator)\b"),
    ("stylus", "Stylus profile", r"^(stylus|diamond|tip)( type| shape| profile| tip| cut)?\b(?!.*(force|pressure|life))"),
    ("output", "Output", r"^output( voltage| level)?\b"),
    ("tracking", "Tracking force", r"tracking force|^vtf|stylus pressure|tracking weight|downforce"),
    ("compliance", "Compliance", r"compliance"),
    ("frequency", "Frequency response", r"frequency (response|range)"),
    ("separation", "Channel separation", r"channel separation|^separation"),
    ("load", "Load", r"load (impedance|resistance|capacitance)|recommended load|^load\b"),
    ("weight", "Weight", r"^(cartridge |body )?(weight|mass)\b"),
    ("life", "Stylus life", r"stylus life|life ?span|lifetime|life expectancy|playing life"),
]


def stylus_photo_path(sid):
    """Where a cartridge / stylus's photo is saved, beside the library."""
    return (paths.DATA_DIR or paths.default_db_path().parent) / "cartridges" / "stylus-{}.img".format(sid)


def save_stylus_photo(sid, data):
    """Save a cartridge / stylus photo (replacing any earlier one safely)."""
    path = stylus_photo_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))
    return path


def get_specs(s):
    """A cartridge / stylus's saved specifications as a dictionary."""
    try:
        return json.loads(s["specs"] or "{}")
    except (ValueError, TypeError, IndexError, KeyError):
        return {}


def set_stylus_details(con, sid, specs=None, url=None):
    """Merge new specifications (and a product link) into a cartridge / stylus's saved ones. An empty value removes
    that specification."""
    row = con.execute("SELECT * FROM styli WHERE id = ?", (sid,)).fetchone()
    merged = get_specs(row)
    for k, v in (specs or {}).items():
        if v:
            merged[k] = v
        elif k in merged and v == "":
            del merged[k]
    con.execute("UPDATE styli SET specs = ?, url = COALESCE(?, url) WHERE id = ?",
                (json.dumps(merged), url, sid))
    con.commit()


def suggest_hours(specs):
    """A rated life in hours if the specs mention one (the lower figure of a range)."""
    m = re.search(r"(\d[\d,.]*)\s*(?:-|to|–)?\s*(?:\d[\d,.]*)?\s*h(?:ou)?rs?\b", (specs or {}).get("life", ""), re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None
