"""The stylus (or cartridge) command: list, add, use, retire, edit, show, lookup and delete."""

from styli_app import cartridge_lookup, cartridges, web
from styli_app.common import err, fmt_hours, parse_when


# ---- cartridges / styli ------------------------------------------------------------------------------

def cmd_stylus(con, args):
    """styli stylus (or cartridge): list, add, put on, retire, edit, show, look up or delete cartridges / styli."""
    act = args.stylus_cmd or "list"
    if act == "add":
        try:
            installed = parse_when(args.installed) if args.installed else None
        except ValueError as e:
            err(str(e))
            return 1
        old = cartridges.find_stylus(con, args.replaces) if args.replaces else None
        if args.replaces and not old:
            err("no cartridge / stylus matches '{}'.".format(args.replaces))
            return 1
        sid = cartridges.stylus_add(con, args.name, args.hours, args.used, args.warn, installed, old["id"] if old else None)
        print("Cartridge / stylus #{} '{}' is on the turntable (rated {:g} h, warning at {:g}%).".format(
            sid, args.name, args.hours, args.warn))
        if old:
            print("Retired #{} '{}'; its {} of plays are kept.".format(old["id"], old["name"],
                                                                     fmt_hours(cartridges.stylus_used_seconds(con, old))))
        elif len(cartridges.styli_in_use(con)) > 1:
            print("Your other cartridges / styli stay in use. Retire a replaced one with: styli stylus retire <number>")
        return 0
    if act in ("use", "retire", "edit", "show", "lookup", "delete"):
        row = cartridges.find_stylus(con, args.id)
        if not row:
            err("no cartridge / stylus matches '{}'.".format(args.id))
            return 1
        sid = row["id"]
        if act == "use":
            cartridges.stylus_use(con, sid)
            print("Cartridge / stylus #{} '{}' is now on the turntable.".format(sid, row["name"]))
        elif act == "retire":
            cartridges.stylus_retire(con, sid)
            print("Cartridge / stylus #{} retired after {}. Its plays are kept.".format(sid, fmt_hours(cartridges.stylus_used_seconds(con, row))))
        elif act == "delete":
            if not cartridges.stylus_delete(con, sid):
                err("plays were logged with this cartridge / stylus, so it can only be retired.")
                return 1
            print("Cartridge / stylus #{} deleted.".format(sid))
        elif act == "edit":
            cartridges.stylus_edit(con, sid, args.name, args.hours, args.warn, args.used)
            if args.url:
                cartridges.set_stylus_details(con, sid, url=args.url)
            print("Cartridge / stylus #{} updated.".format(sid))
        elif act == "show":
            return show_stylus(con, row)
        else:
            return lookup_stylus(con, row, args.url)
        return 0
    rows = con.execute("SELECT * FROM styli ORDER BY active DESC, id").fetchall()
    if not rows:
        print("No cartridges / styli yet. Add one with: styli stylus add \"Name\" --hours <rated life>")
    cur = cartridges.active_stylus(con)
    for s in rows:
        m = cartridges.stylus_stats(con, s)
        state = ("ON TURNTABLE" if cur and s["id"] == cur["id"] else "in use") if s["active"] else \
            "retired {}".format(m["retired"].date() if m["retired"] else "")
        print("#{:<3} {:<28} {:>9} / {:g} h ({:5.1f}%)  {:>4} sides  fitted {}  {}".format(
            s["id"], s["name"][:28], fmt_hours(m["used"]), s["rated_hours"], m["pct"], m["sides_played"],
            m["installed"].date(), state))
    return 0


def show_stylus(con, s):
    """styli stylus show: everything about one cartridge / stylus, with its hours by month."""
    m = cartridges.stylus_stats(con, s)
    print("#{}  {}".format(s["id"], s["name"]))
    print("  {:<19} {}".format("Status", "in use" if m["in_use"] else "retired {}".format(
        m["retired"].date() if m["retired"] else "")))
    print("  {:<19} {} of {:g} h ({:.1f}%) over {} sides of {} records".format(
        "Used", fmt_hours(m["used"]), s["rated_hours"], m["pct"], m["sides_played"], m["records"]))
    print("  {:<19} {}".format("Fitted", m["installed"].date()))
    if m["first"]:
        print("  {:<19} {} to {}".format("Played", m["first"].date(), m["last"].date()))
    specs = cartridges.get_specs(s)
    for key, label, _ in cartridges.SPEC_FIELDS:
        if specs.get(key):
            print("  {:<19} {}".format(label, specs[key]))
    if specs.get("description"):
        print("  {}".format(specs["description"]))
    if s["url"]:
        print("  {:<19} {}".format("Details", s["url"]))
    months = cartridges.stylus_hours_by_month(con, s["id"])
    if months:
        print("  Hours by month:")
        for month, secs in months[-12:]:
            print("    {}  {:>6}  {}".format(month, fmt_hours(secs), "#" * max(1, int(secs / 3600))))
    return 0


def lookup_stylus(con, s, url=None):
    """styli stylus lookup: fetch a photo and specifications from Wikipedia or a product page."""
    try:
        found = cartridge_lookup.scrape_product_page(url) if url else cartridge_lookup.lookup_wikipedia(s["name"])
    except web.MBError as e:
        err(str(e))
        return 1
    if not found:
        print("Wikipedia has no article on '{}'. Try a product page: styli stylus lookup {} --url <link>".format(
            s["name"], s["id"]))
        return 1
    print("Found on {}: {}".format(found["source"], found["title"]))
    if found.get("description"):
        print("  " + found["description"][:300])
    specs = dict(found["specs"])
    if found.get("description"):
        specs["description"] = found["description"]
    for key, label, _ in cartridges.SPEC_FIELDS:
        if specs.get(key):
            print("  {:<18} {}".format(label, specs[key]))
    cartridges.set_stylus_details(con, s["id"], specs, found.get("url"))
    if found.get("image_url"):
        try:
            data, ctype, _ = web.http_get(found["image_url"])
            if ctype.startswith("image/") or data[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1"):
                cartridges.save_stylus_photo(s["id"], data)
                print("Photo saved.")
        except web.MBError:
            print("Couldn't download the photo.")
    hours = cartridges.suggest_hours(specs)
    if hours:
        print("The page gives a stylus life of {:g} h. Set it with: styli stylus edit {} --hours {:g}".format(
            hours, s["id"], hours))
    return 0
